import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.agent.memory import ExperienceMemory
from app.agent.worker import AgentWorker, WorkerExecutionOutcome
from app.browser.session import BrowserSession
from app.db import async_session_maker
from app.models import Step, Task, TaskProfileModel, TaskRun
from app.orchestrator.escalation import evaluate_escalation
from app.router.routing_policy import route_task
from app.router.task_profile import TaskProfile
from app.schemas import AgentTier

logger = logging.getLogger(__name__)


class OrchestrationManager:
    def __init__(self):
        # Map of task_id -> active AgentWorker
        self.active_workers: Dict[str, AgentWorker] = {}
        # Map of task_id -> list of async queue listeners for SSE streaming
        self.event_subscribers: Dict[str, List[asyncio.Queue]] = {}

    def subscribe(self, task_id: str) -> asyncio.Queue:
        """Subscribes an SSE or WebSocket client queue to task events."""
        q = asyncio.Queue()
        self.event_subscribers.setdefault(task_id, []).append(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        """Unsubscribes a client queue."""
        if task_id in self.event_subscribers:
            try:
                self.event_subscribers[task_id].remove(q)
                if not self.event_subscribers[task_id]:
                    del self.event_subscribers[task_id]
            except ValueError:
                pass

    async def broadcast_event(self, task_id: str, event_type: str, data: Dict[str, Any]) -> None:
        """Pushes an event payload to all active client queues for this task."""
        subscribers = self.event_subscribers.get(task_id, [])
        payload = {"event": event_type, "data": data, "timestamp": datetime.now(timezone.utc).isoformat()}
        for q in list(subscribers):
            try:
                q.put_nowait(payload)
            except Exception as e:
                logger.warning(f"Failed to queue event for subscriber: {e}")

    def confirm_user_action(self, task_id: str, approved: bool, user_input: Optional[str]) -> bool:
        """Resolves human confirmation on an active worker."""
        worker = self.active_workers.get(task_id)
        if worker:
            worker.provide_user_response(approved=approved, user_input=user_input)
            return True
        return False

    async def execute_task(
        self,
        task_id: str,
        query: str,
        forced_tier: Optional[AgentTier] = None,
    ) -> None:
        """Full orchestration lifecycle: Router -> Worker -> Verify -> Escalate -> Finish."""
        async with async_session_maker() as db_session:
            # 1. ROUTING
            profile: TaskProfile = await route_task(query, forced_tier=forced_tier)
            
            # Persist Task & Profile to DB
            task_record = await db_session.get(Task, task_id)
            if not task_record:
                task_record = Task(id=task_id, query=query, forced_tier=forced_tier, status="running")
                db_session.add(task_record)
            else:
                task_record.status = "running"

            db_profile = TaskProfileModel(
                task_id=task_id,
                task_type=profile.task_type,
                constraints=profile.constraints,
                target_sites=profile.target_sites,
                requires_comparison=profile.requires_comparison,
                requires_decision=profile.requires_decision,
                requires_extraction=profile.requires_extraction,
                requires_filtering=profile.requires_filtering,
                requires_form_fill=profile.requires_form_fill,
                expected_steps=profile.expected_steps,
                num_sites=profile.num_sites,
                num_constraints=profile.num_constraints,
                reasoning_requirement=profile.reasoning_requirement,
                routed_tier=profile.routed_tier,
                routing_reason=profile.routing_reason,
            )
            db_session.add(db_profile)
            await db_session.commit()

            # Broadcast profile and routing decision
            await self.broadcast_event(
                task_id,
                "profile_created",
                profile.model_dump(),
            )
            await self.broadcast_event(
                task_id,
                "routed",
                {"tier": profile.routed_tier, "reasons": profile.routing_reason},
            )

            # Retrieve experience memory hints
            experience_hints = await ExperienceMemory.retrieve_similar_experiences(
                db_session, query=query, top_k=3
            )

        # 2. START BROWSER & AGENT WORKER LOOP
        session = BrowserSession()
        await session.start()

        current_tier = profile.routed_tier
        escalation_count = 0
        carried_extracted_data: Dict[str, Any] = {}
        carried_visited_urls: List[str] = []
        failure_approaches: List[str] = []

        try:
            while True:
                # Create DB Run record
                async with async_session_maker() as db_session:
                    run_record = TaskRun(
                        task_id=task_id,
                        tier=current_tier,
                        status="running",
                    )
                    db_session.add(run_record)
                    await db_session.commit()
                    run_id = run_record.id

                # Define step event callback to stream and persist steps
                async def worker_event_handler(event_type: str, data: Dict[str, Any]) -> None:
                    await self.broadcast_event(task_id, event_type, data)
                    if event_type == "verification":
                        # Record step in database
                        async with async_session_maker() as db_session:
                            step = Step(
                                run_id=run_id,
                                step_number=data.get("step_number", 0),
                                tier=current_tier,
                                action={"status": data.get("status")},
                                verification_status=data.get("status", "unknown"),
                                failure_type=data.get("failure_type"),
                                actual_state={"reason": data.get("reason")},
                            )
                            db_session.add(step)
                            await db_session.commit()

                # Instantiate Worker
                worker = AgentWorker(
                    tier=current_tier,
                    task_query=query,
                    constraints=profile.constraints,
                    session=session,
                    event_callback=worker_event_handler,
                    experience_hints=experience_hints + failure_approaches,
                    initial_extracted_data=carried_extracted_data,
                    initial_visited_urls=carried_visited_urls,
                )
                self.active_workers[task_id] = worker

                # Run worker loop
                outcome: WorkerExecutionOutcome = await worker.run()

                # Update Run metrics in DB
                async with async_session_maker() as db_session:
                    run_obj = await db_session.get(TaskRun, run_id)
                    if run_obj:
                        run_obj.step_count = outcome.steps_executed
                        run_obj.prompt_tokens = outcome.token_usage.prompt_tokens
                        run_obj.completion_tokens = outcome.token_usage.completion_tokens
                        run_obj.total_tokens = outcome.token_usage.total_tokens
                        run_obj.completed_at = datetime.now(timezone.utc)

                    task_obj = await db_session.get(Task, task_id)

                    # 3. HANDLE OUTCOME
                    if outcome.success:
                        # Success! Finish task
                        if run_obj:
                            run_obj.status = "completed"
                        if task_obj:
                            task_obj.status = "completed"
                            task_obj.final_result = outcome.result
                        await db_session.commit()

                        # Store in experience memory
                        await ExperienceMemory.store_experience(
                            session=db_session,
                            query=query,
                            profile=profile,
                            tier=current_tier,
                            action_summary=f"Completed in {outcome.steps_executed} steps on {current_tier}",
                            outcome="success",
                            failure_types=[],
                        )
                        break

                    # Failed: evaluate escalation
                    decision = evaluate_escalation(
                        current_tier=current_tier,
                        failure_type=outcome.failure_type,
                        error_message=outcome.error_message,
                        extracted_data=outcome.extracted_data,
                        visited_urls=outcome.visited_urls,
                        previous_escalations=escalation_count,
                    )

                    if decision.should_escalate and decision.next_tier:
                        escalation_count += 1
                        if run_obj:
                            run_obj.status = "escalated"
                            run_obj.failure_type = outcome.failure_type
                            run_obj.error_message = outcome.error_message
                            run_obj.escalation_reason = decision.reason
                        await db_session.commit()

                        await self.broadcast_event(
                            task_id,
                            "escalated",
                            {
                                "from_tier": current_tier,
                                "to_tier": decision.next_tier,
                                "reason": decision.reason,
                                "escalation_count": escalation_count,
                            },
                        )

                        # State carry-over to next tier
                        current_tier = decision.next_tier
                        carried_extracted_data = outcome.extracted_data
                        carried_visited_urls = outcome.visited_urls
                        failure_approaches.append(f"Failed on {current_tier} with: {decision.reason}")
                        continue
                    else:
                        # Non-capability failure or escalation exhausted
                        if run_obj:
                            run_obj.status = "failed"
                            run_obj.failure_type = outcome.failure_type
                            run_obj.error_message = outcome.error_message
                        if task_obj:
                            task_obj.status = "failed"
                        await db_session.commit()

                        await self.broadcast_event(
                            task_id,
                            "error",
                            {
                                "failure_type": outcome.failure_type,
                                "error_message": outcome.error_message or decision.reason,
                            },
                        )

                        await ExperienceMemory.store_experience(
                            session=db_session,
                            query=query,
                            profile=profile,
                            tier=current_tier,
                            action_summary=f"Failed on {current_tier}: {outcome.error_message}",
                            outcome="failure",
                            failure_types=[outcome.failure_type] if outcome.failure_type else [],
                        )
                        break
        finally:
            self.active_workers.pop(task_id, None)
            await session.close()


# Singleton orchestrator
orchestrator = OrchestrationManager()
