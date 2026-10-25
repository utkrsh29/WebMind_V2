import asyncio
import base64
import logging
from typing import Any, Callable, Coroutine, Dict, List, Optional
from app.agent.actions import execute_action
from app.agent.llm import TokenUsage
from app.agent.planner import Planner
from app.agent.verifier import BrowserVerifier, VerificationResult
from app.browser.dom_snapshot import DOMSnapshot, capture_dom_snapshot
from app.browser.session import BrowserSession
from app.config import settings
from app.schemas import AgentTier, FailureType, PlannerOutputSchema

logger = logging.getLogger(__name__)

# Step budgets per tier
TIER_STEP_BUDGETS = {
    "FAST": 12,
    "GENERAL": 25,
    "REASONING": 40,
}


class WorkerExecutionOutcome:
    def __init__(
        self,
        success: bool,
        status: str,  # "completed" | "failed" | "waiting_user"
        result: Optional[Any] = None,
        failure_type: Optional[FailureType] = None,
        error_message: Optional[str] = None,
        steps_executed: int = 0,
        token_usage: Optional[TokenUsage] = None,
        extracted_data: Optional[Dict[str, Any]] = None,
        visited_urls: Optional[List[str]] = None,
    ):
        self.success = success
        self.status = status
        self.result = result
        self.failure_type = failure_type
        self.error_message = error_message
        self.steps_executed = steps_executed
        self.token_usage = token_usage or TokenUsage()
        self.extracted_data = extracted_data or {}
        self.visited_urls = visited_urls or []


