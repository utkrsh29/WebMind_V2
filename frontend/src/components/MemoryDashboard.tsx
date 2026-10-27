"use client";

import React, { useCallback, useEffect, useState } from "react";
import { Brain, RefreshCw, Search, Trash2, ShieldCheck, AlertTriangle, CircleHelp } from "lucide-react";

interface MemoryItem {
  memory_id: string;
  schema_version: number;
  task_description: string;
  task_type: string;
  website_domain?: string | null;
  page_url_pattern: string;
  page_context: { page_title?: string; relevant_elements?: string[]; page_state_summary?: string };
  action_attempted: { action_type?: string; target_description?: string };
  failure?: { failure_type?: string; reason?: string } | null;
  successful_alternative?: { action_type?: string; target_description?: string; recovery_steps?: string[] } | null;
  verified_workflow?: Array<{ action_type?: string; target_description?: string }>;
  outcome: string;
  reliability_score: number;
  success_count: number;
  failure_count: number;
  retrieval_count: number;
  updated_at: string;
}

interface MemoryDetail extends MemoryItem {
  retrieval_history: Array<{ task_id: string; decision: string; reason: string; planner_selected: boolean; feedback_applied: boolean; attributed_outcome?: string | null; created_at?: string | null }>;
  evidence_history: Array<{ task_id: string; outcome: string; evidence: Record<string, unknown>; created_at?: string | null }>;
}

const API = "/api/memories";

