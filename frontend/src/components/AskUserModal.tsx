"use client";

import React, { useEffect, useState } from "react";
import { ShieldAlert, Check, X, MonitorPlay } from "lucide-react";
import { BrowserHandoffPanel } from "@/components/BrowserHandoffPanel";

interface AskUserModalProps {
  prompt: string;
  isOpen: boolean;
  onConfirm: (approved: boolean, userInput?: string) => void;
  taskId?: string | null;
  isLogin?: boolean;
  canTakeOver?: boolean;
  requiresApproval?: boolean;
  latestScreenshot?: string | null;
}

export const AskUserModal: React.FC<AskUserModalProps> = ({
  prompt, isOpen, onConfirm, taskId, isLogin = false, canTakeOver = false, requiresApproval = false, latestScreenshot,
}) => {
  const [userInput, setUserInput] = useState("");
  const [takingOver, setTakingOver] = useState(false);

  useEffect(() => {
    setTakingOver(false);
    setUserInput("");
  }, [prompt, isOpen]);

  if (!isOpen) return null;
  if (takingOver && taskId) {
    return <BrowserHandoffPanel
      taskId={taskId}
      isLogin={isLogin}
      onClose={() => setTakingOver(false)}
      onResume={(note) => { setTakingOver(false); onConfirm(true, note || userInput); }}
    />;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-slate-950/80 backdrop-blur-md">
      <div className="glass-panel w-full max-w-lg rounded-2xl p-6 shadow-2xl border border-amber-500/30 animate-in fade-in zoom-in-95 duration-200">
        <div className="flex items-center gap-3 mb-4 text-amber-400">
          <div className="p-3 bg-amber-500/10 rounded-xl">
            <ShieldAlert className="w-7 h-7" />
          </div>
          <div>
            <h3 className="text-lg font-bold text-white">Human Confirmation Required</h3>
            <p className="text-xs text-amber-300/80">Safety guardrail paused execution</p>
          </div>
        </div>

        <div className="bg-slate-900/80 rounded-xl p-4 mb-4 border border-slate-800 text-sm text-slate-200 leading-relaxed">
          {prompt}
        </div>

        {latestScreenshot && (
          <div className="mb-4 max-h-48 overflow-hidden rounded-xl border border-white/[0.08] bg-black/30">
            <img src={`data:image/jpeg;base64,${latestScreenshot}`} alt="Current browser page for review" className="max-h-48 w-full object-contain" />
          </div>
        )}

        {isLogin ? (
          <p className="mb-5 rounded-xl border border-cyan-500/20 bg-cyan-500/[0.06] p-3 text-xs leading-relaxed text-cyan-100/80">
            Enter credentials directly in the website tab. WebMind won’t put them in the planner prompt or saved memory.
          </p>
        ) : (
          <div className="mb-5">
            <label className="block text-xs font-medium text-slate-400 mb-1.5">
              Requested information or response:
            </label>
            <input
              type="text"
              value={userInput}
              onChange={(e) => setUserInput(e.target.value)}
              placeholder="Type the requested details..."
              className="w-full bg-slate-900/90 border border-slate-700 rounded-xl px-3.5 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-amber-400"
            />
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-3">
          {canTakeOver && taskId ? (
            <button type="button" onClick={() => setTakingOver(true)} className="inline-flex items-center gap-2 rounded-xl border border-cyan-500/30 bg-cyan-500/10 px-3 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-500/20">
              <MonitorPlay className="h-4 w-4" />Take over browser tab
            </button>
          ) : <span />}
          <div className="flex items-center justify-end gap-3">
          <button
            type="button"
            onClick={() => onConfirm(false, userInput)}
            className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-300 transition-colors"
          >
            <X className="w-4 h-4 text-rose-400" />
            <span>Reject / Cancel</span>
          </button>

          <button
            type="button"
            onClick={() => onConfirm(true, userInput)}
            className="flex items-center gap-1.5 px-5 py-2 text-xs font-semibold rounded-xl bg-gradient-to-r from-amber-500 to-yellow-500 hover:from-amber-400 hover:to-yellow-400 text-slate-950 font-bold transition-transform active:scale-95 shadow-lg shadow-amber-500/20"
          >
            <Check className="w-4 h-4 text-slate-950" />
            <span>{requiresApproval ? "Approve & proceed" : isLogin ? "I’ve handled it — continue" : "Send response & continue"}</span>
          </button>
          </div>
        </div>
      </div>
    </div>
  );
};