class AgentWorker:
    def __init__(
        self,
        tier: AgentTier,
        task_query: str,
        constraints: Dict[str, Any],
        session: BrowserSession,
        event_callback: Optional[Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]] = None,
        experience_hints: Optional[List[str]] = None,
        initial_extracted_data: Optional[Dict[str, Any]] = None,
        initial_visited_urls: Optional[List[str]] = None,
    ):
        self.tier = tier
        self.task_query = task_query
        self.constraints = constraints
        self.session = session
        self.event_callback = event_callback
        self.experience_hints = experience_hints or []
        self.extracted_data = initial_extracted_data or {}
        self.visited_urls = initial_visited_urls or []

        self.planner = Planner(tier=tier)
        self.verifier = BrowserVerifier()
        self.total_usage = TokenUsage()
        self.action_history: List[Dict[str, Any]] = []
        self.max_steps = TIER_STEP_BUDGETS.get(tier, settings.MAX_STEPS_PER_RUN)

        # Human-in-the-loop wait event
        self._user_response_future: Optional[asyncio.Future] = None

    async def emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Helper to stream events to websocket/SSE listeners."""
        if self.event_callback:
            try:
                await self.event_callback(event_type, data)
            except Exception as e:
                logger.warning(f"Error in event callback: {e}")

    def provide_user_response(self, approved: bool, user_input: Optional[str]) -> None:
        """Resolves waiting human-in-the-loop response."""
        if self._user_response_future and not self._user_response_future.done():
            self._user_response_future.set_result({"approved": approved, "user_input": user_input})

    async def run(self) -> WorkerExecutionOutcome:
        """Main Observe -> Plan -> Act -> Verify loop."""
        step_number = 0
        last_failure_reason: Optional[str] = None
        consecutive_failures = 0

        logger.info(f"Starting AgentWorker on tier {self.tier} (budget: {self.max_steps} steps)")

        while step_number < self.max_steps:
            step_number += 1
            retry_count = 0

            while retry_count <= settings.MAX_RETRIES_PER_STEP:
                # 1. OBSERVE
                current_snapshot = await capture_dom_snapshot(
                    self.session.page,
                    include_som_screenshot=True,
                )

                if current_snapshot.url not in self.visited_urls:
                    self.visited_urls.append(current_snapshot.url)

                screenshot_b64 = None
                if current_snapshot.screenshot_bytes:
                    screenshot_b64 = base64.b64encode(current_snapshot.screenshot_bytes).decode("utf-8")

                await self.emit_event(
                    "step_started",
                    {
                        "step_number": step_number,
                        "tier": self.tier,
                        "url": current_snapshot.url,
                        "title": current_snapshot.title,
                        "screenshot_b64": screenshot_b64,
                        "retry_count": retry_count,
                    },
                )

                # 2. PLAN
                try:
                    plan_output, usage = await self.planner.plan_next_step(
                        task_query=self.task_query,
                        constraints=self.constraints,
                        step_number=step_number,
                        current_snapshot=current_snapshot,
                        action_history=self.action_history,
                        extracted_data=self.extracted_data,
                        experience_hints=self.experience_hints,
                        last_failure_reason=last_failure_reason,
                    )
                    self.total_usage.add(usage)
                except Exception as e:
                    err_str = str(e).lower()
                    is_quota = any(k in err_str for k in ["rate_limit", "ratelimit", "quota", "429", "resource_exhausted"])
                    failure_type = "quota_exceeded" if is_quota else "reasoning_error"
                    logger.error(f"Planner error at step {step_number} ({failure_type}): {e}")
                    return WorkerExecutionOutcome(
                        success=False,
                        status="failed",
                        failure_type=failure_type,
                        error_message=f"Planner model failed: {str(e)}",
                        steps_executed=step_number,
                        token_usage=self.total_usage,
                        extracted_data=self.extracted_data,
                        visited_urls=self.visited_urls,
                    )

                action = plan_output.next_action
                thought = plan_output.thought
                expected_state = plan_output.expected_state

                await self.emit_event(
                    "action_planned",
                    {
                        "step_number": step_number,
                        "thought": thought,
                        "action": action.model_dump(),
                        "expected_state": expected_state.model_dump() if expected_state else None,
                    },
                )

                # 3. ACT
                action_result = await execute_action(
                    session=self.session,
                    action=action,
                    extracted_store=self.extracted_data,
                )

                # Check if ask_user confirmation is needed
                if action_result.ask_user_prompt:
                    await self.emit_event(
                        "ask_user",
                        {"prompt": action_result.ask_user_prompt, "step_number": step_number},
                    )
                    loop = asyncio.get_running_loop()
                    self._user_response_future = loop.create_future()
                    try:
                        # Wait up to 5 minutes for human answer
                        user_ans = await asyncio.wait_for(self._user_response_future, timeout=300.0)
                        if not user_ans.get("approved"):
                            return WorkerExecutionOutcome(
                                success=False,
                                status="cancelled",
                                error_message="User declined action approval.",
                                steps_executed=step_number,
                                token_usage=self.total_usage,
                                extracted_data=self.extracted_data,
                                visited_urls=self.visited_urls,
                            )
                    except asyncio.TimeoutError:
                        return WorkerExecutionOutcome(
                            success=False,
                            status="failed",
                            failure_type="timeout",
                            error_message="Timed out waiting for user confirmation.",
                            steps_executed=step_number,
                            token_usage=self.total_usage,
                            extracted_data=self.extracted_data,
                            visited_urls=self.visited_urls,
                        )

                # Check if task is finished
                if action_result.is_finished:
                    final_res = action_result.finish_result or self.extracted_data
                    await self.emit_event(
                        "result",
                        {"result": final_res, "steps_executed": step_number},
                    )
                    return WorkerExecutionOutcome(
                        success=True,
                        status="completed",
                        result=final_res,
                        steps_executed=step_number,
                        token_usage=self.total_usage,
                        extracted_data=self.extracted_data,
                        visited_urls=self.visited_urls,
                    )

                # 4. POST-ACTION OBSERVE & VERIFY
                post_snapshot = await capture_dom_snapshot(
                    self.session.page,
                    include_som_screenshot=True,
                )

                post_screenshot_b64 = None
                if post_snapshot.screenshot_bytes:
                    post_screenshot_b64 = base64.b64encode(post_snapshot.screenshot_bytes).decode("utf-8")

                verification: VerificationResult = await self.verifier.verify(
                    session=self.session,
                    action=action,
                    expected_state=expected_state,
                    previous_snapshot=current_snapshot,
                    current_snapshot=post_snapshot,
                )

                self.action_history.append(
                    {
                        "step": step_number,
                        "action": action.action,
                        "status": "success" if verification.success else "failure",
                        "reason": verification.reason,
                    }
                )

                await self.emit_event(
                    "verification",
                    {
                        "step_number": step_number,
                        "status": "success" if verification.success else "failure",
                        "failure_type": verification.failure_type,
                        "reason": verification.reason,
                        "screenshot_b64": post_screenshot_b64,
                    },
                )

                # Pacing delay between steps to respect provider rate limits (e.g. 5 RPM on Free Tier)
                await asyncio.sleep(2.5)

                if verification.success:
                    last_failure_reason = None
                    consecutive_failures = 0
                    break  # Success! Proceed to next step
                else:
                    # Verification failed: increment retry
                    consecutive_failures += 1
                    retry_count += 1
                    last_failure_reason = f"{verification.failure_type}: {verification.reason}"
                    
                    await self.emit_event(
                        "retry",
                        {
                            "step_number": step_number,
                            "retry_count": retry_count,
                            "reason": last_failure_reason,
                        },
                    )

                    # Non-capability failure (e.g. blocked by CAPTCHA) -> return immediately
                    if verification.failure_type == "blocked":
                        return WorkerExecutionOutcome(
                            success=False,
                            status="failed",
                            failure_type="blocked",
                            error_message=verification.reason,
                            steps_executed=step_number,
                            token_usage=self.total_usage,
                            extracted_data=self.extracted_data,
                            visited_urls=self.visited_urls,
                        )

            # If retries for this step were exhausted without success
            if consecutive_failures > settings.MAX_RETRIES_PER_STEP:
                logger.warning(f"Exhausted retries on step {step_number}. Failure: {last_failure_reason}")
                return WorkerExecutionOutcome(
                    success=False,
                    status="failed",
                    failure_type="reasoning_error" if "loop" not in (last_failure_reason or "") else "loop_detected",
                    error_message=f"Step failed after {settings.MAX_RETRIES_PER_STEP} retries: {last_failure_reason}",
                    steps_executed=step_number,
                    token_usage=self.total_usage,
                    extracted_data=self.extracted_data,
                    visited_urls=self.visited_urls,
                )

        # Step budget exhausted
        logger.warning(f"Step budget exhausted ({self.max_steps} steps) on tier {self.tier}.")
        return WorkerExecutionOutcome(
            success=False,
            status="failed",
            failure_type="reasoning_error",
            error_message=f"Step budget exhausted ({self.max_steps} steps) on tier {self.tier}.",
            steps_executed=step_number,
            token_usage=self.total_usage,
            extracted_data=self.extracted_data,
            visited_urls=self.visited_urls,
        )
