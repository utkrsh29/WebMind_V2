import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.config import settings
from app.db import get_db, init_db
from app.models import Step, Task, TaskProfileModel, TaskRun
from app.orchestrator.orchestrator import orchestrator
from app.schemas import (
    MetricSummaryResponse,
    TaskConfirmRequest,
    TaskCreateRequest,
    TaskProfileSchema,
    TaskStatusResponse,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("webmind.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing WebMind database...")
    await init_db()
    logger.info("WebMind backend initialized successfully.")
    yield


app = FastAPI(
    title="WebMind API",
    description="Failure-Aware Autonomous AI Web Agent with Task-Aware Multi-Agent Orchestration",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS configuration for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    return {"status": "ok", "app": "WebMind", "provider": settings.LLM_PROVIDER}


@app.post("/api/tasks", response_model=Dict[str, str])
async def create_task(
    payload: TaskCreateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Creates a new task and schedules background execution."""
    task = Task(
        query=payload.query,
        forced_tier=payload.forced_tier,
        status="pending",
    )
    db.add(task)
    await db.commit()
    await db.refresh(task)

    # Launch autonomous orchestration in background
    background_tasks.add_task(
        orchestrator.execute_task,
        task_id=task.id,
        query=task.query,
        forced_tier=payload.forced_tier,
    )

    return {"task_id": task.id, "status": "pending"}


@app.get("/api/tasks/{task_id}")
async def get_task_details(
    task_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Retrieves full task details, profile, runs, and final result."""
    stmt = (
        select(Task)
        .where(Task.id == task_id)
        .options(selectinload(Task.profile), selectinload(Task.runs).selectinload(TaskRun.steps))
    )
    res = await db.execute(stmt)
    task = res.scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    profile_data = None
    if task.profile:
        profile_data = {
            "task_type": task.profile.task_type,
            "constraints": task.profile.constraints,
            "target_sites": task.profile.target_sites,
            "requires_comparison": task.profile.requires_comparison,
            "requires_decision": task.profile.requires_decision,
            "requires_extraction": task.profile.requires_extraction,
            "requires_filtering": task.profile.requires_filtering,
            "requires_form_fill": task.profile.requires_form_fill,
            "expected_steps": task.profile.expected_steps,
            "num_sites": task.profile.num_sites,
            "num_constraints": task.profile.num_constraints,
            "reasoning_requirement": task.profile.reasoning_requirement,
            "routed_tier": task.profile.routed_tier,
            "routing_reason": task.profile.routing_reason,
        }

    total_steps = sum(run.step_count for run in task.runs)
    escalations = sum(1 for run in task.runs if run.status == "escalated")
    current_tier = task.runs[-1].tier if task.runs else (task.profile.routed_tier if task.profile else None)

    runs_data = []
    for run in task.runs:
        runs_data.append({
            "id": run.id,
            "tier": run.tier,
            "status": run.status,
            "step_count": run.step_count,
            "total_tokens": run.total_tokens,
            "failure_type": run.failure_type,
            "error_message": run.error_message,
            "escalation_reason": run.escalation_reason,
            "steps": [
                {
                    "step_number": s.step_number,
                    "tier": s.tier,
                    "thought": s.thought,
                    "verification_status": s.verification_status,
                    "failure_type": s.failure_type,
                }
                for s in run.steps
            ],
        })

    return {
        "id": task.id,
        "query": task.query,
        "status": task.status,
        "forced_tier": task.forced_tier,
        "final_result": task.final_result,
        "profile": profile_data,
        "current_tier": current_tier,
        "step_count": total_steps,
        "escalation_count": escalations,
        "runs": runs_data,
        "created_at": task.created_at.isoformat(),
        "updated_at": task.updated_at.isoformat(),
    }


@app.get("/api/tasks/{task_id}/events")
async def task_events_stream(task_id: str):
    """Server-Sent Events (SSE) endpoint streaming real-time execution steps."""
    queue = orchestrator.subscribe(task_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            # Yield initial connection confirmation
            yield f"event: ping\ndata: {json.dumps({'message': 'Connected to WebMind event stream'})}\n\n"

            while True:
                # Wait for next event with a periodic heartbeat
                try:
                    event_payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"event: {event_payload['event']}\ndata: {json.dumps(event_payload['data'])}\n\n"
                    if event_payload["event"] in ("result", "error"):
                        break
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
        finally:
            orchestrator.unsubscribe(task_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.post("/api/tasks/{task_id}/confirm")
async def confirm_task_action(
    task_id: str,
    payload: TaskConfirmRequest,
):
    """Answers a waiting ask_user prompt / human confirmation."""
    success = orchestrator.confirm_user_action(
        task_id=task_id,
        approved=payload.approved,
        user_input=payload.user_input,
    )
    if not success:
        raise HTTPException(
            status_code=400,
            detail="Task is not currently waiting for user confirmation or is not active.",
        )
    return {"status": "confirmed", "approved": payload.approved}


@app.get("/api/tasks")
async def list_tasks(
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    """Returns task history ordered by most recent."""
    stmt = (
        select(Task)
        .options(selectinload(Task.profile), selectinload(Task.runs))
        .order_by(desc(Task.created_at))
        .limit(limit)
    )
    res = await db.execute(stmt)
    tasks = res.scalars().all()

    items = []
    for t in tasks:
        items.append({
            "id": t.id,
            "query": t.query,
            "status": t.status,
            "tier": t.runs[-1].tier if t.runs else (t.profile.routed_tier if t.profile else "FAST"),
            "final_result": t.final_result,
            "step_count": sum(r.step_count for r in t.runs),
            "created_at": t.created_at.isoformat(),
        })
    return {"tasks": items}


@app.get("/api/metrics", response_model=MetricSummaryResponse)
async def get_metrics(
    db: AsyncSession = Depends(get_db),
):
    """Calculates overall agent efficiency and failure-recovery performance metrics."""
    # Query tasks and runs
    tasks_res = await db.execute(select(Task).options(selectinload(Task.runs)))
    all_tasks = tasks_res.scalars().all()

    total_tasks = len(all_tasks)
    if total_tasks == 0:
        return MetricSummaryResponse(
            total_tasks=0,
            success_rate=0.0,
            avg_latency_seconds=0.0,
            total_tokens_used=0,
            escalation_rate=0.0,
            recovery_rate=0.0,
            action_efficiency=0.0,
            tier_distribution={"FAST": 0, "GENERAL": 0, "REASONING": 0},
        )

    completed_tasks = [t for t in all_tasks if t.status == "completed"]
    success_rate = round(len(completed_tasks) / total_tasks * 100.0, 1)

    total_tokens = 0
    total_steps = 0
    total_latency = 0.0
    escalated_runs_count = 0
    recovered_after_escalation = 0
    tier_counts = {"FAST": 0, "GENERAL": 0, "REASONING": 0}

    for t in all_tasks:
        task_had_escalation = any(r.status == "escalated" for r in t.runs)
        if task_had_escalation:
            escalated_runs_count += 1
            if t.status == "completed":
                recovered_after_escalation += 1

        for r in t.runs:
            total_tokens += (r.total_tokens or 0)
            total_steps += (r.step_count or 0)
            if r.tier in tier_counts:
                tier_counts[r.tier] += 1
            if r.completed_at and r.created_at:
                diff = (r.completed_at - r.created_at).total_seconds()
                if diff > 0:
                    total_latency += diff

    avg_latency = round(total_latency / max(total_tasks, 1), 2)
    escalation_rate = round(escalated_runs_count / max(total_tasks, 1) * 100.0, 1)
    recovery_rate = round(
        (recovered_after_escalation / max(escalated_runs_count, 1)) * 100.0 if escalated_runs_count > 0 else 100.0,
        1,
    )
    action_efficiency = round(total_steps / max(len(completed_tasks), 1), 2)

    return MetricSummaryResponse(
        total_tasks=total_tasks,
        success_rate=success_rate,
        avg_latency_seconds=avg_latency,
        total_tokens_used=total_tokens,
        escalation_rate=escalation_rate,
        recovery_rate=recovery_rate,
        action_efficiency=action_efficiency,
        tier_distribution=tier_counts,
    )
