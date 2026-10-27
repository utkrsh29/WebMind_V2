import type { ReactNode } from "react";
import { BarChart3, Bot, BrainCircuit, Cpu, Hash } from "lucide-react";
import { useWebMindStore, type WebMindPage } from "../store/useWebMindStore";
import { StatusBadge } from "./StatusBadge";

export function Header() {
  const page = useWebMindStore((state) => state.page);
  const status = useWebMindStore((state) => state.status);
  const tier = useWebMindStore((state) => state.tier);
  const currentStep = useWebMindStore((state) => state.currentStep);
  const setPage = useWebMindStore((state) => state.setPage);
  const pages: { id: WebMindPage; label: string; icon: ReactNode }[] = [
    { id: "agent", label: "Agent Studio", icon: <Cpu size={14} /> },
    { id: "memory", label: "Memory", icon: <BrainCircuit size={14} /> },
    { id: "metrics", label: "Metrics & Eval", icon: <BarChart3 size={14} /> },
  ];

  return (
    <header className="header-bar">
      <a className="brand" href="#top" aria-label="WebMind home">
        <span className="brand-mark"><Bot size={21} strokeWidth={2.1} /></span>
        <span className="brand-word">WebMind</span>
      </a>
      <nav className="page-nav" aria-label="WebMind pages">
        {pages.map((item) => (
          <button
            className={`page-nav-button ${page === item.id ? "page-nav-active" : ""}`}
            type="button"
            key={item.id}
            aria-current={page === item.id ? "page" : undefined}
            onClick={() => setPage(item.id)}
          >
            {item.icon}<span>{item.label}</span>
          </button>
        ))}
      </nav>
      <div className="header-state">
        <StatusBadge status={status} />
        <span className="header-meta"><BrainCircuit size={14} /> {tier}</span>
        <span className="header-meta step-counter"><Hash size={14} /> Step {currentStep}</span>
      </div>
    </header>
  );
}
