import { Check, CircleAlert, Eye, LoaderCircle, RotateCcw, WandSparkles } from "lucide-react";
import type { AgentStep } from "../types";

export function StepCard({ step }: { step: AgentStep }) {
  const icon = step.result === "success"
    ? <Check size={15} />
    : step.result === "failed"
      ? <CircleAlert size={15} />
      : <LoaderCircle size={15} className="animate-spin" />;
  const resultLabel = step.result === "in_progress" ? "In progress" : step.result === "failed" ? "Failed" : "Success";

  return (
    <li className={`step-card step-${step.result} step-enter`}>
      <div className="step-topline">
        <span className="step-number">Step {step.n}{step.attempt > 1 ? <span className="retry-label"><RotateCcw size={11} /> Retry</span> : null}</span>
        <span className={`result-badge result-${step.result}`}>{icon}{resultLabel}</span>
      </div>
      <div className="step-detail">
        <div className="detail-icon"><Eye size={14} /></div>
        <div><span className="detail-label">Observation</span><p>{step.observation}</p></div>
      </div>
      <div className="step-detail">
        <div className="detail-icon"><WandSparkles size={14} /></div>
        <div><span className="detail-label">Action</span><p>{step.action}</p></div>
      </div>
      {step.error && <p className="step-error"><CircleAlert size={14} />{step.error}</p>}
    </li>
  );
}
