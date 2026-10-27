import asyncio
import json
import logging
import sys

# On Windows, enforce ProactorEventLoop so Playwright subprocesses work properly
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Dict, List
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.config import settings
from app.db import get_db, init_db
from app.models import ExperienceMemoryModel, Step, Task, TaskProfileModel, TaskRun
from app.agent.memory import ExperienceMemoryService
from app.models import MemoryAccessModel, MemoryEvidenceModel
from app.orchestrator.orchestrator import orchestrator
from app.schemas import (
    MetricSummaryResponse,
    HumanBrowserActionRequest,
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
    indexed = await ExperienceMemoryService().rebuild_vector_index()
    logger.info("ChromaDB startup backfill indexed %s existing memories.", indexed)
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


@app.get("/api/tasks/{task_id}/browser-handoff")
async def get_browser_handoff(task_id: str):
    """Expose a live page snapshot only while an active worker awaits a person."""
    worker = orchestrator.active_workers.get(task_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="No active browser task found.")
    try:
        return await worker.get_browser_handoff_state()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/browser-handoff/action")
async def perform_browser_handoff_action(task_id: str, payload: HumanBrowserActionRequest):
    """Perform a user-selected, observed browser action while the worker is paused."""
    worker = orchestrator.active_workers.get(task_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="No active browser task found.")
    try:
        result = await worker.perform_human_browser_action(payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.get("stale"):
        return JSONResponse(status_code=409, content=result)
    return result


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


@app.get("/api/memories")
async def list_memories(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    domain: str | None = None,
    task_type: str | None = None,
    outcome: str | None = None,
    min_reliability: float | None = Query(None, ge=0.0, le=1.0),
    max_reliability: float | None = Query(None, ge=0.0, le=1.0),
    db: AsyncSession = Depends(get_db),
):
    """List real persisted experience memories with filters and pagination."""
    filters = []
    if domain:
        filters.append(ExperienceMemoryModel.domain == domain.lower().removeprefix("www."))
    if task_type:
        filters.append(ExperienceMemoryModel.task_type == task_type)
    if outcome:
        filters.append(ExperienceMemoryModel.outcome == outcome)
    if min_reliability is not None:
        filters.append(ExperienceMemoryModel.reliability_score >= min_reliability)
    if max_reliability is not None:
        filters.append(ExperienceMemoryModel.reliability_score <= max_reliability)

    count_result = await db.execute(select(func.count()).select_from(ExperienceMemoryModel).where(*filters))
    total = int(count_result.scalar_one())
    rows_result = await db.execute(
        select(ExperienceMemoryModel)
        .where(*filters)
        .order_by(ExperienceMemoryModel.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    return {
        "items": [ExperienceMemoryService.serialize_memory(row) for row in rows_result.scalars().all()],
        "page": page,
        "page_size": page_size,
        "total": total,
    }


@app.get("/api/memories/{memory_id}")
async def get_memory_detail(memory_id: str, db: AsyncSession = Depends(get_db)):
    record = await db.get(ExperienceMemoryModel, memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    service = ExperienceMemoryService()
    return {
        **service.serialize_memory(record),
        "retrieval_history": await service.list_access_history(memory_id, db),
        "evidence_history": await service.list_evidence(memory_id, db),
    }


@app.delete("/api/memories/{memory_id}")
async def delete_memory(memory_id: str, db: AsyncSession = Depends(get_db)):
    record = await db.get(ExperienceMemoryModel, memory_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Memory not found")
    embedding_model = record.embedding_model
    await db.execute(delete(MemoryAccessModel).where(MemoryAccessModel.memory_id == memory_id))
    await db.execute(delete(MemoryEvidenceModel).where(MemoryEvidenceModel.memory_id == memory_id))
    await db.delete(record)
    await db.commit()
    await ExperienceMemoryService().delete_vector_record(memory_id, embedding_model)
    return {"deleted": memory_id}


@app.delete("/api/memories")
async def clear_memories(
    confirm: bool = Query(False, description="Must be true to permanently clear all memories"),
    db: AsyncSession = Depends(get_db),
):
    if not confirm:
        raise HTTPException(status_code=400, detail="Set confirm=true to clear all memories.")
    await db.execute(delete(MemoryAccessModel))
    await db.execute(delete(MemoryEvidenceModel))
    rows = await db.execute(select(ExperienceMemoryModel))
    count = 0
    for record in rows.scalars().all():
        await db.delete(record)
        count += 1
    await db.commit()
    await ExperienceMemoryService().clear_vector_index()
    return {"deleted_count": count}
