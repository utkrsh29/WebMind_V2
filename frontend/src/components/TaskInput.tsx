"use client";

import React, { useState, useRef, useEffect } from "react";
import { Sparkles, ArrowRight, Sliders, Loader2, Zap, Cpu, Brain } from "lucide-react";

interface TaskInputProps {
  onSubmit: (query: string, forcedTier: string | null) => void;
  isLoading: boolean;
}

const EXAMPLE_TASKS = [
  "Find laptops under 70k with 16GB RAM",
  "Find a hotel in Goa for 2 nights under ₹5000",
  "Compare phone prices on 3 sites",
  "Book-style search for flights Pune to Delhi",
  "Fill the contact form on example.com",
];

const TIER_OPTIONS = [
  { value: "auto",      label: "Auto-Routing",  desc: "Deterministic policy",   icon: <Sliders className="w-3.5 h-3.5" /> },
  { value: "FAST",      label: "Fast Tier",      desc: "Low latency",            icon: <Zap className="w-3.5 h-3.5" /> },
  { value: "GENERAL",   label: "General Tier",   desc: "Balanced",               icon: <Cpu className="w-3.5 h-3.5" /> },
  { value: "REASONING", label: "Reasoning Tier", desc: "High depth",             icon: <Brain className="w-3.5 h-3.5" /> },
];

export const TaskInput: React.FC<TaskInputProps> = ({ onSubmit, isLoading }) => {
  const [query, setQuery]         = useState("");
  const [forcedTier, setForcedTier] = useState("auto");
  const [focused, setFocused]     = useState(false);
  const [showTierMenu, setShowTierMenu] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const tierRef     = useRef<HTMLDivElement>(null);

  // Auto-resize textarea
  useEffect(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
      textareaRef.current.style.height = Math.min(textareaRef.current.scrollHeight, 160) + "px";
    }
  }, [query]);

  // Close tier dropdown on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (tierRef.current && !tierRef.current.contains(e.target as Node)) {
        setShowTierMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isLoading) return;
    onSubmit(query.trim(), forcedTier === "auto" ? null : forcedTier);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit(e as any);
  };

  const selectedTier = TIER_OPTIONS.find(t => t.value === forcedTier) ?? TIER_OPTIONS[0];

  return (
    <div className="w-full max-w-4xl mx-auto">
      <form onSubmit={handleSubmit}>
        <div
          className={`relative glass-bright rounded-2xl transition-all duration-300 ${
            focused ? "glow-border-teal" : "border border-white/[0.08]"
          }`}
        >
          {/* Top bar with textarea */}
          <div className="flex items-start gap-4 p-4 pb-2">
            {/* Pulsing icon */}
            <div className={`mt-1 shrink-0 p-2.5 rounded-xl transition-all duration-300 ${
              isLoading
                ? "bg-teal-500/20 text-teal-400"
                : focused
                ? "bg-teal-500/15 text-teal-300"
                : "bg-white/[0.05] text-slate-500"
            }`}>
              {isLoading
                ? <Loader2 className="w-5 h-5 animate-spin" />
                : <Sparkles className={`w-5 h-5 ${focused ? "animate-pulse" : ""}`} />
              }
            </div>

            <textarea
              ref={textareaRef}
              id="task-input"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              onKeyDown={handleKeyDown}
              placeholder="Give WebMind any autonomous web task…"
              rows={1}
              disabled={isLoading}
              className="w-full bg-transparent text-white placeholder-slate-500 resize-none outline-none text-[15px] leading-relaxed pt-1.5 font-medium disabled:opacity-60"
              style={{ minHeight: "40px" }}
            />
          </div>

          {/* Bottom toolbar */}
          <div className="flex items-center justify-between gap-3 px-4 pb-4 pt-2 border-t border-white/[0.05] mt-1">
            {/* Tier selector */}
            <div ref={tierRef} className="relative">
              <button
                type="button"
                id="tier-selector"
                onClick={() => setShowTierMenu(v => !v)}
                disabled={isLoading}
                className="flex items-center gap-2 text-xs glass rounded-lg px-3 py-2 text-slate-400 hover:text-white border border-white/[0.07] hover:border-white/20 transition-all disabled:opacity-50"
              >
                <span className="text-teal-400">{selectedTier.icon}</span>
                <span className="font-medium">{selectedTier.label}</span>
                <span className="text-slate-600 hidden sm:inline">· {selectedTier.desc}</span>
                <span className="text-slate-600 text-[10px]">▾</span>
              </button>

              {showTierMenu && (
                <div className="absolute bottom-full mb-2 left-0 w-56 glass-bright border border-white/10 rounded-xl overflow-hidden shadow-2xl z-30">
                  {TIER_OPTIONS.map(opt => (
                    <button
                      key={opt.value}
                      type="button"
                      onClick={() => { setForcedTier(opt.value); setShowTierMenu(false); }}
                      className={`w-full flex items-center gap-3 px-4 py-3 text-left text-xs transition-all hover:bg-white/[0.06] ${
                        forcedTier === opt.value ? "bg-teal-500/10 text-teal-300" : "text-slate-400"
                      }`}
                    >
                      <span className={forcedTier === opt.value ? "text-teal-400" : "text-slate-500"}>
                        {opt.icon}
                      </span>
                      <div>
                        <div className="font-semibold text-inherit">{opt.label}</div>
                        <div className="text-slate-600 text-[10px]">{opt.desc}</div>
                      </div>
                      {forcedTier === opt.value && <span className="ml-auto text-teal-400">✓</span>}
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="flex items-center gap-2">
              <span className="hidden sm:inline text-[10px] text-slate-600">⌘↵ to run</span>
              <button
                id="execute-task-btn"
                type="submit"
                disabled={!query.trim() || isLoading}
                className="flex items-center gap-2 bg-gradient-to-r from-teal-500 to-cyan-500 hover:from-teal-400 hover:to-cyan-400 disabled:opacity-40 disabled:cursor-not-allowed text-slate-950 font-bold text-xs px-5 py-2.5 rounded-xl transition-all duration-200 shadow-lg shadow-teal-500/25 active:scale-95"
              >
                {isLoading ? (
                  <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    <span>Orchestrating…</span>
                  </>
                ) : (
                  <>
                    <span>Execute Task</span>
                    <ArrowRight className="w-3.5 h-3.5" />
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      </form>

      {/* Example chips */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <span className="text-[11px] text-slate-600 flex items-center gap-1 mr-0.5">
          <Sparkles className="w-3 h-3 text-amber-500/70" />
          Try:
        </span>
        {EXAMPLE_TASKS.map((chip, idx) => (
          <button
            key={idx}
            type="button"
            onClick={() => { setQuery(chip); textareaRef.current?.focus(); }}
            disabled={isLoading}
            className="text-[11px] glass border border-white/[0.07] hover:border-teal-500/30 hover:text-teal-300 text-slate-400 px-3 py-1.5 rounded-full transition-all duration-200 disabled:opacity-40 card-hover"
          >
            {chip}
          </button>
        ))}
      </div>
    </div>
  );
};
