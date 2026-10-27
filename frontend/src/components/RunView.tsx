"use client";

import React, { useState } from "react";
import {
  Activity, ArrowUpRight, CheckCircle2, XCircle,
  RefreshCw, AlertTriangle, ChevronDown, ChevronUp,
  Maximize2, Terminal, Clock, Code2, MessageSquare, Brain,
} from "lucide-react";

interface StepItem {
  step_number: number;
  tier: string;
  url?: string;
  title?: string;
  thought?: string;
  action?: any;
  expected_state?: any;
  screenshot_b64?: string;
  verification_status?: string;
  failure_type?: string;
  retry_count?: number;
  reason?: string;
}

interface RunViewProps {
  currentTier: string;
  steps: StepItem[];
  escalationLog: Array<{ from_tier: string; to_tier: string; reason: string }>;
  latestScreenshot: string | null;
  status: string;
  errorMessage?: string | null;
  memoryEvents?: Array<{ stage: string; memory_id?: string; memory_ids?: string[]; decision?: string; reason?: string; relevance_score?: number; outcome?: string; reliability_score?: number; step_number?: number }>;
}

const ACTION_COLORS: Record<string, string> = {
  goto: "text-cyan-400 bg-cyan-500/10 border-cyan-500/20",
  click: "text-teal-400 bg-teal-500/10 border-teal-500/20",
  type: "text-violet-400 bg-violet-500/10 border-violet-500/20",
  scroll: "text-blue-400 bg-blue-500/10 border-blue-500/20",
  done: "text-emerald-400 bg-emerald-500/10 border-emerald-500/20",
  wait: "text-amber-400 bg-amber-500/10 border-amber-500/20",
  extract: "text-pink-400 bg-pink-500/10 border-pink-500/20",
};

function ActionBadge({ action }: { action: string }) {
  const cls = ACTION_COLORS[action?.toLowerCase()] ?? "text-slate-300 bg-slate-800 border-slate-700";
  return (
    <span className={`inline-flex items-center text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded border ${cls}`}>
      {action}
    </span>
  );
}

function StepDot({ status }: { status?: string }) {
  if (status === "success")
    return <CheckCircle2 className="w-4 h-4 text-emerald-400 shrink-0 drop-shadow-[0_0_6px_rgba(52,211,153,0.7)]" />;
  if (status === "failure")
    return <XCircle className="w-4 h-4 text-rose-400 shrink-0 drop-shadow-[0_0_6px_rgba(248,113,113,0.7)]" />;
  return (
    <span className="w-4 h-4 shrink-0 flex items-center justify-center">
      <span className="w-2 h-2 rounded-full bg-teal-400 animate-pulse shadow-[0_0_6px_rgba(20,184,166,0.8)]" />
    </span>
  );
}

