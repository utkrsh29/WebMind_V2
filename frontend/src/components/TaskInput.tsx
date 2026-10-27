import { ArrowUp, Globe2 } from "lucide-react";
import { useWebMindStore } from "../store/useWebMindStore";

export function TaskInput() {
  const query = useWebMindStore((state) => state.query);
  const status = useWebMindStore((state) => state.status);
  const setQuery = useWebMindStore((state) => state.setQuery);
  const startTask = useWebMindStore((state) => state.startTask);
  const isRunning = status === "running";

  return (
    <section aria-labelledby="task-input-title" className="task-input-section">
      <div className="section-eyebrow"><Globe2 size={15} /> Browser task</div>
      <h1 id="task-input-title">What should WebMind do?</h1>
      <form
        className="task-form"
        onSubmit={(event) => { event.preventDefault(); startTask(); }}
      >
        <label className="sr-only" htmlFor="task-query">Describe a browser task in plain English</label>
        <textarea
          id="task-query"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Describe a browser task in plain English"
          rows={3}
          disabled={isRunning}
          required
        />
        <div className="task-form-footer">
          <span className="input-hint">Describe the result you want in plain English.</span>
          <button type="submit" className="run-button" disabled={isRunning || !query.trim()}>
            <span>{isRunning ? "Running" : "Run task"}</span>
            <ArrowUp size={16} />
          </button>
        </div>
      </form>
    </section>
  );
}
