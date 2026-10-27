"use client";

import React, { useCallback, useEffect, useState } from "react";
import { ArrowDown, ArrowUp, Check, ExternalLink, RefreshCw, X } from "lucide-react";

interface BrowserControl {
  element_id: number;
  tag: string;
  role: string;
  name: string;
  placeholder: string;
  input_type: string;
  disabled: boolean;
  checked: boolean;
  options: Array<{ label: string; value: string }>;
}

interface BrowserState {
  revision: number;
  url: string;
  title: string;
  screenshot_b64: string | null;
  controls: BrowserControl[];
}

interface Props {
  taskId: string;
  isLogin?: boolean;
  onResume: (note?: string) => void;
  onClose: () => void;
}

export const BrowserHandoffPanel: React.FC<Props> = ({ taskId, isLogin = false, onResume, onClose }) => {
  const [state, setState] = useState<BrowserState | null>(null);
  const [drafts, setDrafts] = useState<Record<number, string>>({});
  const [selections, setSelections] = useState<Record<number, string>>({});
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/tasks/${taskId}/browser-handoff`);
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail ?? `Could not open browser tab (${response.status}).`);
      setState(body);
      setDrafts({});
      setSelections({});
    } catch (err: any) {
      setError(err.message ?? "Could not open the browser tab.");
    } finally {
      setBusy(false);
    }
  }, [taskId]);

  useEffect(() => { void refresh(); }, [refresh]);

  const act = async (action: string, control?: BrowserControl, value?: string) => {
    if (!state) return;
    setBusy(true);
    setError("");
    try {
      const response = await fetch(`/api/tasks/${taskId}/browser-handoff/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          revision: state.revision,
          action,
          element_id: control?.element_id,
          text: action === "type" ? value : undefined,
          value: action === "select" ? value : undefined,
          direction: action === "scroll" ? value : undefined,
        }),
      });
      const body = await response.json();
      if (body.state) setState(body.state);
      if (!response.ok || !body.ok) setError(body.message ?? body.detail ?? "That action did not complete.");
      if (body.ok) {
        setDrafts(previous => {
          const next = { ...previous };
          if (control) delete next[control.element_id];
          return next;
        });
        setSelections(previous => {
          const next = { ...previous };
          if (control) delete next[control.element_id];
          return next;
        });
      }
    } catch (err: any) {
      setError(err.message ?? "Could not control the browser tab.");
    } finally {
      setBusy(false);
    }
  };

  const isTextControl = (control: BrowserControl) =>
    control.tag === "textarea" || control.role === "textbox" ||
    (control.tag === "input" && !["button", "submit", "reset", "checkbox", "radio", "file", "hidden"].includes(control.input_type));
  const isClickable = (control: BrowserControl) =>
    !isTextControl(control) && control.tag !== "select" && !control.disabled;

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-slate-950/90 p-3 backdrop-blur-md md:p-6">
      <section className="flex max-h-[94vh] w-full max-w-7xl flex-col overflow-hidden rounded-2xl border border-cyan-500/25 bg-slate-950 shadow-2xl">
        <header className="flex items-start justify-between gap-4 border-b border-white/[0.08] px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-base font-bold text-white">Take over the live browser tab</h2>
            <p className="mt-1 truncate text-xs text-slate-400">{state?.title || "Connecting to the paused browser…"}</p>
            {state?.url && <p className="mt-1 truncate font-mono text-[10px] text-slate-600">{state.url}</p>}
          </div>
          <button onClick={onClose} className="rounded-lg p-2 text-slate-500 hover:bg-white/5 hover:text-white" aria-label="Back to prompt"><X className="h-4 w-4" /></button>
        </header>

        <div className="grid min-h-0 flex-1 gap-4 overflow-auto p-4 lg:grid-cols-[minmax(0,1.35fr)_minmax(340px,0.9fr)]">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs font-semibold text-slate-300">Current page</p>
              <button onClick={() => void refresh()} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-2.5 py-1.5 text-[10px] text-slate-300 disabled:opacity-50"><RefreshCw className={`h-3 w-3 ${busy ? "animate-spin" : ""}`} />Refresh tab</button>
            </div>
            <div className="flex min-h-64 items-center justify-center overflow-hidden rounded-xl border border-white/[0.08] bg-black/40">
              {state?.screenshot_b64
                ? <img src={`data:image/jpeg;base64,${state.screenshot_b64}`} alt="Screenshot of the paused browser tab" className="max-h-[58vh] w-full object-contain" />
                : <p className="p-8 text-center text-xs text-slate-500">{busy ? "Loading the current page…" : "No page screenshot is available."}</p>}
            </div>
            <p className="text-[10px] leading-relaxed text-slate-500">Actions are applied to the paused Playwright tab. Field values are sent to the website but are not added to WebMind’s task trace or memory.</p>
          </div>

          <div className="flex min-h-0 flex-col">
            <p className="mb-2 text-xs font-semibold text-slate-300">Visible page controls</p>
            <div className="min-h-32 flex-1 space-y-2 overflow-auto rounded-xl border border-white/[0.06] bg-black/20 p-2">
              {state?.controls.length ? state.controls.map(control => (
                <div key={control.element_id} className="rounded-lg border border-white/[0.06] bg-white/[0.02] p-2.5">
                  <div className="mb-2 flex items-center gap-2">
                    <span className="rounded bg-slate-800 px-1.5 py-0.5 font-mono text-[9px] text-cyan-300">{control.element_id}</span>
                    <span className="min-w-0 flex-1 truncate text-[11px] text-slate-200">{control.name || control.placeholder || control.tag}</span>
                    <span className="text-[9px] uppercase text-slate-600">{control.input_type || control.role}</span>
                  </div>
                  {isTextControl(control) && !control.disabled ? (
                    <div className="flex gap-2">
                      <input
                        type={control.input_type === "password" ? "password" : "text"}
                        value={drafts[control.element_id] ?? ""}
                        onChange={event => setDrafts(current => ({ ...current, [control.element_id]: event.target.value }))}
                        placeholder={control.placeholder || `Enter ${control.name || "text"}`}
                        className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-xs text-white outline-none focus:border-cyan-500"
                      />
                      <button onClick={() => void act("type", control, drafts[control.element_id] ?? "")} disabled={busy || !(drafts[control.element_id] ?? "").length} className="rounded-lg bg-cyan-500/15 px-3 text-[10px] font-semibold text-cyan-200 disabled:opacity-40">Fill</button>
                    </div>
                  ) : control.tag === "select" && !control.disabled ? (
                    <div className="flex gap-2">
                      <select value={selections[control.element_id] ?? ""} onChange={event => setSelections(current => ({ ...current, [control.element_id]: event.target.value }))} className="min-w-0 flex-1 rounded-lg border border-slate-700 bg-slate-950 px-2.5 py-2 text-xs text-white">
                        <option value="">Choose an option</option>
                        {control.options.map((option, index) => <option key={`${option.value}-${index}`} value={option.value}>{option.label || option.value}</option>)}
                      </select>
                      <button onClick={() => void act("select", control, selections[control.element_id])} disabled={busy || selections[control.element_id] === undefined} className="rounded-lg bg-cyan-500/15 px-3 text-[10px] font-semibold text-cyan-200 disabled:opacity-40">Select</button>
                    </div>
                  ) : isClickable(control) ? (
                    <button onClick={() => void act("click", control)} disabled={busy} className="inline-flex items-center gap-1.5 rounded-lg border border-slate-700 px-3 py-1.5 text-[10px] text-slate-300 hover:border-cyan-500/50 hover:text-cyan-200 disabled:opacity-40"><ExternalLink className="h-3 w-3" />Click control</button>
                  ) : <span className="text-[10px] text-slate-600">Disabled or unsupported control</span>}
                </div>
              )) : <p className="p-4 text-center text-xs text-slate-600">No visible controls were detected. Refresh after the page changes.</p>}
            </div>
            <div className="mt-2 flex gap-2">
              <button onClick={() => void act("scroll", undefined, "up")} disabled={busy} className="flex-1 rounded-lg border border-slate-700 px-3 py-2 text-[10px] text-slate-300 disabled:opacity-40"><ArrowUp className="mr-1 inline h-3 w-3" />Scroll up</button>
              <button onClick={() => void act("scroll", undefined, "down")} disabled={busy} className="flex-1 rounded-lg border border-slate-700 px-3 py-2 text-[10px] text-slate-300 disabled:opacity-40"><ArrowDown className="mr-1 inline h-3 w-3" />Scroll down</button>
            </div>
          </div>
        </div>

        {error && <div role="status" className="mx-4 rounded-lg border border-amber-500/25 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{error}</div>}
        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-white/[0.08] px-4 py-3">
          {!isLogin && <input value={note} onChange={event => setNote(event.target.value)} placeholder="Optional note for WebMind before it resumes" className="min-w-[220px] flex-1 rounded-lg border border-slate-700 bg-slate-900 px-3 py-2 text-xs text-white outline-none focus:border-cyan-500" />}
          <button onClick={() => onResume(note)} disabled={busy} className="inline-flex items-center gap-2 rounded-lg bg-gradient-to-r from-cyan-400 to-teal-400 px-4 py-2 text-xs font-bold text-slate-950 disabled:cursor-not-allowed disabled:opacity-50"><Check className="h-4 w-4" />Done — resume agent</button>
        </footer>
      </section>
    </div>
  );
};