function OutcomeBadge({ outcome }: { outcome: string }) {
  const style = outcome === "recovered"
    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-300"
    : outcome === "success"
      ? "border-teal-500/30 bg-teal-500/10 text-teal-300"
      : "border-rose-500/30 bg-rose-500/10 text-rose-300";
  const Icon = outcome === "recovered" || outcome === "success" ? ShieldCheck : AlertTriangle;
  return <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[10px] font-bold uppercase ${style}`}><Icon className="h-3 w-3" />{outcome}</span>;
}

export const MemoryDashboard: React.FC = () => {
  const [items, setItems] = useState<MemoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [domain, setDomain] = useState("");
  const [taskType, setTaskType] = useState("");
  const [outcome, setOutcome] = useState("");
  const [minReliability, setMinReliability] = useState("");
  const [selected, setSelected] = useState<MemoryDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (requestedPage = page) => {
    setLoading(true);
    setError("");
    try {
      const params = new URLSearchParams({ page: String(requestedPage), page_size: "20" });
      if (domain.trim()) params.set("domain", domain.trim());
      if (taskType) params.set("task_type", taskType);
      if (outcome) params.set("outcome", outcome);
      if (minReliability !== "") params.set("min_reliability", minReliability);
      const response = await fetch(`${API}?${params}`);
      if (!response.ok) throw new Error(`Could not load memories (${response.status}).`);
      const data = await response.json();
      setItems(data.items ?? []);
      setTotal(data.total ?? 0);
    } catch (err: any) {
      setError(err.message ?? "Could not load memories.");
    } finally {
      setLoading(false);
    }
  }, [page, domain, taskType, outcome, minReliability]);

  useEffect(() => { void load(page); }, [page, load]);

  const openDetail = async (id: string) => {
    try {
      const response = await fetch(`${API}/${id}`);
      if (!response.ok) throw new Error(`Could not load memory details (${response.status}).`);
      setSelected(await response.json());
    } catch (err: any) {
      setError(err.message ?? "Could not load memory details.");
    }
  };

  const remove = async (id: string) => {
    if (!window.confirm("Delete this experience memory and its history?")) return;
    const response = await fetch(`${API}/${id}`, { method: "DELETE" });
    if (!response.ok) { setError("Could not delete this memory."); return; }
    if (selected?.memory_id === id) setSelected(null);
    await load(page);
  };

  const clearAll = async () => {
    if (!window.confirm(`Permanently clear all ${total} memories and their histories? This cannot be undone.`)) return;
    const response = await fetch(`${API}?confirm=true`, { method: "DELETE" });
    if (!response.ok) { setError("Could not clear memories."); return; }
    setSelected(null);
    setPage(1);
    await load(1);
  };

  return (
    <section className="w-full space-y-5">
      <div className="glass-bright rounded-2xl border border-white/[0.08] p-5 md:p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="flex items-center gap-2 text-xl font-bold text-white"><Brain className="h-5 w-5 text-violet-300" />Experience Memory</h2>
            <p className="mt-1 text-xs text-slate-400">Verified browser outcomes, applicability decisions, and persistent reliability.</p>
          </div>
          <div className="flex gap-2">
            <button onClick={() => load(page)} disabled={loading} className="inline-flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-50"><RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />Refresh</button>
            <button onClick={clearAll} disabled={!total} className="inline-flex items-center gap-2 rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200 hover:bg-rose-500/20 disabled:opacity-40"><Trash2 className="h-3.5 w-3.5" />Clear all</button>
          </div>
        </div>

        <div className="mt-5 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-5">
          <label className="relative"><Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-500" /><input value={domain} onChange={e => { setPage(1); setDomain(e.target.value); }} placeholder="Website domain" className="w-full rounded-lg border border-slate-700 bg-slate-950/50 py-2 pl-9 pr-3 text-xs text-white outline-none focus:border-teal-500" /></label>
          <select value={taskType} onChange={e => { setPage(1); setTaskType(e.target.value); }} className="rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-xs text-slate-200"><option value="">All task types</option>{["search", "navigation", "extraction", "form_fill", "filtering", "comparison", "multi_site", "other"].map(v => <option key={v}>{v}</option>)}</select>
          <select value={outcome} onChange={e => { setPage(1); setOutcome(e.target.value); }} className="rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-xs text-slate-200"><option value="">All outcomes</option><option value="success">Success</option><option value="recovered">Recovered failure</option><option value="failure">Failure</option></select>
          <label className="flex items-center gap-2 rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-xs text-slate-400">Min reliability <input type="number" min="0" max="1" step="0.05" value={minReliability} onChange={e => { setPage(1); setMinReliability(e.target.value); }} className="w-16 bg-transparent text-white outline-none" /></label>
          <div className="rounded-lg border border-slate-700 bg-slate-950/50 px-3 py-2 text-xs text-slate-400">{total} persisted entr{total === 1 ? "y" : "ies"}</div>
        </div>
      </div>

      {error && <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 p-3 text-xs text-rose-200">{error}</div>}
      <div className="grid gap-5 xl:grid-cols-[1.1fr_0.9fr]">
        <div className="glass-bright overflow-hidden rounded-2xl border border-white/[0.08]">
          {loading && items.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">Loading persisted memories…</div> : items.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">No matching execution memories have been recorded.</div> : (
            <div className="divide-y divide-white/[0.06]">
              {items.map(item => <div key={item.memory_id} className="flex items-start gap-3 p-4 hover:bg-white/[0.02]">
                <button onClick={() => openDetail(item.memory_id)} className="min-w-0 flex-1 text-left">
                  <div className="flex flex-wrap items-center gap-2"><OutcomeBadge outcome={item.outcome} /><span className="text-[10px] text-slate-500">{item.website_domain || "domain unavailable"}</span><span className="text-[10px] text-slate-600">{item.task_type}</span></div>
                  <p className="mt-2 line-clamp-2 text-sm font-medium text-slate-100">{item.task_description}</p>
                  <p className="mt-1 line-clamp-1 text-xs text-slate-500">{item.successful_alternative?.target_description || item.verified_workflow?.map(step => step.target_description).filter(Boolean).join(" → ") || item.action_attempted?.target_description}</p>
                  <div className="mt-3 flex flex-wrap gap-3 text-[10px] text-slate-500"><span>Reliability {(item.reliability_score * 100).toFixed(0)}%</span><span>{item.success_count} verified success</span><span>{item.failure_count} attributable failure</span><span>{item.retrieval_count} retrievals</span></div>
                </button>
                <button aria-label="Delete memory" onClick={() => remove(item.memory_id)} className="rounded-md p-2 text-slate-600 hover:bg-rose-500/10 hover:text-rose-300"><Trash2 className="h-4 w-4" /></button>
              </div>)}
            </div>
          )}
          <div className="flex items-center justify-between border-t border-white/[0.06] px-4 py-3 text-xs text-slate-500"><span>Page {page}</span><div className="flex gap-2"><button onClick={() => setPage(p => Math.max(1, p - 1))} disabled={page <= 1} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-30">Previous</button><button onClick={() => setPage(p => p + 1)} disabled={page * 20 >= total} className="rounded border border-slate-700 px-2 py-1 disabled:opacity-30">Next</button></div></div>
        </div>

        <div className="glass-bright min-h-[320px] rounded-2xl border border-white/[0.08] p-5">
          {!selected ? <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-slate-600"><CircleHelp className="h-8 w-8 text-slate-700" /><p className="text-sm">Select a memory to inspect its evidence and gate history.</p></div> : <>
            <div className="flex items-start justify-between gap-3"><div><OutcomeBadge outcome={selected.outcome} /><h3 className="mt-3 text-base font-semibold text-white">{selected.task_description}</h3><p className="mt-1 text-xs text-slate-500">{selected.website_domain || "domain unavailable"}{selected.page_url_pattern}</p></div><button onClick={() => remove(selected.memory_id)} className="rounded-md p-2 text-slate-500 hover:text-rose-300"><Trash2 className="h-4 w-4" /></button></div>
            <div className="mt-4 grid grid-cols-3 gap-2 text-center text-[10px]"><div className="rounded-lg bg-white/[0.04] p-2 text-slate-400">Reliability<strong className="mt-1 block text-sm text-white">{(selected.reliability_score * 100).toFixed(0)}%</strong></div><div className="rounded-lg bg-white/[0.04] p-2 text-slate-400">Verified success<strong className="mt-1 block text-sm text-emerald-300">{selected.success_count}</strong></div><div className="rounded-lg bg-white/[0.04] p-2 text-slate-400">Attributable failure<strong className="mt-1 block text-sm text-rose-300">{selected.failure_count}</strong></div></div>
            {selected.failure && <section className="mt-5"><h4 className="text-xs font-bold uppercase tracking-wider text-rose-300">Observed failure</h4><p className="mt-1 text-xs text-slate-300">{selected.failure.failure_type}: {selected.failure.reason}</p></section>}
            {selected.successful_alternative && <section className="mt-4"><h4 className="text-xs font-bold uppercase tracking-wider text-emerald-300">Verified recovery</h4><p className="mt-1 text-xs text-slate-300">{selected.successful_alternative.action_type} · {selected.successful_alternative.target_description}</p></section>}
            {selected.verified_workflow?.length ? <section className="mt-4"><h4 className="text-xs font-bold uppercase tracking-wider text-teal-300">Verified workflow</h4><ol className="mt-1 list-inside list-decimal text-xs text-slate-300">{selected.verified_workflow.map((step, i) => <li key={i}>{step.action_type} · {step.target_description}</li>)}</ol></section> : null}
            <section className="mt-5"><h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">Gate and retrieval history</h4><div className="mt-2 max-h-36 space-y-2 overflow-auto">{selected.retrieval_history.length ? selected.retrieval_history.map((event, i) => <div key={i} className="rounded-lg bg-black/20 p-2 text-[10px] text-slate-400"><span className="font-bold text-violet-300">{event.decision}</span> · {event.reason}<div className="mt-1 text-slate-600">selected {String(event.planner_selected)} · feedback {event.attributed_outcome || "none"}</div></div>) : <p className="text-xs text-slate-600">No retrieval decisions recorded yet.</p>}</div></section>
            <section className="mt-5"><h4 className="text-xs font-bold uppercase tracking-wider text-slate-400">Recorded evidence</h4><div className="mt-2 max-h-32 space-y-2 overflow-auto">{selected.evidence_history.length ? selected.evidence_history.map((event, i) => <div key={i} className="rounded-lg bg-black/20 p-2 text-[10px] text-slate-400"><span className="font-bold">{event.outcome}</span> · {String(event.evidence.reason || event.evidence.verification_status || "Verified run evidence")} <span className="text-slate-600">{event.created_at ? new Date(event.created_at).toLocaleString() : ""}</span></div>) : <p className="text-xs text-slate-600">No outcome evidence recorded yet.</p>}</div></section>
          </>}
        </div>
      </div>
    </section>
  );
};
