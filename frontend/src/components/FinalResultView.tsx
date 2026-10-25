"use client";

import React, { useState } from "react";
import {
  CheckCircle2, Copy, Check, ExternalLink,
  TrendingUp, Package, Globe, ChevronDown, ChevronUp,
} from "lucide-react";

interface FinalResultViewProps {
  result: any;
}

function ResultItem({ item, idx }: { item: any; idx: number }) {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(idx < 3);

  const copy = () => {
    navigator.clipboard.writeText(JSON.stringify(item, null, 2));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  // Detect common fields
  const title   = item.item ?? item.title ?? item.name ?? item.product ?? `Result ${idx + 1}`;
  const price   = item.price ?? item.cost ?? item.fare ?? item.rate ?? null;
  const source  = item.source ?? item.site ?? item.platform ?? null;
  const specs   = item.specs ?? item.description ?? item.details ?? null;
  const url     = item.url ?? item.link ?? null;

  return (
    <div
      className={`glass-bright border border-white/[0.08] rounded-2xl overflow-hidden slide-up card-hover transition-all`}
      style={{ animationDelay: `${idx * 0.05}s` }}
    >
      <div className="flex items-start gap-4 p-4">
        {/* Index badge */}
        <div className="shrink-0 w-8 h-8 rounded-lg bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-xs font-black text-teal-400">
          {idx + 1}
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-start justify-between gap-3">
            <p className="text-sm font-semibold text-white leading-snug flex-1">
              {String(title)}
            </p>
            {price && (
              <div className="shrink-0 flex flex-col items-end">
                <span className="text-lg font-black text-teal-300 leading-none">{price}</span>
              </div>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 mt-2">
            {source && (
              <span className="inline-flex items-center gap-1 text-[10px] text-slate-400 bg-white/[0.05] px-2 py-1 rounded border border-white/[0.07]">
                <Globe className="w-2.5 h-2.5 text-cyan-400" />
                {source}
              </span>
            )}
            {url && (
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-[10px] text-cyan-400 hover:text-cyan-300 transition-colors"
              >
                <ExternalLink className="w-2.5 h-2.5" />
                Open link
              </a>
            )}
          </div>

          {specs && (
            <p className="text-xs text-slate-500 mt-2 leading-relaxed line-clamp-2">{specs}</p>
          )}
        </div>

        {/* Actions */}
        <div className="flex items-start gap-1 shrink-0">
          <button
            onClick={copy}
            className="p-1.5 rounded-lg hover:bg-white/[0.06] text-slate-600 hover:text-slate-300 transition-all"
            title="Copy JSON"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-teal-400" /> : <Copy className="w-3.5 h-3.5" />}
          </button>
          <button
            onClick={() => setExpanded(v => !v)}
            className="p-1.5 rounded-lg hover:bg-white/[0.06] text-slate-600 hover:text-slate-300 transition-all"
          >
            {expanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {/* Expanded raw JSON */}
      {expanded && (
        <div className="border-t border-white/[0.05] bg-black/25 px-4 py-3">
          <pre className="text-[10px] text-slate-400 overflow-x-auto leading-relaxed">
            {JSON.stringify(item, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}

export const FinalResultView: React.FC<FinalResultViewProps> = ({ result }) => {
  const [allCopied, setAllCopied] = useState(false);

  const items: any[] = Array.isArray(result)
    ? result
    : typeof result === "object" && result !== null
    ? [result]
    : [{ value: String(result) }];

  const copyAll = () => {
    navigator.clipboard.writeText(JSON.stringify(result, null, 2));
    setAllCopied(true);
    setTimeout(() => setAllCopied(false), 1800);
  };

  return (
    <div className="w-full">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div className="flex items-center gap-3">
          <div className="p-2 rounded-xl bg-emerald-500/15 text-emerald-400 shadow-[0_0_20px_rgba(52,211,153,0.2)]">
            <CheckCircle2 className="w-5 h-5" />
          </div>
          <div>
            <h3 className="font-black text-base text-white">Task Completed</h3>
            <p className="text-xs text-slate-500">{items.length} result{items.length !== 1 ? "s" : ""} extracted</p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          <span className="hidden sm:flex items-center gap-1.5 text-[11px] text-slate-500 glass px-3 py-1.5 rounded-full border border-white/[0.06]">
            <TrendingUp className="w-3 h-3 text-teal-400" />
            {items.length} items
          </span>
          <button
            onClick={copyAll}
            className="flex items-center gap-1.5 text-xs glass px-3 py-2 rounded-xl border border-white/[0.08] text-slate-400 hover:text-white hover:border-teal-500/30 transition-all"
          >
            {allCopied ? <Check className="w-3.5 h-3.5 text-teal-400" /> : <Copy className="w-3.5 h-3.5" />}
            <span>{allCopied ? "Copied!" : "Copy all"}</span>
          </button>
        </div>
      </div>

      {/* Result grid */}
      <div className="grid gap-3 sm:grid-cols-1 lg:grid-cols-2">
        {items.map((item, idx) => (
          <ResultItem key={idx} item={item} idx={idx} />
        ))}
      </div>

      {items.length === 0 && (
        <div className="glass-bright rounded-2xl border border-white/[0.07] p-8 text-center text-slate-500 text-sm">
          <Package className="w-8 h-8 mx-auto mb-3 opacity-30" />
          No structured results were extracted.
        </div>
      )}
    </div>
  );
};
