import { Check, Circle, LoaderCircle, OctagonAlert, Square } from "lucide-react";
import type { TaskStatus } from "../types";

const statusCopy: Record<TaskStatus, string> = {
  idle: "Idle",
  running: "Running",
  completed: "Completed",
  stopped: "Stopped",
  failed: "Failed",
};

const statusStyles: Record<TaskStatus, string> = {
  idle: "status-idle",
  running: "status-running",
  completed: "status-completed",
  stopped: "status-stopped",
  failed: "status-failed",
};

export function StatusBadge({ status }: { status: TaskStatus }) {
  const icon = {
    idle: <Circle size={14} />,
    running: <LoaderCircle size={14} className="animate-spin" />,
    completed: <Check size={14} strokeWidth={2.5} />,
    stopped: <Square size={13} />,
    failed: <OctagonAlert size={14} />,
  }[status];

  return (
    <span className={`status-badge ${statusStyles[status]}`} aria-live="polite" aria-atomic="true">
      {icon}
      <span>{statusCopy[status]}</span>
    </span>
  );
}
