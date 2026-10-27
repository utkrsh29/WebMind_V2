import { useEffect, useRef } from "react";
import { Activity, ListChecks, MessageCircleMore } from "lucide-react";
import { useWebMindStore } from "../store/useWebMindStore";
import { StepCard } from "./StepCard";

export function AgentStudio() {
  const steps = useWebMindStore((state) => state.steps);
  const status = useWebMindStore((state) => state.status);
  const errorMessage = useWebMindStore((state) => state.errorMessage);
  const currentStep = useWebMindStore((state) => state.currentStep);
  const askUserRequest = useWebMindStore((state) => state.askUserRequest);
  const userReply = useWebMindStore((state) => state.userReply);
  const setUserReply = useWebMindStore((state) => state.setUserReply);
  const resumeTask = useWebMindStore((state) => state.resumeTask);
  const listRef = useRef<HTMLOListElement>(null);
  const shouldFollow = useRef(true);

  useEffect(() => {
    if (shouldFollow.current && listRef.current) {
      listRef.current.scrollTo({ top: listRef.current.scrollHeight, behavior: "smooth" });
    }
  }, [steps.length]);

  const handleScroll = () => {
    const list = listRef.current;
    if (!list) return;
    shouldFollow.current = list.scrollHeight - list.scrollTop - list.clientHeight < 48;
  };

  return (
    <section className="panel studio-panel" aria-labelledby="agent-studio-title">
      <div className="panel-heading">
        <div className="panel-title-wrap">
          <span className="panel-icon"><Activity size={17} /></span>
          <div>
            <h2 id="agent-studio-title">Agent Studio</h2>
            <p className="helper-text">A live log of what the agent sees and does.</p>
          </div>
        </div>
        <div className="studio-count"><ListChecks size={14} /> Step {currentStep}</div>
      </div>

      <ol
        className="step-list"
        ref={listRef}
        onScroll={handleScroll}
        role="log"
        aria-live="polite"
        aria-relevant="additions text"
        aria-label="Agent progress timeline"
      >
        {steps.length > 0 ? steps.map((step) => <StepCard key={step.id} step={step} />) : (
          <li className="empty-log">
            <span className="empty-log-icon"><ListChecks size={19} /></span>
            <span>{status === "running" ? "Starting the browser task…" : "Your task progress will appear here."}</span>
          </li>
        )}
      </ol>

      {errorMessage && <div className="api-error" role="alert">{errorMessage}</div>}

      {askUserRequest && (
        <div className="agent-prompt" role="region" aria-label="Agent needs your input">
          <div className="agent-prompt-heading"><MessageCircleMore size={16} /><strong>Agent needs your input</strong></div>
          <p>{askUserRequest.prompt}</p>
          {askUserRequest.is_login ? (
            <p className="helper-text">Complete sign-in in the open browser window, then resume here. Don’t enter your password in this panel.</p>
          ) : (
            <label className="reply-field">
              <span>Your response</span>
              <input value={userReply} onChange={(event) => setUserReply(event.target.value)} placeholder="Type a response (optional)" />
            </label>
          )}
          <div className="agent-prompt-actions">
            {askUserRequest.requires_approval && <button type="button" className="prompt-deny" onClick={() => void resumeTask(false)}>Cancel task</button>}
            <button type="button" className="run-button" onClick={() => void resumeTask(true)}>
              {askUserRequest.requires_approval ? "Approve and continue" : askUserRequest.is_login ? "I’m done, resume" : "Send and continue"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
