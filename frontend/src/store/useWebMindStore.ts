import { create } from "zustand";
import { API_BASE, apiGet, apiPost } from "../api";
import { startMockAgentStream, type MockAgentStream } from "../mock/mockAgentBackend";
import type { AgentProfile, AgentStep, MemoryItem, MockAgentEvent, RunMetrics, TaskStatus } from "../types";

export const DEMO_TASK = "Find the price of the cheapest USB-C hub on example-store.com";
export type WebMindPage = "agent" | "memory" | "metrics";

interface AskUserRequest {
  prompt: string;
  is_login?: boolean;
  can_take_over?: boolean;
  requires_approval?: boolean;
  step_number?: number;
}

interface WebMindState {
  page: WebMindPage;
  query: string;
  status: TaskStatus;
  tier: string;
  currentStep: number;
  taskId: string | null;
  profile: AgentProfile | null;
  steps: AgentStep[];
  memory: MemoryItem[];
  metrics: RunMetrics;
  finalOutput: string;
  errorMessage: string;
  askUserRequest: AskUserRequest | null;
  userReply: string;
  setPage: (page: WebMindPage) => void;
  setQuery: (query: string) => void;
  setUserReply: (value: string) => void;
  startTask: () => Promise<void>;
  stopTask: () => Promise<void>;
  resumeTask: (approved?: boolean) => Promise<void>;
  receiveEvent: (event: MockAgentEvent) => void;
  receiveBackendEvent: (eventName: string, data: Record<string, any>) => void;
}

let activeSource: EventSource | null = null;
let activeMock: MockAgentStream | null = null;
let elapsedTimer: ReturnType<typeof setInterval> | null = null;
let taskStartedAt = 0;
let stopAfterCreate = false;

const emptyMetrics: RunMetrics = {
  totalSteps: 0,
  successfulSteps: 0,
  failedSteps: 0,
  elapsedSeconds: 0,
  outcome: "Not started",
};

function closeRunConnections() {
  activeSource?.close();
  activeSource = null;
  activeMock?.stop();
  activeMock = null;
  if (elapsedTimer) clearInterval(elapsedTimer);
  elapsedTimer = null;
}

function prettyOutput(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined || value === null) return "The task ended without a result.";
  return JSON.stringify(value, null, 2);
}

function tierLabel(tier?: string): string | undefined {
  if (!tier) return undefined;
  return ({ FAST: "Fast", GENERAL: "Balanced", REASONING: "Deep" } as Record<string, string>)[tier] || tier;
}

function actionLabel(raw: unknown): string {
  if (typeof raw === "string") return raw;
  if (!raw || typeof raw !== "object") return "Agent performed a browser action";
  const action = raw as Record<string, unknown>;
  const kind = String(action.action || action.type || "action");
  const id = action.element_id ? ` element ${action.element_id}` : "";
  const url = typeof action.url === "string" ? action.url : "";
  switch (kind) {
    case "goto": return `Opened ${url || "a webpage"}`;
    case "new_tab": return `Opened a new browser tab${url ? `: ${url}` : ""}`;
    case "click": return `Clicked${id}`;
    case "type": return `Filled a text field${id}`;
    case "select": return `Selected a form option${id}`;
    case "extract": return "Read requested information from the page";
    case "finish": return "Prepared the final result";
    case "scroll": return `Scrolled ${String(action.direction || "the page")}`;
    case "press": return `Pressed ${String(action.key || "a key")}`;
    case "wait": return "Waited for the page to update";
    default: return `${kind.charAt(0).toUpperCase()}${kind.slice(1)}${id}`;
  }
}

function mergeStep(steps: AgentStep[], next: AgentStep): AgentStep[] {
  const index = steps.findIndex((step) => step.id === next.id);
  if (index < 0) return [...steps, next];
  const updated = [...steps];
  updated[index] = { ...updated[index], ...next };
  return updated;
}

function upsertMemory(memory: MemoryItem[], key: string, value: string): MemoryItem[] {
  const index = memory.findIndex((item) => item.key === key);
  if (index < 0) return [...memory, { key, value }].slice(-30);
  const updated = [...memory];
  updated[index] = { key, value };
  return updated;
}

function countMetrics(steps: AgentStep[], elapsedSeconds: number, outcome: string): RunMetrics {
  return {
    totalSteps: steps.length,
    successfulSteps: steps.filter((step) => step.result === "success").length,
    failedSteps: steps.filter((step) => step.result === "failed").length,
    elapsedSeconds,
    outcome,
  };
}

