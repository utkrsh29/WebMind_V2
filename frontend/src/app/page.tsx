"use client";

import React, { useState, useEffect, useRef } from "react";
import {
  Bot, BarChart2, Cpu, Zap, Brain, Shield,
  Activity, Layers, GitBranch, Eye,
} from "lucide-react";
import { TaskInput } from "@/components/TaskInput";
import { TaskProfileCard } from "@/components/TaskProfileCard";
import { RunView } from "@/components/RunView";
import { AskUserModal } from "@/components/AskUserModal";
import { FinalResultView } from "@/components/FinalResultView";
import { MetricsDashboard } from "@/components/MetricsDashboard";

/* ── Tier badge helper ─────────────────────────────────── */
const TIER_META: Record<string, { icon: React.ReactNode; cls: string; label: string }> = {
  FAST:      { icon: <Zap  className="w-3 h-3" />, cls: "tier-fast",      label: "FAST" },
  GENERAL:   { icon: <Cpu  className="w-3 h-3" />, cls: "tier-general",   label: "GENERAL" },
  REASONING: { icon: <Brain className="w-3 h-3"/>, cls: "tier-reasoning", label: "REASONING" },
};

function TierPill({ tier }: { tier: string }) {
  const m = TIER_META[tier] ?? TIER_META.GENERAL;
  return (
    <span className={`inline-flex items-center gap-1 text-[10px] font-bold uppercase tracking-widest px-2.5 py-1 rounded-full border ${m.cls}`}>
      {m.icon}{m.label}
    </span>
  );
}

/* ── Status indicator ─────────────────────────────────── */
function StatusDot({ status }: { status: string }) {
  const map: Record<string, string> = {
    idle:      "bg-slate-600",
    running:   "bg-teal-400 animate-pulse shadow-[0_0_8px_rgba(20,184,166,0.8)]",
    completed: "bg-emerald-400 shadow-[0_0_8px_rgba(52,211,153,0.6)]",
    failed:    "bg-red-400 shadow-[0_0_8px_rgba(248,113,113,0.6)]",
  };
  return <span className={`inline-block w-2 h-2 rounded-full ${map[status] ?? map.idle}`} />;
}

/* ── Feature pills in hero ─────────────────────────────── */
const FEATURES = [
  { icon: <GitBranch className="w-3.5 h-3.5" />, label: "Deterministic Routing" },
  { icon: <Eye        className="w-3.5 h-3.5" />, label: "Live Browser Verification" },
  { icon: <Layers     className="w-3.5 h-3.5" />, label: "Multi-Agent Orchestration" },
  { icon: <Shield     className="w-3.5 h-3.5" />, label: "Failure-Aware Recovery" },
  { icon: <Activity   className="w-3.5 h-3.5" />, label: "Real-time SSE Events" },
];