export const RunView: React.FC<RunViewProps> = ({
  currentTier, steps, escalationLog, latestScreenshot, status, errorMessage, memoryEvents = [],
}) => {
  const [expandedStep, setExpandedStep] = useState<number | null>(null);
  const [fullImageModal, setFullImageModal] = useState<string | null>(null);

  const TIER_CLS: Record<string, string> = {
    FAST: "tier-fast",
    GENERAL: "tier-general",
    REASONING: "tier-reasoning",
  };

  return (
    <div className="w-full space-y-4">
      {/* ── Error banner ── */}
      {(status === "failed" || errorMessage) && (
        <div className="glass-bright border border-rose-500/30 rounded-2xl p-4 flex items-start gap-4 shadow-xl shadow-rose-500/10">
          <div className="p-2.5 rounded-xl bg-rose-500/15 text-rose-400 shrink-0">
            <AlertTriangle className="w-5 h-5" />
          </div>
          <div>
            <p className="font-bold text-sm text-white mb-1">Execution Halted</p>
            <p className="text-xs text-rose-300/80 leading-relaxed">
              {errorMessage ?? "The agent run failed or encountered an unrecoverable state."}
            </p>
          </div>
        </div>
      )}

      {/* ── Escalation banners ── */}
      {escalationLog.map((esc, i) => (
        <div key={i}
          className="glass-bright border border-amber-500/25 rounded-2xl p-4 flex items-start gap-4 slide-up">
          <div className="p-2.5 rounded-xl bg-amber-500/15 text-amber-400 shrink-0">
            <ArrowUpRight className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="font-bold text-sm text-white">Capability Escalation</span>
              <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/25">
                {esc.from_tier} → {esc.to_tier}
              </span>
            </div>
            <p className="text-xs text-slate-400">{esc.reason}</p>
          </div>
        </div>
      ))}

      {memoryEvents.length > 0 && (
        <div className="glass-bright rounded-2xl border border-violet-500/20 p-4">
          <div className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-violet-300">
            <Brain className="h-4 w-4" />Memory trace
          </div>
          <div className="space-y-2">
            {memoryEvents.slice(-8).reverse().map((event, index) => (
              <div key={`${event.stage}-${event.memory_id ?? event.memory_ids?.join("-")}-${index}`} className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-[11px]">
                <span className="font-semibold text-slate-200">{event.stage.replaceAll("_", " ")}</span>
                {event.decision && <span className="rounded border border-violet-500/25 bg-violet-500/10 px-1.5 py-0.5 text-[9px] font-bold text-violet-200">{event.decision}</span>}
                {event.memory_id && <span className="font-mono text-[9px] text-slate-600">{event.memory_id.slice(0, 8)}</span>}
                {event.memory_ids?.length ? <span className="font-mono text-[9px] text-slate-600">{event.memory_ids.length} memory(s)</span> : null}
                {event.reason && <span className="text-slate-400">{event.reason}</span>}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Timeline card ── */}
      <div className="glass-bright rounded-2xl border border-white/[0.08] overflow-hidden shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-white/[0.06]">
          <div className="flex items-center gap-3">
            <Activity className="w-4.5 h-4.5 text-teal-400" />
            <h3 className="font-bold text-sm text-white">Execution Timeline</h3>
            {steps.length > 0 && (
              <span className="text-[10px] font-semibold text-slate-500 bg-white/[0.05] px-2 py-0.5 rounded-full border border-white/[0.06]">
                {steps.length} step{steps.length !== 1 ? "s" : ""}
              </span>
            )}
          </div>
          <span className={`inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full border ${TIER_CLS[currentTier] ?? "tier-general"}`}>
            {currentTier}
          </span>
        </div>

        {/* Steps */}
        <div className="divide-y divide-white/[0.04]">
          {steps.length === 0 ? (
            <div className="py-14 text-center text-slate-600">
              <Terminal className="w-8 h-8 mx-auto mb-3 text-teal-500/30" />
              <p className="text-sm">Initializing browser session…</p>
              <div className="flex justify-center gap-1 mt-3">
                {[0, 1, 2].map(i => (
                  <span key={i} className="typing-dot w-1.5 h-1.5 rounded-full bg-teal-400/50"
                    style={{ animationDelay: `${i * 0.2}s` }} />
                ))}
              </div>
            </div>
          ) : (
            steps.map((st, idx) => {
              const isExpanded = expandedStep === st.step_number;
              const isLast = idx === steps.length - 1;

              return (
                <div key={st.step_number} className="slide-up hover:bg-white/[0.02] transition-colors">
                  <button
                    onClick={() => setExpandedStep(isExpanded ? null : st.step_number)}
                    className="w-full text-left px-5 py-3.5 flex items-center gap-4"
                  >
                    {/* Step index + connector */}
                    <div className={`relative flex flex-col items-center ${!isLast ? "step-line" : ""}`}>
                      <StepDot status={st.verification_status} />
                    </div>

                    {/* Step content */}
                    <div className="flex-1 min-w-0 flex items-center gap-3">
                      <span className="font-mono text-[10px] text-slate-600 shrink-0">#{st.step_number}</span>
                      {st.action?.action && <ActionBadge action={st.action.action} />}
                      <span className="text-xs text-slate-300 truncate font-medium">
                        {st.action?.url ? st.action.url
                          : st.action?.text ? `"${st.action.text.slice(0, 50)}"`
                            : st.thought?.split(".")[0] ?? "Processing…"}
                      </span>
                    </div>

                    {/* Right side badges */}
                    <div className="flex items-center gap-2 shrink-0">
                      {st.retry_count && st.retry_count > 0 ? (
                        <span className="text-[10px] px-2 py-0.5 rounded bg-amber-500/10 text-amber-400 border border-amber-500/20 font-bold">
                          ×{st.retry_count}
                        </span>
                      ) : null}
                      {isExpanded
                        ? <ChevronUp className="w-3.5 h-3.5 text-slate-600" />
                        : <ChevronDown className="w-3.5 h-3.5 text-slate-600" />}
                    </div>
                  </button>

                  {/* Expanded detail */}
                  {isExpanded && (
                    <div className="px-5 pb-4 pt-1 border-t border-white/[0.04] bg-black/20 space-y-3">
                      {st.thought && (
                        <div className="flex gap-2">
                          <MessageSquare className="w-3.5 h-3.5 text-violet-400 mt-0.5 shrink-0" />
                          <div>
                            <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Thought</p>
                            <p className="text-xs text-slate-300 leading-relaxed">{st.thought}</p>
                          </div>
                        </div>
                      )}
                      {st.action && (
                        <div className="flex gap-2">
                          <Code2 className="w-3.5 h-3.5 text-teal-400 mt-0.5 shrink-0" />
                          <div className="flex-1 min-w-0">
                            <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Action</p>
                            <pre className="text-[11px] text-slate-300 bg-black/40 p-2.5 rounded-lg overflow-x-auto border border-white/[0.05]">
                              {JSON.stringify(st.action, null, 2)}
                            </pre>
                          </div>
                        </div>
                      )}
                      {st.reason && (
                        <div className="flex gap-2">
                          <Clock className="w-3.5 h-3.5 text-slate-500 mt-0.5 shrink-0" />
                          <div>
                            <p className="text-[10px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Verification</p>
                            <p className="text-xs text-slate-400">{st.reason}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })
          )}
        </div>
      </div>

      {/* ── Full screenshot modal ── */}
      {fullImageModal && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/90 backdrop-blur-md cursor-zoom-out"
          onClick={() => setFullImageModal(null)}
        >
          <div className="max-w-5xl max-h-[90vh] overflow-auto rounded-2xl border border-white/10 shadow-2xl">
            <img
              src={`data:image/png;base64,${fullImageModal}`}
              alt="Full browser snapshot"
              className="w-full h-auto"
            />
          </div>
        </div>
      )}
    </div>
  );
};