function getElapsedSeconds() {
  return taskStartedAt ? Math.max(0, Math.floor((Date.now() - taskStartedAt) / 1000)) : 0;
}

function terminalStatus(status: TaskStatus, output: string, errorMessage = "") {
  const state = useWebMindStore.getState();
  const elapsed = getElapsedSeconds();
  closeRunConnections();
  useWebMindStore.setState({
    status,
    finalOutput: output,
    errorMessage,
    askUserRequest: null,
    metrics: countMetrics(state.steps, elapsed, status === "completed" ? "Completed" : status === "stopped" ? "Stopped" : "Failed"),
  });
}

async function hydrateTask(taskId: string) {
  try {
    const task = await apiGet<Record<string, any>>(`/api/tasks/${taskId}`);
    const state = useWebMindStore.getState();
    if (task.profile) {
      const target = tierLabel(task.current_tier || task.profile.routed_tier);
      if (target) useWebMindStore.setState({ profile: task.profile, tier: target });
    }
    if (task.status === "completed" && state.status === "running") {
      terminalStatus("completed", prettyOutput(task.final_result));
    } else if ((task.status === "failed" || task.status === "cancelled") && state.status === "running") {
      const message = task.runs?.at(-1)?.error_message || `Task ${task.status}.`;
      terminalStatus(task.status === "cancelled" ? "stopped" : "failed", message, message);
    }
  } catch (error) {
    console.warn("Could not hydrate WebMind task state:", error);
  }
}

function connectTaskEvents(taskId: string): Promise<void> {
  const source = new EventSource(`${API_BASE}/api/tasks/${taskId}/events`);
  activeSource = source;

  const eventNames = [
    "profile_created", "routed", "step_started", "action_planned", "verification", "retry",
    "memory_event", "escalated", "result", "error", "cancelled", "ask_user", "user_resumed",
  ];
  for (const eventName of eventNames) {
    source.addEventListener(eventName, (event) => {
      try {
        const data = JSON.parse((event as MessageEvent<string>).data) as Record<string, any>;
        useWebMindStore.getState().receiveBackendEvent(eventName, data);
      } catch (error) {
        console.warn(`Could not read WebMind ${eventName} event:`, error);
      }
    });
  }
  let didOpen = false;
  let resolveConnected: (() => void) | undefined;
  let rejectConnected: ((reason: Error) => void) | undefined;
  const connected = new Promise<void>((resolve, reject) => {
    resolveConnected = resolve;
    rejectConnected = reject;
  });
  const openTimeout = setTimeout(() => {
    if (didOpen) return;
    source.close();
    if (activeSource === source) activeSource = null;
    rejectConnected?.(new Error("Timed out connecting to the backend event stream."));
  }, 8000);
  source.onopen = () => {
    didOpen = true;
    clearTimeout(openTimeout);
    void hydrateTask(taskId);
    resolveConnected?.();
  };
  source.onerror = () => {
    const current = useWebMindStore.getState();
    if (!didOpen && source.readyState === EventSource.CLOSED) {
      clearTimeout(openTimeout);
      if (activeSource === source) activeSource = null;
      rejectConnected?.(new Error("Could not connect to the backend event stream."));
      return;
    }
    if (source.readyState === EventSource.CLOSED && current.status === "running") {
      const message = "The backend event stream closed before the task reported a result.";
      terminalStatus("failed", message, message);
    }
  };
  return connected;
}

