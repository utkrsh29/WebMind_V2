"use client";

import React, { useState } from "react";
import { ShieldAlert, Check, X } from "lucide-react";

interface AskUserModalProps {
  prompt: string;
  isOpen: boolean;
  onConfirm: (approved: boolean, userInput?: string) => void;
}

export const AskUserModal: React.FC<AskUserModalProps> = ({ prompt, isOpen, onConfirm }) => {
  const [userInput, setUserInput] = useState("");

  if (!isOpen) return null;

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

        <div className="mb-5">
          <label className="block text-xs font-medium text-slate-400 mb-1.5">
            Optional response or requested details:
          </label>
          <input
            type="text"
            value={userInput}
            onChange={(e) => setUserInput(e.target.value)}
            placeholder="Type your response or confirmation input..."
            className="w-full bg-slate-900/90 border border-slate-700 rounded-xl px-3.5 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-amber-400"
          />
        </div>

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
            <span>Approve & Proceed</span>
          </button>
        </div>
      </div>
    </div>
  );
};
