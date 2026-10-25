# WebMind: Failure-Aware Autonomous AI Web Agent

WebMind is a generic, failure-aware autonomous AI web agent with task-aware multi-agent orchestration, deterministic tier routing (Fast, General, Reasoning), an observe-plan-act-verify browser loop, and verification-driven capability escalation.

---

## Architecture Overview

```mermaid
graph TD
    User["User / Web UI (Next.js App Router)"] -->|"Task Query"| API["FastAPI Backend (/api/tasks)"]
    API -->|"Task Text"| Router["Deterministic Task Router"]
    
    subgraph Router_Pipeline ["Task Router Pipeline"]
        Normalizer["Task Normalizer (FAST_MODEL + Fallback)"] --> Extractor["Feature Extractor (Pure Python)"]
        Extractor --> Profile["Task Profile (Pydantic)"]
        Profile --> Policy["Routing Policy (policy.yaml)"]
        Policy -->|"Tier + Reason"| TierSelect["Selected Tier: {FAST, GENERAL, REASONING}"]
    end
    
    Router --> Orchestrator["Agent Orchestrator"]
    Orchestrator --> Worker["Agent Worker"]
    
    subgraph Worker_Loop ["Agent Worker Loop"]
        Observe["Observe (DOM Snapshot + Set-of-Marks)"] --> Plan["Planner (LiteLLM + Experience Memory)"]
        Plan --> Act["Act (Playwright Action Space)"]
        Act --> Verify["Verify (Browser State vs Expected State)"]
        Verify -->|"Success"| Result["Return Result / Next Step"]
        Verify -->|"Failure (Capability Error)"| Escalate["Escalate Tier (FAST -> GENERAL -> REASONING)"]
        Verify -->|"Failure (Retryable)"| Recover["Replan & Recover"]
    end
    
    Worker --> Browser["Playwright Chromium (Docker or Local)"]
    Worker --> Memory["Experience Memory (Postgres + pgvector / SentenceTransformers)"]
    Worker -->|"SSE Stream"| UI["Live Execution Timeline & Perception Viewer"]
```

---

## Key Features

1. **Deterministic Task Routing**: Rules and thresholds defined in `policy.yaml` route queries deterministically to `FAST`, `GENERAL`, or `REASONING` agents based on constraints, comparison needs, target site count, and expected steps. Every routing decision produces an interpretable audit reason.
2. **Generic Browser Loop**: No hardcoded scrapers. Uses an observe-plan-act-verify loop driven by Playwright with Set-of-Marks visual indexing and machine-verifiable expected browser states.
3. **Capability-Driven Escalation**: When verification detects reasoning errors, loops, or step budget exhaustion, WebMind automatically escalates `FAST -> GENERAL -> REASONING`, carrying over visited URLs, extracted data, and failed approaches.
4. **Human-in-the-Loop Safety**: Irreversible actions (purchases, payments) and sensitive fields (credit cards) are halted by automated safety guardrails and routed to an interactive user approval dialog (`ask_user`).
5. **Universal Provider Support**: Uses LiteLLM to seamlessly route to Gemini, OpenAI, or Anthropic models with a single API key.
6. **Experience Memory**: Past successful and failed runs are indexed via vector embeddings and retrieved to inject short tactical hints into the planner prompt.

---

## Getting Started

### 1. Prerequisites
- Docker & Docker Compose (or Python 3.11+ and Node.js 20+ for local development)
- An API key for your chosen LLM provider (Google Gemini, OpenAI, or Anthropic)

### 2. Configure Environment
Copy `.env.example` to `.env`:
```bash
cp .env.example .env
```
Set your `LLM_API_KEY` in `.env`:
```env
LLM_API_KEY="your-api-key-here"
LLM_PROVIDER="gemini"
FAST_MODEL="gemini/gemini-2.5-flash"
GENERAL_MODEL="gemini/gemini-2.5-flash"
REASONING_MODEL="gemini/gemini-2.5-pro"
VLM_MODEL="gemini/gemini-2.5-flash"
DATABASE_URL="postgresql+asyncpg://webmind:webmind@db:5432/webmind"
```

### 3. Run with Docker Compose
```bash
docker compose up --build
```
- Frontend UI: `http://localhost:3000`
- Backend API Docs: `http://localhost:8000/docs`

### 4. Running Locally without Docker
If running natively on the host:
```bash
# Backend
cd backend
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Frontend
cd frontend
npm install
npm run dev
```

---

## Tuning Routing Policy (`policy.yaml`)

Routing rules are defined declaratively in `backend/app/router/policy.yaml`:
```yaml
thresholds:
  reasoning:
    min_constraints: 3
    require_comparison: true
    require_multi_site: true
    require_decision: true
    task_types:
      - comparison
      - multi_site
    reasoning_requirement: high

  general:
    min_constraints: 1
    require_filtering: true
    require_form_fill: true
    require_extraction: true
    task_types:
      - form_fill
      - filtering
      - extraction
      - navigation
    expected_steps:
      - medium

  fast:
    fallback_tier: FAST
```
- To make WebMind use the `REASONING` tier on queries with fewer constraints, lower `min_constraints` to `2`.
- Every routing decision records the exact triggers that activated the tier in the database and streams them to the UI.

---

## Evaluation Benchmark Harness

WebMind includes a 20-task benchmark suite testing:
- **Config A**: Fixed Fast Agent (no escalation)
- **Config B**: Fixed Reasoning Agent (no escalation)
- **Config C**: WebMind Task-Aware Adaptive Routing (with escalation)

To run the evaluation:
```bash
python -m app.eval.run_eval --config all --mock
```
Output results are exported to `app/eval/eval_results.csv` and rendered on the **Metrics & Eval** dashboard.

---

## Limitations & Safety Guardrails
- **CAPTCHA & Login Walls**: WebMind does not attempt to bypass CAPTCHAs, Cloudflare checks, or auth paywalls; it flags a `blocked` failure and pauses for user intervention.
- **Financial Transactions**: All purchase/payment buttons trigger an `ask_user` human approval prompt.
- **Credit Card Data**: The action executor prohibits entering 13-16 digit numbers matching card patterns.
