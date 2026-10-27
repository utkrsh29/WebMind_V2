import { useState } from "react";
import { Check, CircleAlert, Clipboard, Copy, OctagonAlert } from "lucide-react";
import { useWebMindStore } from "../store/useWebMindStore";

export function FinalResult() {
  const status = useWebMindStore((state) => state.status);
  const output = useWebMindStore((state) => state.finalOutput);
  const [copied, setCopied] = useState(false);
  if (!["completed", "stopped", "failed"].includes(status)) return null;

  const completed = status === "completed";
  const failed = status === "failed";

  const copyResult = async () => {
    try {
      await navigator.clipboard.writeText(output);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      setCopied(false);
    }
  };

  return (
    <section className={`final-card ${completed ? "final-completed" : failed ? "final-failed" : "final-stopped"}`} aria-labelledby="final-result-title">
      <div className="final-heading">
        <span className="final-icon">{completed ? <Check size={17} /> : failed ? <OctagonAlert size={17} /> : <CircleAlert size={17} />}</span>
        <div>
          <h2 id="final-result-title">{completed ? "Task result" : failed ? "Task failed" : "Task stopped"}</h2>
          <p>{completed ? "WebMind finished this run." : "Here is the information collected before the run ended."}</p>
        </div>
        {completed && <button type="button" className="copy-button" onClick={copyResult} aria-label="Copy result">
          {copied ? <Check size={15} /> : <Copy size={15} />}{copied ? "Copied" : "Copy"}
        </button>}
      </div>
      <pre className="final-output">{output || "No additional information was collected."}</pre>
      {!completed && <div className="partial-note"><Clipboard size={14} /> Progress log and task memory are preserved above.</div>}
    </section>
  );
}
