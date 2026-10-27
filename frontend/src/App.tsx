import { Header } from "./components/Header";
import { TaskInput } from "./components/TaskInput";
import { AgentStudio } from "./components/AgentStudio";
import { StopButton } from "./components/StopButton";
import { MemoryPanel } from "./components/MemoryPanel";
import { MetricsPanel } from "./components/MetricsPanel";
import { FinalResult } from "./components/FinalResult";
import { useWebMindStore } from "./store/useWebMindStore";

export default function App() {
  const page = useWebMindStore((state) => state.page);

  return (
    <div className="app-shell" id="top">
      <Header />
      <main className="main-column">
        {page === "agent" ? (
          <>
            <TaskInput />
            <AgentStudio />
            <StopButton />
            <FinalResult />
          </>
        ) : page === "memory" ? <MemoryPanel /> : <MetricsPanel />}
      </main>
    </div>
  );
}
