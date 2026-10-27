import { Square } from "lucide-react";
import { useWebMindStore } from "../store/useWebMindStore";

export function StopButton() {
  const status = useWebMindStore((state) => state.status);
  const stopTask = useWebMindStore((state) => state.stopTask);
  const enabled = status === "running";

  return (
    <div className="stop-row">
      <span className="helper-text">You can stop the run at any time.</span>
      <button className="stop-button" type="button" onClick={stopTask} disabled={!enabled}>
        <Square size={14} fill={enabled ? "currentColor" : "none"} />
        Stop task
      </button>
    </div>
  );
}
