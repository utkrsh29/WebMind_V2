import type { MockAgentEvent } from "../types";

export interface MockAgentStream {
  stop: () => void;
}

type EventHandler = (event: MockAgentEvent) => void;

const DEMO_STEPS = [
  { observation: "The example store loaded and its search field is available.", action: "Opened example-store.com" },
  { observation: "The search field is ready for a product query.", action: "Searched for USB-C hub" },
  { observation: "Several USB-C hub listings appeared with product names and prices.", action: "Read the product results" },
  { observation: "The results can be ordered by price.", action: "Sorted results from lowest to highest price" },
  { observation: "The lowest-priced listing shows a price of $19.99.", action: "Extracted the cheapest listing and its price" },
  { observation: "The requested price has been collected.", action: "Prepared the final answer" },
];

const MEMORY_NOTES = [
  ["Visited page", "https://example-store.com"],
  ["Search query", "USB-C hub"],
  ["Sort order", "Price: low to high"],
  ["Cheapest listing", "USB-C hub · $19.99"],
];

export function startMockAgentStream(query: string, onEvent: EventHandler): MockAgentStream {
  let stopped = false;
  const timers = new Set<ReturnType<typeof setTimeout>>();
  const startedAt = Date.now();
  const failedAt = Math.floor(Math.random() * DEMO_STEPS.length);
  let attemptCount = 0;
  let successCount = 0;
  let failureCount = 0;

  const schedule = (callback: () => void, delay = 650) => {
    const timer = setTimeout(() => {
      timers.delete(timer);
      if (!stopped) callback();
    }, delay);
    timers.add(timer);
  };

  const emitMetrics = (outcome = "In progress") => {
    onEvent({
      type: "metrics",
      totalSteps: attemptCount,
      successfulSteps: successCount,
      failedSteps: failureCount,
      elapsedSeconds: Math.max(1, Math.floor((Date.now() - startedAt) / 1000)),
      outcome,
    });
  };

  const runStep = (index: number, retry = false) => {
    if (stopped) return;
    if (index >= DEMO_STEPS.length) {
      onEvent({ type: "status", status: "completed", tier: "Balanced", step: DEMO_STEPS.length });
      emitMetrics("Completed");
      onEvent({
        type: "final",
        output: `The cheapest USB-C hub on example-store.com is listed at $19.99.\n\nDemo task: ${query}`,
      });
      return;
    }

    const step = DEMO_STEPS[index];
    const attempt = retry ? 2 : 1;
    attemptCount += 1;
    onEvent({ type: "status", status: "running", tier: "Balanced", step: index + 1 });
    onEvent({
      type: "step",
      n: index + 1,
      attempt,
      observation: step.observation,
      action: retry ? `Retrying: ${step.action}` : step.action,
      result: "in_progress",
    });
    emitMetrics();
    schedule(() => {
      if (!retry && index === failedAt) {
        failureCount += 1;
        onEvent({
          type: "step",
          n: index + 1,
          attempt,
          observation: step.observation,
          action: step.action,
          result: "failed",
          error: "The page response took too long. The agent will retry this step once.",
        });
        emitMetrics();
        schedule(() => runStep(index, true), 700);
        return;
      }

      successCount += 1;
      onEvent({
        type: "step",
        n: index + 1,
        attempt,
        observation: retry ? `${step.observation} (verified on retry)` : step.observation,
        action: retry ? `Retried: ${step.action}` : step.action,
        result: "success",
      });
      const memoryNote = MEMORY_NOTES[index];
      if (memoryNote) onEvent({ type: "memory", key: memoryNote[0], value: memoryNote[1] });
      emitMetrics();
      schedule(() => runStep(index + 1), 260);
    }, 500);
  };

  onEvent({ type: "status", status: "running", tier: "Balanced", step: 0 });
  onEvent({ type: "memory", key: "Task", value: query });
  emitMetrics();
  schedule(() => runStep(0), 450);

  return {
    stop() {
      stopped = true;
      timers.forEach(clearTimeout);
      timers.clear();
    },
  };
}
