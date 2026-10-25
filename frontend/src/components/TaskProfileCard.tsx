"use client";

import React from "react";
import { Cpu, CheckCircle2, AlertTriangle, Compass, ListFilter, Layers } from "lucide-react";

interface TaskProfileProps {
  profile: {
    task_type: string;
    constraints: Record<string, any>;
    target_sites: string[];
    requires_comparison: boolean;
    requires_decision: boolean;
    requires_extraction: boolean;
    requires_filtering: boolean;
    requires_form_fill: boolean;
    expected_steps: string;
    num_sites: number;
    num_constraints: number;
    reasoning_requirement: string;
    routed_tier: string;
    routing_reason: string[];
  } | null;
}

export const TaskProfileCard: React.FC<TaskProfileProps> = ({ profile }) => {
  if (!profile) return null;

  const tierColors: Record<string, { bg: string; text: string; border: string }> = {
    FAST: { bg: "bg-blue-500/10", text: "text-blue-400", border: "border-blue-500/30" },
    GENERAL: { bg: "bg-amber-500/10", text: "text-amber-400", border: "border-amber-500/30" },
    REASONING: { bg: "bg-purple-500/10", text: "text-purple-400", border: "border-purple-500/30" },
  };

  const currentColors = tierColors[profile.routed_tier] || tierColors.FAST;

  return (
    <div className="glass-panel rounded-2xl p-5 mb-6 shadow-xl">
      <div className="flex flex-wrap items-center justify-between gap-4 mb-4 pb-3 border-b border-slate-800">
        <div className="flex items-center gap-2">
          <Cpu className="w-5 h-5 text-teal-400" />
          <h3 className="font-semibold text-slate-100 text-base">Deterministic Task Profile</h3>
          <span className="text-xs bg-slate-800 px-2.5 py-0.5 rounded-full text-slate-400 border border-slate-700">
            Type: {profile.task_type.toUpperCase()}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-400">Deterministic Tier:</span>
          <span
            className={`text-xs font-bold px-3 py-1 rounded-full border ${currentColors.bg} ${currentColors.text} ${currentColors.border}`}
          >
            {profile.routed_tier}
          </span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-xs">
        {/* Constraints */}
        <div className="bg-slate-900/50 p-3 rounded-xl border border-slate-800/80">
          <span className="text-slate-400 font-medium flex items-center gap-1.5 mb-2">
            <ListFilter className="w-3.5 h-3.5 text-teal-400" /> Extracted Constraints ({profile.num_constraints}):
          </span>
          {Object.keys(profile.constraints).length > 0 ? (
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(profile.constraints).map(([k, v], idx) => (
                <span
                  key={idx}
                  className="bg-slate-800/80 text-teal-300 border border-teal-500/20 px-2 py-0.5 rounded-md"
                >
                  <span className="text-slate-400">{k}:</span> {String(v)}
                </span>
              ))}
            </div>
          ) : (
            <span className="text-slate-400 italic">None specified</span>
          )}
        </div>

        {/* Requirements & Target Sites */}
        <div className="bg-slate-900/50 p-3 rounded-xl border border-slate-800/80">
          <span className="text-slate-400 font-medium flex items-center gap-1.5 mb-2">
            <Compass className="w-3.5 h-3.5 text-cyan-400" /> Scope & Targets:
          </span>
          <div className="space-y-1 text-slate-300">
            <div>
              <span className="text-slate-400">Sites ({profile.num_sites}):</span>{" "}
              {profile.target_sites.join(", ")}
            </div>
            <div>
              <span className="text-slate-400">Steps Complexity:</span>{" "}
              <span className="capitalize">{profile.expected_steps}</span>
            </div>
            <div>
              <span className="text-slate-400">Reasoning Need:</span>{" "}
              <span className="capitalize">{profile.reasoning_requirement}</span>
            </div>
          </div>
        </div>

        {/* Feature Flags */}
        <div className="bg-slate-900/50 p-3 rounded-xl border border-slate-800/80">
          <span className="text-slate-400 font-medium flex items-center gap-1.5 mb-2">
            <Layers className="w-3.5 h-3.5 text-purple-400" /> Functional Requirements:
          </span>
          <div className="flex flex-wrap gap-1.5">
            {profile.requires_comparison && (
              <span className="bg-purple-500/10 text-purple-300 border border-purple-500/30 px-2 py-0.5 rounded">
                Comparison
              </span>
            )}
            {profile.requires_filtering && (
              <span className="bg-blue-500/10 text-blue-300 border border-blue-500/30 px-2 py-0.5 rounded">
                Filtering
              </span>
            )}
            {profile.requires_form_fill && (
              <span className="bg-amber-500/10 text-amber-300 border border-amber-500/30 px-2 py-0.5 rounded">
                Form Fill
              </span>
            )}
            {profile.requires_decision && (
              <span className="bg-rose-500/10 text-rose-300 border border-rose-500/30 px-2 py-0.5 rounded">
                Decision Making
              </span>
            )}
            {profile.requires_extraction && (
              <span className="bg-emerald-500/10 text-emerald-300 border border-emerald-500/30 px-2 py-0.5 rounded">
                Structured Extraction
              </span>
            )}
          </div>
        </div>
      </div>

      {/* Routing Reason Explanations */}
      {profile.routing_reason && profile.routing_reason.length > 0 && (
        <div className="mt-3 pt-3 border-t border-slate-800/80 flex items-start gap-2 text-xs">
          <span className="text-slate-400 whitespace-nowrap font-medium">Routing Trigger:</span>
          <div className="flex flex-wrap gap-2">
            {profile.routing_reason.map((r, i) => (
              <span key={i} className="text-slate-300 bg-slate-800/60 px-2 py-0.5 rounded border border-slate-700/60">
                • {r}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
