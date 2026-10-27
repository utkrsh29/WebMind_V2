# WebMind UI

The frontend is a React + TypeScript single-page app built with Vite, Tailwind CSS, lucide-react, and Zustand. Agent Studio creates real tasks through the WebMind API and subscribes to its SSE event stream. The separate Memory and Metrics & Eval pages read persisted data from the same API.

## Run locally

```bash
npm install
npm run dev
```

Open [http://localhost:3001](http://localhost:3001). The input is prefilled with “Find the price of the cheapest USB-C hub on example-store.com”. Run starts a backend task; observations, planned actions, verification outcomes, retries, experience-memory events, and final output stream into the UI. Stop sends the backend stop request and preserves the log. Memory and Metrics & Eval load saved records from the backend on port 8000.

To exercise the timer-based mock agent instead, set `VITE_USE_MOCK_AGENT=true` in the frontend environment before starting Vite. The default is the real backend.

Set `VITE_API_BASE_URL` at build time to point the historical-data pages at a different WebMind API. By default, the UI uses the current browser hostname on port 8000.

## Mock stream contract

The backend adapter maps the WebMind SSE events to Zustand state. The optional mock adapter is in `src/mock/mockAgentBackend.ts`; it emits a discriminated event stream consumed by the same store:

```ts
{ type: "status", status: "idle" | "running" | "completed" | "stopped" | "failed", tier?: string, step?: number }
{ type: "step", n: number, attempt: number, observation: string, action: string, result: "in_progress" | "success" | "failed", error?: string }
{ type: "memory", key: string, value: string }
{ type: "metrics", totalSteps: number, successfulSteps: number, failedSteps: number, elapsedSeconds: number, outcome: string }
{ type: "final", output: string }
```

## Connect a real agent backend

The real SSE mapping lives in `src/store/useWebMindStore.ts`: task creation uses `POST /api/tasks`, progress subscribes to `/api/tasks/{id}/events`, and stopping uses `POST /api/tasks/{id}/stop`. To point the frontend at another backend, set `VITE_API_BASE_URL` at build time. The UI doesn't assume that every browser action succeeds.