export default function Home() {
  const [activeTab, setActiveTab] = useState<"agent" | "metrics">("agent");
  const [taskId, setTaskId]       = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [status, setStatus]       = useState("idle");
  const [currentTier, setCurrentTier] = useState("FAST");
  const [taskProfile, setTaskProfile] = useState<any>(null);
  const [steps, setSteps]         = useState<any[]>([]);
  const [escalationLog, setEscalationLog] = useState<any[]>([]);
  const [latestScreenshot, setLatestScreenshot] = useState<string | null>(null);
  const [finalResult, setFinalResult] = useState<any>(null);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [askUserPrompt, setAskUserPrompt] = useState<string | null>(null);

  const eventSourceRef = useRef<EventSource | null>(null);

  const closeEventSource = () => {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
  };

  useEffect(() => () => closeEventSource(), []);

  const handleTaskSubmit = async (query: string, forcedTier: string | null) => {
    closeEventSource();
    setIsLoading(true);
    setStatus("running");
    setTaskProfile(null);
    setSteps([]);
    setEscalationLog([]);
    setLatestScreenshot(null);
    setFinalResult(null);
    setAskUserPrompt(null);
    setErrorMessage(null);
    setActiveTab("agent");

    try {
      const res = await fetch("/api/tasks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query, forced_tier: forcedTier }),
      });

      if (!res.ok) throw new Error(`Failed to create task: ${res.statusText}`);
      const data = await res.json();
      const newTaskId = data.task_id;
      setTaskId(newTaskId);

      const eventSource = new EventSource(`/api/tasks/${newTaskId}/events`);
      eventSourceRef.current = eventSource;

      eventSource.addEventListener("profile_created", (e: MessageEvent) =>
        setTaskProfile(JSON.parse(e.data))
      );
      eventSource.addEventListener("routed", (e: MessageEvent) =>
        setCurrentTier(JSON.parse(e.data).tier)
      );
      eventSource.addEventListener("step_started", (e: MessageEvent) => {
        const p = JSON.parse(e.data);
        if (p.screenshot_b64) setLatestScreenshot(p.screenshot_b64);
        setSteps(prev => prev.find(s => s.step_number === p.step_number) ? prev : [...prev, p]);
      });
      eventSource.addEventListener("action_planned", (e: MessageEvent) => {
        const p = JSON.parse(e.data);
        setSteps(prev => prev.map(s =>
          s.step_number === p.step_number
            ? { ...s, thought: p.thought, action: p.action, expected_state: p.expected_state }
            : s
        ));
      });
      eventSource.addEventListener("verification", (e: MessageEvent) => {
        const p = JSON.parse(e.data);
        if (p.screenshot_b64) setLatestScreenshot(p.screenshot_b64);
        setSteps(prev => prev.map(s =>
          s.step_number === p.step_number
            ? { ...s, verification_status: p.status, failure_type: p.failure_type, reason: p.reason }
            : s
        ));
      });
      eventSource.addEventListener("retry", (e: MessageEvent) => {
        const p = JSON.parse(e.data);
        setSteps(prev => prev.map(s =>
          s.step_number === p.step_number
            ? { ...s, retry_count: p.retry_count, reason: p.reason }
            : s
        ));
      });
      eventSource.addEventListener("escalated", (e: MessageEvent) => {
        const p = JSON.parse(e.data);
        setCurrentTier(p.to_tier);
        setEscalationLog(prev => [...prev, p]);
      });
      eventSource.addEventListener("ask_user", (e: MessageEvent) =>
        setAskUserPrompt(JSON.parse(e.data).prompt)
      );
      eventSource.addEventListener("result", (e: MessageEvent) => {
        setFinalResult(JSON.parse(e.data).result);
        setStatus("completed");
        setIsLoading(false);
        closeEventSource();
      });
      eventSource.addEventListener("error", (e: MessageEvent) => {
        let msg = "Task execution encountered an error.";
        try { const p = JSON.parse(e.data); msg = p.error_message ?? p.reason ?? msg; } catch {}
        setErrorMessage(msg);
        setStatus("failed");
        setIsLoading(false);
        closeEventSource();
      });
      eventSource.onerror = () => { setIsLoading(false); closeEventSource(); };

    } catch (err: any) {
      setErrorMessage(err.message ?? "Failed to initiate task.");
      setStatus("failed");
      setIsLoading(false);
    }
  };

  const handleAskUserConfirm = async (approved: boolean, userInput?: string) => {
    if (!taskId) return;
    setAskUserPrompt(null);
    try {
      await fetch(`/api/tasks/${taskId}/confirm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, user_input: userInput }),
      });
    } catch (e) { console.error("Confirm error:", e); }
  };

  const hasResult = finalResult || errorMessage;
  const isIdle    = status === "idle";

  return (
    <div className="relative min-h-screen flex flex-col">
      {/* ── Ambient background orbs ── */}
      <div className="pointer-events-none fixed inset-0 overflow-hidden z-0">
        <div className="absolute -top-40 left-1/2 -translate-x-1/2 w-[900px] h-[500px] rounded-full bg-teal-500/[0.06] blur-[120px]" />
        <div className="absolute top-1/3 -right-32 w-[500px] h-[500px] rounded-full bg-cyan-500/[0.04] blur-[100px]" />
        <div className="absolute bottom-1/4 -left-24 w-[400px] h-[400px] rounded-full bg-violet-500/[0.04] blur-[100px]" />
      </div>

      {/* ── Header ── */}
      <header className="sticky top-0 z-50 border-b border-white/[0.06] glass">
        <div className="max-w-7xl mx-auto px-4 md:px-8 py-3 flex items-center justify-between gap-4">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="relative">
              <div className="p-2.5 rounded-xl bg-gradient-to-br from-teal-400 to-cyan-500 shadow-lg shadow-teal-500/30">
                <Bot className="w-6 h-6 text-slate-950" strokeWidth={2.5} />
              </div>
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-black tracking-tight gradient-text">WebMind</h1>
                <span className="hidden sm:inline-flex text-[9px] font-bold uppercase tracking-widest bg-teal-500/10 text-teal-400 border border-teal-500/25 px-2 py-0.5 rounded-full">
                  v1.0
                </span>
              </div>
              <p className="hidden md:block text-[10px] text-slate-500 leading-tight mt-0.5">
                Failure-Aware Autonomous Web Agent
              </p>
            </div>
          </div>

          {/* Status strip */}
          {taskId && (
            <div className="hidden sm:flex items-center gap-3 glass rounded-full px-4 py-2 text-xs">
              <StatusDot status={status} />
              <span className="text-slate-400 capitalize">{status}</span>
              {status === "running" && <TierPill tier={currentTier} />}
              {steps.length > 0 && (
                <span className="text-slate-500">{steps.length} step{steps.length !== 1 ? "s" : ""}</span>
              )}
            </div>
          )}

          {/* Tab nav */}
          <nav className="flex items-center gap-1 glass rounded-xl p-1">
            {[
              { id: "agent",   icon: <Cpu className="w-3.5 h-3.5" />,      label: "Agent Studio" },
              { id: "metrics", icon: <BarChart2 className="w-3.5 h-3.5" />, label: "Metrics & Eval" },
            ].map(tab => (
              <button
                key={tab.id}
                id={`tab-${tab.id}`}
                onClick={() => setActiveTab(tab.id as any)}
                className={`flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-xs font-semibold transition-all duration-200 ${
                  activeTab === tab.id
                    ? "bg-gradient-to-r from-teal-500 to-cyan-500 text-slate-950 shadow-md shadow-teal-500/25"
                    : "text-slate-400 hover:text-white hover:bg-white/5"
                }`}
              >
                {tab.icon}
                <span className="hidden sm:inline">{tab.label}</span>
              </button>
            ))}
          </nav>
        </div>
      </header>

      {/* ── Main content ── */}
      <main className="relative z-10 flex-1 max-w-7xl w-full mx-auto px-4 md:px-8 py-10 flex flex-col gap-8">

        {activeTab === "agent" ? (
          <>
            {/* Hero — only shown when idle */}
            {isIdle && (
              <section className="text-center pt-6 pb-2">
                <div className="inline-flex items-center gap-2 glass rounded-full px-4 py-1.5 mb-5 text-[11px] text-teal-400 border border-teal-500/20">
                  <Activity className="w-3.5 h-3.5 animate-pulse" />
                  Real Playwright browser · Live verification · Gemma 26B reasoning
                </div>
                <h2 className="text-4xl md:text-5xl font-black tracking-tight text-white mb-3">
                  Give WebMind any{" "}
                  <span className="gradient-text">autonomous web task</span>
                </h2>
                <p className="text-slate-400 text-base max-w-2xl mx-auto mb-6 leading-relaxed">
                  It spins up a real browser, plans actions step-by-step, verifies each one against
                  expected browser state, self-corrects on failure, and returns structured results.
                </p>
                <div className="flex flex-wrap justify-center gap-2 mb-2">
                  {FEATURES.map(f => (
                    <span key={f.label}
                      className="inline-flex items-center gap-1.5 text-[11px] text-slate-400 bg-white/[0.04] border border-white/[0.07] px-3 py-1.5 rounded-full">
                      <span className="text-teal-400">{f.icon}</span>
                      {f.label}
                    </span>
                  ))}
                </div>
              </section>
            )}

            {/* Task input */}
            <div className={`${isIdle ? "" : "pt-2"}`}>
              <TaskInput onSubmit={handleTaskSubmit} isLoading={isLoading} />
            </div>

            {/* Live run layout: 2 cols when screenshot exists */}
            {taskId && (
              <div className={`grid gap-6 ${latestScreenshot ? "lg:grid-cols-[1fr_380px]" : "grid-cols-1"}`}>
                <div className="flex flex-col gap-5">
                  {taskProfile && <TaskProfileCard profile={taskProfile} />}
                  <RunView
                    currentTier={currentTier}
                    steps={steps}
                    escalationLog={escalationLog}
                    latestScreenshot={null}  /* shown in right column */
                    status={status}
                    errorMessage={errorMessage}
                  />
                </div>

                {/* Right column: live screenshot panel */}
                {latestScreenshot && (
                  <aside className="hidden lg:flex flex-col gap-4">
                    <div className="glass-bright rounded-2xl overflow-hidden border border-white/[0.08] sticky top-20">
                      <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06]">
                        <div className="flex gap-1.5">
                          <span className="w-2.5 h-2.5 rounded-full bg-red-500/70" />
                          <span className="w-2.5 h-2.5 rounded-full bg-amber-500/70" />
                          <span className="w-2.5 h-2.5 rounded-full bg-emerald-500/70" />
                        </div>
                        <span className="text-[10px] text-slate-500 font-mono ml-1">live browser</span>
                        {isLoading && (
                          <span className="ml-auto flex gap-1">
                            {[0,1,2].map(i => (
                              <span key={i} className={`typing-dot w-1.5 h-1.5 rounded-full bg-teal-400`}
                                style={{ animationDelay: `${i * 0.2}s` }} />
                            ))}
                          </span>
                        )}
                      </div>
                      <div className="relative scan-line">
                        <img
                          src={`data:image/png;base64,${latestScreenshot}`}
                          alt="Live browser screenshot"
                          className="w-full object-contain"
                        />
                      </div>
                      <div className="px-4 py-2 text-[10px] text-slate-500 flex items-center gap-2 border-t border-white/[0.05]">
                        <StatusDot status={status} />
                        Step {steps.length} · {currentTier} tier
                      </div>
                    </div>
                  </aside>
                )}
              </div>
            )}

            {/* Final result */}
            {finalResult && (
              <div className="slide-up">
                <FinalResultView result={finalResult} />
              </div>
            )}

            {/* Empty state hint */}
            {isIdle && !taskId && (
              <div className="flex flex-col items-center gap-2 py-12 text-slate-600 text-sm">
                <Bot className="w-10 h-10 opacity-20" />
                <span>Enter a task above to start</span>
              </div>
            )}
          </>
        ) : (
          <MetricsDashboard />
        )}
      </main>

      {/* ── Human-in-the-loop modal ── */}
      <AskUserModal
        isOpen={Boolean(askUserPrompt)}
        prompt={askUserPrompt ?? ""}
        onConfirm={handleAskUserConfirm}
      />

      {/* ── Footer ── */}
      <footer className="relative z-10 border-t border-white/[0.04] py-6">
        <div className="max-w-7xl mx-auto px-4 md:px-8 flex flex-wrap items-center justify-between gap-3 text-[11px] text-slate-600">
          <span>WebMind © 2026 — Playwright · LiteLLM · Gemma/Gemini · Deterministic Routing</span>
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            System online
          </span>
        </div>
      </footer>
    </div>
  );
}
