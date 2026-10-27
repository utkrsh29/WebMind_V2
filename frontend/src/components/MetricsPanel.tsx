import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Activity, Clock3, Coins, Gauge, History, RefreshCw, ShieldCheck, TrendingUp, Zap } from "lucide-react";
import { apiGet } from "../api";
import { useWebMindStore } from "../store/useWebMindStore";

interface MetricsData {
  total_tasks: number;
  success_rate: number;
  avg_latency_seconds: number;
  total_tokens_used: number;
  escalation_rate: number;
  recovery_rate: number;
  action_efficiency: number;
  tier_distribution: Record<string, number>;
  eval_benchmark_results?: Array<Record<string, unknown>> | null;
}

interface TaskHistoryItem {
  id: string;
  query: string;
  status: string;
  tier: string;
  step_count: number;
  created_at: string;
}

interface TaskHistoryResponse { tasks: TaskHistoryItem[] }

function formatDuration(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${remainder}s` : `${remainder}s`;
}

export function MetricsPanel() {
  const currentRun = useWebMindStore((state) => state.metrics);
  const [metrics, setMetrics] = useState<MetricsData | null>(null);
  const [history, setHistory] = useState<TaskHistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const loadMetrics = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [metricsData, historyData] = await Promise.all([
        apiGet<MetricsData>("/api/metrics"),
        apiGet<TaskHistoryResponse>("/api/tasks?limit=20"),
      ]);
      setMetrics(metricsData);
      setHistory(historyData.tasks ?? []);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not load historical metrics.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadMetrics(); }, [loadMetrics]);

  return (
    <div className="page-stack">
      <section className="panel page-panel" aria-labelledby="metrics-title">
        <div className="panel-heading">
          <div className="panel-title-wrap">
            <span className="panel-icon"><Gauge size={17} /></span>
            <div>
              <h1 id="metrics-title">Metrics &amp; Eval</h1>
              <p className="helper-text">How the run went and whether it met the goal.</p>
            </div>
          </div>
          <button className="icon-action" type="button" onClick={() => void loadMetrics()} disabled={loading} aria-label="Refresh metrics" title="Refresh">
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          </button>
        </div>

        <div className="panel-content page-panel-content">
          <p className="subsection-label"><Activity size={14} /> Current task</p>
          <div className="metrics-grid current-run-grid">
            <Metric label="Total steps" value={currentRun.totalSteps} />
            <Metric label="Successful" value={currentRun.successfulSteps} tone="success" />
            <Metric label="Failed" value={currentRun.failedSteps} tone={currentRun.failedSteps ? "failure" : undefined} />
            <Metric label="Elapsed" value={formatDuration(currentRun.elapsedSeconds)} />
          </div>
          <div className="outcome-row"><span>Current outcome</span><strong>{currentRun.outcome}</strong></div>

          <p className="subsection-label historical-label"><History size={14} /> Saved performance</p>
          {error && <div className="api-error">{error} Check that the WebMind backend is running.</div>}
          <div className="metrics-grid historical-grid">
            <Metric icon={<TrendingUp size={15} />} label="Tasks completed" value={metrics ? `${metrics.success_rate}% · ${metrics.total_tasks} total` : "Loading…"} />
            <Metric icon={<Clock3 size={15} />} label="Average latency" value={metrics ? `${metrics.avg_latency_seconds}s` : "Loading…"} />
            <Metric icon={<Coins size={15} />} label="Total tokens" value={metrics ? metrics.total_tokens_used.toLocaleString() : "Loading…"} />
            <Metric icon={<History size={15} />} label="Escalation rate" value={metrics ? `${metrics.escalation_rate}%` : "Loading…"} />
            <Metric icon={<ShieldCheck size={15} />} label="Recovery rate" value={metrics ? `${metrics.recovery_rate}%` : "Loading…"} />
            <Metric icon={<Zap size={15} />} label="Steps per completed task" value={metrics ? String(metrics.action_efficiency) : "Loading…"} />
          </div>

          <p className="subsection-label historical-label"><History size={14} /> Recent task history</p>
          {loading && history.length === 0 ? <div className="panel-empty">Loading saved task history…</div> : history.length === 0 ? <div className="panel-empty">No saved tasks yet.</div> : (
            <div className="history-table-wrap">
              <table className="history-table">
                <thead><tr><th>Task</th><th>Status</th><th>Tier</th><th>Steps</th><th>Started</th></tr></thead>
                <tbody>{history.map((task) => (
                  <tr key={task.id}>
                    <td className="history-query" title={task.query}>{task.query}</td>
                    <td><span className={`outcome-pill outcome-${task.status}`}>{task.status}</span></td>
                    <td>{task.tier}</td>
                    <td>{task.step_count}</td>
                    <td>{new Date(task.created_at).toLocaleString()}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}

          <p className="helper-text metrics-footnote">Values above are loaded from saved WebMind task runs. Evaluation benchmarks appear when the backend has recorded benchmark results.</p>
          {metrics?.eval_benchmark_results?.length ? <div className="benchmark-note">{metrics.eval_benchmark_results.length} saved evaluation result(s) available.</div> : null}
        </div>
      </section>
    </div>
  );
}

function Metric({ icon, label, value, tone }: { icon?: ReactNode; label: string; value: string | number; tone?: "success" | "failure" }) {
  return (
    <div className={`metric-tile ${tone ? `metric-${tone}` : ""}`}>
      <span className="metric-label">{icon}{label}</span>
      <strong>{value}</strong>
    </div>
  );
}