export const useWebMindStore = create<WebMindState>((set, get) => ({
  page: "agent",
  query: DEMO_TASK,
  status: "idle",
  tier: "Balanced",
  currentStep: 0,
  taskId: null,
  profile: null,
  steps: [],
  memory: [],
  metrics: emptyMetrics,
  finalOutput: "",
  errorMessage: "",
  askUserRequest: null,
  userReply: "",

  setPage: (page) => set({ page }),
  setQuery: (query) => set({ query }),
  setUserReply: (userReply) => set({ userReply }),

  receiveEvent: (event) => {
    switch (event.type) {
      case "status":
        set((state) => ({
          status: event.status,
          tier: event.tier ?? state.tier,
          currentStep: event.step ?? state.currentStep,
          metrics: event.status === "running" ? { ...state.metrics, outcome: "In progress" } : state.metrics,
        }));
        break;
      case "step": {
        const step: AgentStep = { id: `${event.n}-${event.attempt}`, ...event };
        set((state) => ({ steps: mergeStep(state.steps, step) }));
        break;
      }
      case "memory":
        set((state) => ({ memory: upsertMemory(state.memory, event.key, event.value) }));
        break;
      case "metrics":
        set({ metrics: {
          totalSteps: event.totalSteps,
          successfulSteps: event.successfulSteps,
          failedSteps: event.failedSteps,
          elapsedSeconds: event.elapsedSeconds,
          outcome: event.outcome,
        } });
        break;
      case "final":
        terminalStatus("completed", event.output);
        break;
    }
  },

  receiveBackendEvent: (eventName, data) => {
    const state = get();
    const updateMetrics = (steps: AgentStep[], outcome = "In progress") => {
      set({ steps, metrics: countMetrics(steps, getElapsedSeconds(), outcome) });
    };

    switch (eventName) {
      case "profile_created": {
        const profile = data as AgentProfile;
        const targetTier = tierLabel(profile.routed_tier);
        set((current) => ({
          profile,
          ...(targetTier ? { tier: targetTier } : {}),
          memory: upsertMemory(current.memory, "Task type", profile.task_type || "Browser task"),
        }));
        if (profile.constraints && Object.keys(profile.constraints).length) {
          set((current) => ({ memory: upsertMemory(current.memory, "Task constraints", JSON.stringify(profile.constraints)) }));
        }
        if (profile.target_sites?.length) {
          set((current) => ({ memory: upsertMemory(current.memory, "Requested sites", profile.target_sites!.join(", ")) }));
        }
        break;
      }
      case "routed": {
        const targetTier = tierLabel(data.tier);
        if (targetTier) set({ tier: targetTier });
        break;
      }
      case "step_started": {
        const n = Number(data.step_number || 0);
        const attempt = Number(data.retry_count || 0) + 1;
        const title = String(data.title || "Page").trim();
        const url = String(data.url || "");
        const controls = Number(data.element_count || 0);
        const observation = [title, url, controls ? `${controls} interactive controls found` : "Page state captured"]
          .filter(Boolean).join(" · ");
        const step: AgentStep = {
          id: `${n}-${attempt}`, n, attempt, observation,
          action: "Inspecting the page and planning the next action…",
          result: "in_progress", url,
        };
        const steps = mergeStep(state.steps, step);
        set({ currentStep: n, memory: upsertMemory(state.memory, "Last visited page", `${title}${url ? ` · ${url}` : ""}`) });
        updateMetrics(steps);
        break;
      }
      case "action_planned": {
        const n = Number(data.step_number || state.currentStep || 0);
        const existing = [...state.steps].reverse().find((step) => step.n === n);
        const step: AgentStep = {
          id: existing?.id || `${n}-1`, n, attempt: existing?.attempt || 1,
          observation: existing?.observation || "The agent inspected the current page.",
          action: actionLabel(data.action), result: existing?.result || "in_progress", url: existing?.url,
        };
        const steps = mergeStep(state.steps, step);
        set({ memory: upsertMemory(state.memory, "Latest action", step.action) });
        updateMetrics(steps);
        break;
      }
      case "verification": {
        const n = Number(data.step_number || state.currentStep || 0);
        const existing = [...state.steps].reverse().find((step) => step.n === n);
        if (!existing) break;
        const success = data.status === "success";
        const steps = mergeStep(state.steps, {
          ...existing,
          result: success ? "success" : "failed",
          error: success ? undefined : String(data.reason || data.failure_type || "Action could not be verified."),
        });
        set({ memory: upsertMemory(state.memory, "Latest verification", success ? `Step ${n} verified` : `${data.failure_type || "Failure"}: ${data.reason || "Action could not be verified"}`) });
        updateMetrics(steps);
        break;
      }
      case "retry": {
        const n = Number(data.step_number || state.currentStep || 0);
        const existing = [...state.steps].reverse().find((step) => step.n === n);
        const steps = existing ? mergeStep(state.steps, {
          ...existing,
          result: "failed",
          error: String(data.reason || "The agent is changing approach and retrying this step."),
        }) : state.steps;
        set({ memory: upsertMemory(state.memory, "Recovery note", String(data.reason || "Agent is retrying with a different approach.")) });
        updateMetrics(steps);
        break;
      }
      case "memory_event": {
        const stage = String(data.stage || "Agent memory");
        const detail = data.retrieval_count !== undefined
          ? `${data.retrieval_count} experience memories checked`
          : [data.decision, data.reason].filter(Boolean).join(" · ");
        set((current) => ({ memory: upsertMemory(current.memory, stage.replaceAll("_", " "), String(detail || "Updated")) }));
        break;
      }
      case "escalated": {
        const targetTier = tierLabel(data.to_tier);
        if (targetTier) set({ tier: targetTier });
        set((current) => ({ memory: upsertMemory(current.memory, "Tier escalation", String(data.reason || `${data.from_tier} → ${data.to_tier}`)) }));
        break;
      }
      case "ask_user": {
        const request = data as AskUserRequest;
        set({ askUserRequest: request });
        const n = Number(data.step_number || state.currentStep || 0);
        const existing = [...state.steps].reverse().find((step) => step.n === n);
        const steps = mergeStep(state.steps, {
          id: existing?.id || `${n}-1`, n, attempt: existing?.attempt || 1,
          observation: existing?.observation || "The agent paused for user input.",
          action: String(data.prompt || "Waiting for your input"), result: "in_progress", url: existing?.url,
        });
        updateMetrics(steps);
        break;
      }
      case "user_resumed":
        set({ askUserRequest: null, userReply: "" });
        break;
      case "result":
        terminalStatus("completed", prettyOutput(data.result));
        break;
      case "error": {
        const message = String(data.error_message || data.reason || "The agent task failed.");
        terminalStatus("failed", message, message);
        break;
      }
      case "cancelled": {
        const message = String(data.message || "Task stopped.");
        terminalStatus("stopped", state.steps.length ? `Task stopped. ${state.steps.length} step attempt(s) are preserved.` : message);
        break;
      }
    }
  },

  startTask: async () => {
    const { query, status } = get();
    if (status === "running" || !query.trim()) return;
    closeRunConnections();
    stopAfterCreate = false;
    taskStartedAt = Date.now();
    set({
      page: "agent", status: "running", tier: "Balanced", currentStep: 0, taskId: null,
      profile: null, steps: [], memory: [], metrics: { ...emptyMetrics, outcome: "In progress" },
      finalOutput: "", errorMessage: "", askUserRequest: null, userReply: "",
    });
    elapsedTimer = setInterval(() => {
      const current = get();
      if (current.status !== "running") return;
      set({ metrics: countMetrics(current.steps, getElapsedSeconds(), "In progress") });
    }, 1000);

    try {
      if (import.meta.env.VITE_USE_MOCK_AGENT === "true") {
        activeMock = startMockAgentStream(query.trim(), get().receiveEvent);
        return;
      }
      const created = await apiPost<{ task_id: string }>("/api/tasks", { query: query.trim() });
      set({ taskId: created.task_id });
      void connectTaskEvents(created.task_id).catch((cause) => {
        const message = cause instanceof Error ? cause.message : "The live progress stream disconnected.";
        if (get().status === "running") {
          set({ errorMessage: `Task started, but live progress could not connect: ${message}` });
        }
      });
      if (stopAfterCreate) await get().stopTask();
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Could not start the task.";
      terminalStatus("failed", `Could not start the task: ${message}`, message);
    }
  },

  stopTask: async () => {
    const current = get();
    if (current.status !== "running") return;
    if (activeMock) {
      activeMock.stop();
      activeMock = null;
      terminalStatus("stopped", current.steps.length
        ? `Task stopped. ${current.steps.length} step attempt(s) are preserved.`
        : "Task stopped before any browser steps were recorded.");
      return;
    }
    if (!current.taskId) {
      stopAfterCreate = true;
      return;
    }
    const taskId = current.taskId;
    try {
      await apiPost(`/api/tasks/${taskId}/stop`, {});
      terminalStatus("stopped", current.steps.length
        ? `Task stopped. ${current.steps.length} step attempt(s) are preserved.`
        : "Task stopped before any browser steps were recorded.");
    } catch (cause) {
      try {
        const task = await apiGet<Record<string, any>>(`/api/tasks/${taskId}`);
        if (task.status === "completed") {
          terminalStatus("completed", prettyOutput(task.final_result));
          return;
        }
        if (task.status === "failed") {
          const message = task.runs?.at(-1)?.error_message || "The task failed before it could be stopped.";
          terminalStatus("failed", message, message);
          return;
        }
      } catch { /* Fall through to the actionable stop error. */ }
      const message = cause instanceof Error ? cause.message : "Could not stop the task.";
      set({ errorMessage: message, memory: upsertMemory(get().memory, "Stop request", message) });
    }
  },

  resumeTask: async (approved = true) => {
    const { taskId, userReply } = get();
    if (!taskId) return;
    try {
      await apiPost(`/api/tasks/${taskId}/confirm`, { approved, user_input: approved ? userReply.trim() || null : null });
      set({ askUserRequest: null, userReply: "" });
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "Could not resume the agent.";
      set({ errorMessage: message });
    }
  },
}));
