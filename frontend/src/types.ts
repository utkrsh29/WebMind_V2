export type TaskStatus = "idle" | "running" | "completed" | "stopped" | "failed";
export type StepResult = "in_progress" | "success" | "failed";

export interface AgentStep {
  id: string;
  n: number;
  attempt: number;
  observation: string;
  action: string;
  result: StepResult;
  error?: string;
  url?: string;
}

export interface MemoryItem {
  key: string;
  value: string;
}

export interface RunMetrics {
  totalSteps: number;
  successfulSteps: number;
  failedSteps: number;
  elapsedSeconds: number;
  outcome: string;
}

export interface AgentProfile {
  task_type?: string;
  constraints?: Record<string, unknown>;
  target_sites?: string[];
  routed_tier?: string;
}

export type MockAgentEvent =
  | { type: "status"; status: TaskStatus; tier?: string; step?: number }
  | ({ type: "step" } & Omit<AgentStep, "id">)
  | { type: "memory"; key: string; value: string }
  | ({ type: "metrics" } & RunMetrics)
  | { type: "final"; output: string };
