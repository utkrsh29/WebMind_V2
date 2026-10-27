import { useCallback, useEffect, useState } from "react";
import { BrainCircuit, Database, RefreshCw } from "lucide-react";
import { apiGet } from "../api";
import { useWebMindStore } from "../store/useWebMindStore";

interface PersistedMemory {
  memory_id: string;
  task_description: string;
  task_type: string;
  website_domain?: string | null;
  action_attempted?: { action_type?: string; target_description?: string };
  failure?: { failure_type?: string; reason?: string } | null;
  successful_alternative?: { action_type?: string; target_description?: string } | null;
  outcome: string;
  reliability_score: number;
  success_count: number;
  failure_count: number;
  retrieval_count: number;
  updated_at: string;
}

interface PersistedMemoryResponse {
  items: PersistedMemory[];
  page: number;
  page_size: number;
  total: number;
}

export function MemoryPanel() {
  const currentMemory = useWebMindStore((state) => state.memory);
  const [saved, setSaved] = useState<PersistedMemoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadMemory = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const data = await apiGet<PersistedMemoryResponse>("/api/memories?page=1&page_size=20");
      setSaved(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load saved memories.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadMemory(); }, [loadMemory]);

  return (
    <div className="page-stack">
      <section className="panel page-panel" aria-labelledby="memory-title">
        <div className="panel-heading">
          <div className="panel-title-wrap">
            <span className="panel-icon"><BrainCircuit size={17} /></span>
            <div>
              <h1 id="memory-title">Memory</h1>
              <p className="helper-text">Information the agent remembers while working on this task.</p>
            </div>
          </div>
        </div>
        <div className="panel-content page-panel-content">
          {currentMemory.length > 0 ? (
            <dl className="memory-list">
              {currentMemory.map((item) => (
                <div className="memory-row" key={item.key}><dt>{item.key}</dt><dd>{item.value}</dd></div>
              ))}
            </dl>
          ) : (
            <div className="panel-empty"><Database size={16} /><span>No information has been collected in the current task yet.</span></div>
          )}
        </div>
      </section>

      <section className="panel page-panel" aria-labelledby="saved-memory-title">
        <div className="panel-heading">
          <div className="panel-title-wrap">
            <span className="panel-icon"><Database size={17} /></span>
            <div>
              <h2 id="saved-memory-title">Saved experience memory</h2>
              <p className="helper-text">Persisted, verified browser outcomes from previous tasks.</p>
            </div>
          </div>
          <div className="saved-memory-actions">
            <span className="record-count">{saved ? `${saved.total} records` : "Loading records"}</span>
            <button className="icon-action" type="button" onClick={() => void loadMemory()} disabled={loading} aria-label="Refresh saved memories" title="Refresh">
              <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
            </button>
          </div>
        </div>
        <div className="persisted-list" aria-live="polite">
          {error && <div className="api-error">{error} Check that the WebMind backend is running.</div>}
          {loading && !saved ? <div className="panel-empty">Loading persisted memories…</div> : null}
          {!loading && saved?.items.length === 0 && <div className="panel-empty">No saved experience memories yet.</div>}
          {saved?.items.map((item) => (
            <article className="persisted-memory" key={item.memory_id}>
              <div className="persisted-memory-top">
                <span className={`outcome-pill outcome-${item.outcome}`}>{item.outcome}</span>
                <span className="persisted-domain">{item.website_domain || "Domain unavailable"}</span>
                <span className="persisted-type">{item.task_type}</span>
                <span className="persisted-reliability">Reliability {(item.reliability_score * 100).toFixed(0)}%</span>
              </div>
              <p className="persisted-query">{item.task_description}</p>
              <div className="persisted-fact-list">
                <span><b>Attempted</b>{item.action_attempted?.action_type || "—"} · {item.action_attempted?.target_description || "—"}</span>
                {item.failure?.reason && <span><b>Failure note</b>{item.failure.failure_type || "Failure"} · {item.failure.reason}</span>}
                {item.successful_alternative && <span><b>Verified alternative</b>{item.successful_alternative.action_type || "Action"} · {item.successful_alternative.target_description || ""}</span>}
              </div>
              <div className="persisted-stats">
                <span>{item.success_count} verified success</span>
                <span>{item.failure_count} failures</span>
                <span>{item.retrieval_count} retrievals</span>
                <time dateTime={item.updated_at}>{new Date(item.updated_at).toLocaleDateString()}</time>
              </div>
            </article>
          ))}
        </div>
      </section>
    </div>
  );
}
