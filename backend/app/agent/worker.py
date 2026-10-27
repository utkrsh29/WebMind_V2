import asyncio
import base64
import logging
import re
from typing import Any, Callable, Coroutine, Dict, List, Optional
from urllib.parse import quote_plus
from app.agent.actions import execute_action
from app.agent.llm import TokenUsage
from app.agent.memory import (
    ExperienceMemoryService,
    is_attributable_memory_failure,
    is_reusable_memory_action,
    normalized_domain,
    redact_sensitive_text,
    url_pattern,
)
from app.agent.memory_schema import (
    ExperienceMemoryData,
    MemoryAction,
    MemoryAlternative,
    MemoryFailure,
    MemoryPageContext,
    MemoryRetrievalContext,
)
from app.agent.planner import Planner
from app.agent.verifier import BrowserVerifier, VerificationResult
from app.browser.dom_snapshot import DOMSnapshot, capture_dom_snapshot
from app.browser.session import BrowserSession
from app.config import settings
from app.schemas import ActionSchema, AgentTier, ExpectedStateSchema, FailureType, PlannerOutputSchema

logger = logging.getLogger(__name__)

# Step budgets per tier
TIER_STEP_BUDGETS = {
    "FAST": 12,
    "GENERAL": 25,
    "REASONING": 40,
}

# After this many consecutive failures, pause between steps to not hammer the page
CONSECUTIVE_FAILURE_PAUSE_THRESHOLD = 3


class WorkerExecutionOutcome:
    def __init__(
        self,
        success: bool,
        status: str,  # "completed" | "failed" | "waiting_user" | "cancelled"
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
        task_id: str = "",
        task_type: str = "other",
        requires_comparison: bool = False,
        target_sites: Optional[List[str]] = None,
        event_callback: Optional[Callable[[str, Dict[str, Any]], Coroutine[Any, Any, None]]] = None,
        experience_hints: Optional[List[str]] = None,
        initial_extracted_data: Optional[Dict[str, Any]] = None,
        initial_visited_urls: Optional[List[str]] = None,
    ):
        self.tier = tier
        self.task_query = task_query
        self.constraints = constraints
        self.session = session
        self.task_id = task_id
        self.task_type = task_type
        self.requires_comparison = requires_comparison
        self.target_sites = target_sites or []
        self.event_callback = event_callback
        self.experience_hints = experience_hints or []
        self.memory = ExperienceMemoryService()
        self.pending_memory_failure: Optional[Dict[str, Any]] = None
        self.verified_memory_actions: List[MemoryAction] = []
        self.extracted_data = initial_extracted_data or {}
        self.visited_urls = initial_visited_urls or []

        self.planner = Planner(tier=tier)
        self.verifier = BrowserVerifier()
        self.total_usage = TokenUsage()
        self.action_history: List[Dict[str, Any]] = []
        self.max_steps = TIER_STEP_BUDGETS.get(tier, settings.MAX_STEPS_PER_RUN)

        # Human-in-the-loop wait event
        self._user_response_future: Optional[asyncio.Future] = None
        self._awaiting_user = False
        self._browser_handoff_allowed = False
        self._browser_handoff_revision = 0
        self._browser_handoff_signature: Optional[tuple] = None
        self._browser_handoff_lock = asyncio.Lock()

    @staticmethod
    def _page_memory_context(snapshot: DOMSnapshot) -> MemoryPageContext:
        elements: List[str] = []
        controls: List[str] = []
        for element in snapshot.elements[:60]:
            name = element.get("name") or ""
            placeholder = element.get("placeholder") or ""
            role = element.get("role") or element.get("tag") or ""
            label = redact_sensitive_text(" ".join(str(x) for x in (name, placeholder) if x), 120)
            if label:
                elements.append(f"{role}: {label}")
                controls.append(str(role))
        return MemoryPageContext(
            page_title=redact_sensitive_text(snapshot.title, 200),
            relevant_elements=elements,
            available_controls=sorted(set(controls)),
            # Store structure and control labels, not arbitrary page body text
            # that could contain account details or user-entered values.
            page_state_summary=redact_sensitive_text(
                f"{len(snapshot.elements)} visible controls; types: {', '.join(sorted(set(controls)))}",
                300,
            ),
        )

    @staticmethod
    def _memory_action(action, snapshot: DOMSnapshot) -> MemoryAction:
        element = next(
            (item for item in snapshot.elements if item.get("id") == action.element_id),
            None,
        ) if action.element_id is not None else None
        if element:
            target = " ".join(str(x) for x in (
                element.get("role"), element.get("name"), element.get("placeholder")
            ) if x)
        elif action.action in ("goto", "new_tab"):
            target = url_pattern(action.url or "")
        else:
            target = action.action
        params: Dict[str, Any] = {}
        if action.action == "type":
            params = {"input_kind": "text", "input_length": len(action.text or ""), "submit": bool(action.submit)}
        elif action.action == "select":
            params = {"input_kind": "select", "value_redacted": True}
        elif action.action in ("scroll", "scroll_to"):
            params = {"direction": action.direction, "amount": action.amount}
        elif action.action == "press":
            params = {"key": action.key}
        return MemoryAction(
            action_type=action.action,
            target_description=redact_sensitive_text(target, 300),
            selector_strategy="fresh visible WebMind element ID" if element else "page-level action",
            action_parameters=params,
        )

    @staticmethod
    def _action_matches_memory(action: MemoryAction, memory_record: Dict[str, Any]) -> bool:
        candidates = []
        alternative = memory_record.get("successful_alternative") or {}
        if alternative:
            candidates.append(alternative)
        candidates.extend(memory_record.get("verified_workflow") or [])
        for candidate in candidates:
            if candidate.get("action_type") != action.action_type:
                continue
            target = str(candidate.get("target_description") or "").lower().strip()
            current = action.target_description.lower().strip()
            if not target or not current:
                continue
            if target in current or current in target:
                return True
            from app.agent.memory import lexical_similarity
            if lexical_similarity(target, current) >= 0.25:
                return True
        return False

    async def _retrieve_memory_guidance(
        self,
        snapshot: DOMSnapshot,
        step_number: int,
        failure_type: Optional[str],
    ) -> List[Dict[str, Any]]:
        context = MemoryRetrievalContext(
            task_description=self.task_query,
            task_type=self.task_type,
            website_domain=normalized_domain(snapshot.url),
            page_url_pattern=url_pattern(snapshot.url),
            page_context=self._page_memory_context(snapshot),
            failure_type=failure_type,
        )
        memories = await self.memory.retrieve_for_step(context, task_id=self.task_id, step_number=step_number)
        if memories:
            await self.emit_event("memory_event", {
                "stage": "memory_retrieval",
                "memory_ids": [item["memory"]["memory_id"] for item in memories],
                "retrieval_count": len(memories),
                "step_number": step_number,
            })
        for item in memories:
            memory_record = item["memory"]
            gate = item["gate"]
            await self.emit_event("memory_event", {
                "stage": "gate_decision",
                "memory_id": memory_record["memory_id"],
                "decision": gate["decision"],
                "relevance_score": gate["relevance_score"],
                "reason": gate["reason"],
                "outcome": memory_record.get("outcome"),
                "reliability_score": memory_record.get("reliability_score"),
                "step_number": step_number,
            })
        return memories

    @staticmethod
    def _planner_memory_hints(memories: List[Dict[str, Any]]) -> List[str]:
        hints = []
        for item in memories:
            if not item.get("included_in_planner"):
                continue
            memory_record, gate = item["memory"], item["gate"]
            attempted = memory_record.get("action_attempted") or {}
            alternative = memory_record.get("successful_alternative") or {}
            workflow = [
                step for step in (memory_record.get("verified_workflow") or [])
                if is_reusable_memory_action(step.get("action_type"))
            ]
            steps = " → ".join(
                f"{step.get('action_type')} {step.get('target_description')}" for step in workflow[:8]
            )
            if alternative:
                steps = f"verified alternative: {alternative.get('action_type')} {alternative.get('target_description')}"
            hints.append(
                f"memory_id={memory_record['memory_id']}; gate={gate['decision']}; "
                f"outcome={memory_record.get('outcome')}; reliability={memory_record.get('reliability_score', 0):.2f}; "
                f"context={memory_record.get('page_context', {}).get('page_title', '')}; "
                f"previous={attempted.get('action_type')} {attempted.get('target_description')}; {steps}. "
                f"Gate reason: {gate['reason']} Re-identify all controls from this page."
            )
        return hints

    def _missing_comparison_sites(self) -> List[str]:
        if not self.requires_comparison and self.task_type not in ("comparison", "multi_site"):
            return []

        requested = []
        for raw_site in self.target_sites:
            site = str(raw_site).strip().lower().removeprefix("www.")
            if not site or site == "unspecified":
                continue
            canonical = next(
                (known for known in ("amazon", "flipkart", "ebay", "walmart", "target") if known in site),
                site,
            )
            requested.append(canonical)
        if not requested:
            requested = [
                site for site in ("amazon", "flipkart", "ebay", "walmart", "target")
                if re.search(rf"\b{re.escape(site)}\b", self.task_query.lower())
            ]

        evidence_urls = [
            " ".join((str(record.get("source", "")), str(record.get("url", "")))).lower()
            for record in self.extracted_data.get("sources", [])
            if isinstance(record, dict)
        ]
        return [site for site in requested if not any(site in evidence for evidence in evidence_urls)]

    def _comparison_search_url(self, site: str) -> Optional[str]:
        """Build a direct retailer search URL for supported cross-site comparisons."""
        product = str(self.constraints.get("product") or "").strip()
        if not product:
            product = re.sub(
                r"\b(compare|comparison|price|prices|cost|across|between|on|from|both|and|vs|versus|amazon|flipkart)\b",
                " ",
                self.task_query,
                flags=re.IGNORECASE,
            )
            product = re.sub(r"\s+", " ", product).strip(" .,:;-")
        if not product:
            return None
        if site == "amazon":
            return f"https://www.amazon.in/s?k={quote_plus(product)}"
        if site == "flipkart":
            return f"https://www.flipkart.com/search?q={quote_plus(product)}"
        return None

    def _next_comparison_site_hop(self, snapshot: DOMSnapshot) -> Optional[str]:
        """After extracting one requested retailer, move directly to the next missing one."""
        if not (self.requires_comparison or self.task_type in ("comparison", "multi_site")):
            return None
        missing_sites = self._missing_comparison_sites()
        current_host = (snapshot.url or "").lower()
        extracted_current_site = any(
            isinstance(record, dict)
            and str(record.get("url", "")).lower().split("/", 3)[:3]
            == current_host.split("/", 3)[:3]
            for record in self.extracted_data.get("sources", [])
        )
        if not extracted_current_site:
            return None
        for site in missing_sites:
            direct_url = self._comparison_search_url(site)
            if direct_url:
                return direct_url
        return None

    async def _record_recovery(
        self,
        failure: Dict[str, Any],
        alternative_action: MemoryAction,
        current_snapshot: DOMSnapshot,
        step_number: int,
    ) -> None:
        attempted = failure["action"]
        same_action = (
            attempted.action_type == alternative_action.action_type
            and attempted.target_description == alternative_action.target_description
        )
        if same_action or not is_reusable_memory_action(alternative_action.action_type):
            return
        failure_reason = redact_sensitive_text(failure.get("reason"), 1000)
        data = ExperienceMemoryData(
            task_description=self.task_query,
            task_type=self.task_type,
            tier=self.tier,
            website_domain=normalized_domain(failure["snapshot"].url),
            page_url_pattern=url_pattern(failure["snapshot"].url),
            page_context=self._page_memory_context(failure["snapshot"]),
            action_attempted=attempted,
            failure=MemoryFailure(
                occurred=True,
                failure_type=str(failure["failure_type"] or "postcondition_mismatch"),
                reason=failure_reason,
                verification_evidence=failure_reason,
            ),
            successful_alternative=MemoryAlternative(
                action_type=alternative_action.action_type,
                target_description=alternative_action.target_description,
                selector_strategy=alternative_action.selector_strategy,
                recovery_steps=[
                    "Re-observed the page after the failed verification.",
                    f"Verified {alternative_action.action_type} on {alternative_action.target_description}.",
                ],
            ),
            outcome="recovered",
        )
        memory_id = await self.memory.record_experience(
            data,
            task_id=self.task_id,
            evidence_key=f"recovery:{self.task_id}:{failure['step_number']}",
            evidence_outcome="success",
            evidence={
                "failure_type": str(failure["failure_type"] or "postcondition_mismatch"),
                "reason": str(failure["reason"]),
                "verification_status": "success",
                "action_type": alternative_action.action_type,
                "target_description": alternative_action.target_description,
                "step_number": step_number,
                "task_type": self.task_type,
            },
        )
        if memory_id:
            await self.emit_event("memory_event", {
                "stage": "learning_update",
                "memory_id": memory_id,
                "decision": "RECORDED_RECOVERY",
                "reason": "A failed action was followed by a different action with verified success.",
                "step_number": step_number,
            })

    async def persist_terminal_failure(self) -> Optional[str]:
        failure = self.pending_memory_failure
        if not failure:
            return None
        failure_reason = redact_sensitive_text(failure.get("reason"), 1000)
        data = ExperienceMemoryData(
            task_description=self.task_query,
            task_type=self.task_type,
            tier=self.tier,
            website_domain=normalized_domain(failure["snapshot"].url),
            page_url_pattern=url_pattern(failure["snapshot"].url),
            page_context=self._page_memory_context(failure["snapshot"]),
            action_attempted=failure["action"],
            failure=MemoryFailure(
                occurred=True,
                failure_type=str(failure["failure_type"] or "postcondition_mismatch"),
                reason=failure_reason,
                verification_evidence=failure_reason,
            ),
            outcome="failure",
        )
        return await self.memory.record_experience(
            data,
            task_id=self.task_id,
            evidence_key=f"terminal-failure:{self.task_id}:{failure['step_number']}",
            evidence_outcome="failure",
            evidence={
                "failure_type": str(failure["failure_type"] or "postcondition_mismatch"),
                "reason": str(failure["reason"]),
                "verification_status": "failure",
                "action_type": failure["action"].action_type,
                "target_description": failure["action"].target_description,
                "step_number": failure["step_number"],
                "task_type": self.task_type,
            },
        )

    async def _record_successful_workflow(self, snapshot: DOMSnapshot) -> Optional[str]:
        workflow = self.verified_memory_actions[-30:]
        if len(workflow) < 2:
            return None
        data = ExperienceMemoryData(
            task_description=self.task_query,
            task_type=self.task_type,
            tier=self.tier,
            website_domain=normalized_domain(snapshot.url),
            page_url_pattern=url_pattern(snapshot.url),
            page_context=self._page_memory_context(snapshot),
            action_attempted=workflow[0],
            verified_workflow=workflow,
            outcome="success",
        )
        memory_id = await self.memory.record_experience(
            data,
            task_id=self.task_id,
            evidence_key=f"verified-workflow:{self.task_id}",
            evidence_outcome="success",
            evidence={
                "verification_status": "success",
                "action_type": "verified_workflow",
                "target_description": f"{len(workflow)} verified actions",
                "step_number": len(workflow),
                "task_type": self.task_type,
            },
        )
        if memory_id:
            await self.emit_event("memory_event", {
                "stage": "learning_update",
                "memory_id": memory_id,
                "decision": "RECORDED_SUCCESS",
                "reason": "Stored a compact workflow made of verified browser actions.",
                "step_number": len(self.verified_memory_actions),
            })
        return memory_id

    # ─────────────────────────────────────────────────────────────────────────
    async def emit_event(self, event_type: str, data: Dict[str, Any]) -> None:
        """Helper to stream events to SSE/WebSocket listeners."""
        if self.event_callback:
            try:
                await self.event_callback(event_type, data)
            except Exception as e:
                logger.warning(f"Error in event callback: {e}")

    def provide_user_response(self, approved: bool, user_input: Optional[str]) -> None:
        """Resolves waiting human-in-the-loop response."""
        if self._user_response_future and not self._user_response_future.done():
            self._user_response_future.set_result({"approved": approved, "user_input": user_input})

    @staticmethod
    def _handoff_control_signature(snapshot: DOMSnapshot) -> tuple:
        return tuple(
            (
                item.get("id"), item.get("tag"), item.get("role"), item.get("name"),
                item.get("placeholder"), item.get("inputType"), bool(item.get("disabled")),
                tuple((option.get("value"), option.get("label")) for option in item.get("options", [])),
            )
            for item in snapshot.elements
        )

    async def _browser_handoff_state_from_snapshot(self, snapshot: DOMSnapshot) -> Dict[str, Any]:
        self._browser_handoff_revision += 1
        self._browser_handoff_signature = self._handoff_control_signature(snapshot)
        controls = []
        for element in snapshot.elements[:100]:
            controls.append({
                "element_id": element.get("id"),
                "tag": element.get("tag"),
                "role": element.get("role"),
                "name": redact_sensitive_text(element.get("name", ""), 120),
                "placeholder": redact_sensitive_text(element.get("placeholder", ""), 120),
                "input_type": element.get("inputType") or "",
                "disabled": bool(element.get("disabled")),
                "checked": bool(element.get("checked")),
                "options": [
                    {
                        "label": redact_sensitive_text(option.get("label", ""), 100),
                        "value": redact_sensitive_text(option.get("value", ""), 100),
                    }
                    for option in (element.get("options") or [])[:30]
                ],
            })
        image = (
            base64.b64encode(snapshot.screenshot_bytes).decode("ascii")
            if snapshot.screenshot_bytes else None
        )
        return {
            "revision": self._browser_handoff_revision,
            "url": snapshot.url,
            "title": redact_sensitive_text(snapshot.title, 200),
            "screenshot_b64": image,
            "controls": controls,
        }

    async def get_browser_handoff_state(self) -> Dict[str, Any]:
        """Return a fresh, value-redacted view only while the worker is paused for a person."""
        if not self._awaiting_user or not self._browser_handoff_allowed:
            raise RuntimeError("The browser is available for handoff only while WebMind is waiting for you.")
        snapshot = await capture_dom_snapshot(self.session.page, include_som_screenshot=True)
        return await self._browser_handoff_state_from_snapshot(snapshot)

    async def perform_human_browser_action(self, request) -> Dict[str, Any]:
        """Apply one explicit human action to a fresh observed control and refresh the view."""
        if not self._awaiting_user or not self._browser_handoff_allowed:
            raise RuntimeError("The browser is available for handoff only while WebMind is waiting for you.")

        async with self._browser_handoff_lock:
            fresh = await capture_dom_snapshot(self.session.page, include_som_screenshot=True)
            fresh_signature = self._handoff_control_signature(fresh)
            if (
                request.revision != self._browser_handoff_revision
                or fresh_signature != self._browser_handoff_signature
            ):
                state = await self._browser_handoff_state_from_snapshot(fresh)
                return {"ok": False, "stale": True, "message": "The page changed. Review the refreshed tab before acting.", "state": state}

            if request.action in ("click", "type", "select"):
                element = next((item for item in fresh.elements if item.get("id") == request.element_id), None)
                if element is None or element.get("disabled"):
                    state = await self._browser_handoff_state_from_snapshot(fresh)
                    return {"ok": False, "stale": True, "message": "That control is no longer available. Review the refreshed tab.", "state": state}
                if request.action == "type" and element.get("tag") not in ("input", "textarea") and element.get("role") != "textbox":
                    raise ValueError("Text can only be entered into an observed text field.")
                if request.action == "select" and element.get("tag") != "select":
                    raise ValueError("Choose an observed dropdown to select an option.")
                if request.action == "select" and request.value not in {
                    option.get("value") for option in (element.get("options") or [])
                }:
                    raise ValueError("That option is not present in the observed dropdown.")

            from app.schemas import ActionSchema
            action = ActionSchema(
                action=request.action,
                element_id=request.element_id,
                text=request.text,
                value=request.value,
                key=request.key,
                direction=request.direction,
                amount=request.amount,
                submit=False,
            )
            result = await execute_action(self.session, action, extracted_store=self.extracted_data)
            if result.ask_user_prompt:
                state = await self.get_browser_handoff_state()
                return {
                    "ok": False,
                    "stale": False,
                    "message": "WebMind protected this consequential control. Resume the agent to review its approval prompt.",
                    "state": state,
                }
            state = await self.get_browser_handoff_state()
            return {
                "ok": bool(result.success),
                "stale": False,
                "message": "Page updated." if result.success else "That action did not complete. Review the refreshed page.",
                "state": state,
            }

    # ─────────────────────────────────────────────────────────────────────────
    async def run(self) -> WorkerExecutionOutcome:
        """Main Observe → Plan → Act → Verify loop (BrowserOS-style)."""
        step_number = 0
        last_failure_reason: Optional[str] = None
        last_user_input: Optional[str] = None
        consecutive_failures = 0
        comparison_task = self.requires_comparison or self.task_type in ("comparison", "multi_site")

        logger.info(f"AgentWorker starting on tier={self.tier} (budget={self.max_steps} steps)")

        while step_number < self.max_steps:
            step_number += 1
            retry_count = 0

            while retry_count <= settings.MAX_RETRIES_PER_STEP:

                # ── OBSERVE ──────────────────────────────────────────────────
                current_snapshot = await capture_dom_snapshot(
                    self.session.page,
                    include_som_screenshot=(
                        self.tier in ("GENERAL", "REASONING") and not comparison_task
                    ),
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
                        "element_count": len(current_snapshot.elements),
                    },
                )

                failure_type_hint = None
                if last_failure_reason:
                    failure_type_hint = last_failure_reason.split(":", 1)[0].strip()
                step_memories = await self._retrieve_memory_guidance(
                    current_snapshot,
                    step_number,
                    failure_type_hint,
                )
                experience_hints = self._planner_memory_hints(step_memories)

                # ── PLAN ─────────────────────────────────────────────────────
                try:
                    plan_output, usage = await self.planner.plan_next_step(
                        task_query=self.task_query,
                        constraints=self.constraints,
                        step_number=step_number,
                        current_snapshot=current_snapshot,
                        action_history=self.action_history,
                        extracted_data=self.extracted_data,
                        experience_hints=experience_hints + self.experience_hints,
                        last_failure_reason=last_failure_reason,
                        last_user_input=last_user_input,
                        consecutive_failures=consecutive_failures,
                        task_type=self.task_type,
                        requires_comparison=self.requires_comparison,
                        target_sites=self.target_sites,
                    )
                    self.total_usage.add(usage)
                    last_user_input = None  # consumed
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

                # Once a retailer page has been extracted, do not spend another
                # planner turn re-extracting the same page or narrating it. Move
                # to the next requested retailer so comparisons make progress.
                next_site_url = self._next_comparison_site_hop(current_snapshot)
                if next_site_url:
                    action = ActionSchema(action="goto", url=next_site_url)
                    thought = "The current retailer has been captured; opening the next requested retailer to collect comparable evidence."
                    expected_state = ExpectedStateSchema(
                        url_contains="amazon.in" if "amazon.in" in next_site_url else "flipkart.com"
                    )

                if action.action == "finish":
                    missing_sites = self._missing_comparison_sites()
                    if missing_sites:
                        retry_count += 1
                        consecutive_failures += 1
                        last_failure_reason = (
                            "comparison_missing_sources: No extracted page evidence yet for "
                            + ", ".join(missing_sites)
                            + ". Visit that requested site, extract the matching product and price, then compare."
                        )
                        self.action_history.append({
                            "step": step_number,
                            "action": "finish",
                            "detail": "Comparison attempted before all requested sites were checked",
                            "status": "failure",
                            "reason": last_failure_reason,
                        })
                        await self.emit_event("retry", {
                            "step_number": step_number,
                            "retry_count": retry_count,
                            "reason": last_failure_reason,
                            "consecutive_failures": consecutive_failures,
                        })
                        if retry_count > settings.MAX_RETRIES_PER_STEP:
                            return WorkerExecutionOutcome(
                                success=False,
                                status="failed",
                                failure_type="reasoning_error",
                                error_message=last_failure_reason,
                                steps_executed=step_number,
                                token_usage=self.total_usage,
                                extracted_data=self.extracted_data,
                                visited_urls=self.visited_urls,
                            )
                        continue

                offered_memory_ids = {
                    item["memory"]["memory_id"] for item in step_memories
                    if item.get("included_in_planner")
                }
                selected_memory_ids = set(plan_output.memory_ids_used) & offered_memory_ids
                await self.memory.mark_planner_selection(self.task_id, step_number, selected_memory_ids)
                if selected_memory_ids:
                    await self.emit_event("memory_event", {
                        "stage": "planner_selection",
                        "memory_ids": sorted(selected_memory_ids),
                        "decision": "SELECTED",
                        "reason": "Planner reported that these supplied memories materially informed its next action.",
                        "step_number": step_number,
                    })

                await self.emit_event(
                    "action_planned",
                    {
                        "step_number": step_number,
                        "thought": thought,
                        "action": _redacted_action_for_trace(action),
                        "expected_state": expected_state.model_dump() if expected_state else None,
                    },
                )

                # ── ACT ──────────────────────────────────────────────────────
                action_result = await execute_action(
                    session=self.session,
                    action=action,
                    extracted_store=self.extracted_data,
                )

                # Emit screenshot if captured
                if action_result.screenshot_b64:
                    await self.emit_event(
                        "screenshot",
                        {"step_number": step_number, "screenshot_b64": action_result.screenshot_b64},
                    )

                # ── HUMAN-IN-THE-LOOP: ask_user ───────────────────────────────
                if action_result.ask_user_prompt:
                    is_login = action_result.is_login
                    loop = asyncio.get_running_loop()
                    self._user_response_future = loop.create_future()
                    self._awaiting_user = True
                    self._browser_handoff_allowed = not action_result.requires_approval
                    self._browser_handoff_revision = 0
                    self._browser_handoff_signature = None
                    await self.emit_event(
                        "ask_user",
                        {
                            "prompt": action_result.ask_user_prompt,
                            "step_number": step_number,
                            "is_login": is_login,
                            "can_take_over": not action_result.requires_approval,
                            "requires_approval": action_result.requires_approval,
                        },
                    )
                    try:
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
                        # Store user input for next planning step
                        raw_input = user_ans.get("user_input")
                        if raw_input:
                            last_user_input = raw_input
                            if is_login:
                                await self.emit_event(
                                    "user_resumed",
                                    {"step_number": step_number, "message": "User provided credentials. Resuming task."},
                                )
                                # Re-capture snapshot after user potentially logged in
                                current_snapshot = await capture_dom_snapshot(
                                    self.session.page,
                                    include_som_screenshot=False,
                                )
                        consecutive_failures = 0
                        last_failure_reason = None
                        if action_result.requires_approval:
                            # Execute exactly the intercepted action once after explicit approval.
                            action_result = await execute_action(
                                session=self.session,
                                action=action,
                                extracted_store=self.extracted_data,
                                approved_irreversible=True,
                            )
                            if action_result.ask_user_prompt:
                                return WorkerExecutionOutcome(
                                    success=False,
                                    status="failed",
                                    failure_type="reasoning_error",
                                    error_message="Approved action remained blocked by a safety check.",
                                    steps_executed=step_number,
                                    token_usage=self.total_usage,
                                    extracted_data=self.extracted_data,
                                    visited_urls=self.visited_urls,
                                )
                        else:
                            break  # User answered a normal question; let the planner continue.
                    except asyncio.TimeoutError:
                        return WorkerExecutionOutcome(
                            success=False,
                            status="failed",
                            failure_type="timeout",
                            error_message="Timed out waiting for user response.",
                            steps_executed=step_number,
                            token_usage=self.total_usage,
                            extracted_data=self.extracted_data,
                            visited_urls=self.visited_urls,
                        )
                    finally:
                        self._awaiting_user = False
                        self._browser_handoff_allowed = False
                        self._browser_handoff_signature = None

                # ── FINISH ───────────────────────────────────────────────────
                if action_result.is_finished:
                    final_res = action_result.finish_result or self.extracted_data
                    await self._record_successful_workflow(current_snapshot)
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

                # ── POST-ACTION OBSERVE & VERIFY ──────────────────────────────
                post_snapshot = await capture_dom_snapshot(
                    self.session.page,
                    include_som_screenshot=(
                        self.tier in ("GENERAL", "REASONING") and not comparison_task
                    ),
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
                    action_succeeded=action_result.success,
                    action_message=action_result.message,
                )

                action_memory = self._memory_action(action, current_snapshot)
                matched_memory_ids = {
                    item["memory"]["memory_id"]
                    for item in step_memories
                    if item.get("included_in_planner")
                    if item["memory"]["memory_id"] in selected_memory_ids
                    if self._action_matches_memory(action_memory, item["memory"])
                }
                if verification.success:
                    if matched_memory_ids:
                        updated_memory_ids = await self.memory.record_feedback(
                            task_id=self.task_id,
                            step_number=step_number,
                            memory_ids=matched_memory_ids,
                            outcome="success",
                            evidence={
                                "verification_status": "success",
                                "action_type": action.action,
                                "target_description": action_memory.target_description,
                                "step_number": step_number,
                                "task_type": self.task_type,
                            },
                        )
                        if updated_memory_ids:
                            await self.emit_event("memory_event", {
                                "stage": "reliability_update",
                                "memory_ids": sorted(updated_memory_ids),
                                "decision": "VERIFIED_SUCCESS",
                                "reason": "The reused action matched a stored workflow and passed the post-condition verifier.",
                                "step_number": step_number,
                            })
                    if self.pending_memory_failure:
                        await self._record_recovery(
                            self.pending_memory_failure,
                            action_memory,
                            current_snapshot,
                            step_number,
                        )
                        self.pending_memory_failure = None
                    if is_reusable_memory_action(action.action):
                        self.verified_memory_actions.append(action_memory)
                else:
                    if matched_memory_ids and is_attributable_memory_failure(
                        verification.failure_type,
                        verification.reason,
                        action_matches_memory=True,
                    ):
                        updated_memory_ids = await self.memory.record_feedback(
                            task_id=self.task_id,
                            step_number=step_number,
                            memory_ids=matched_memory_ids,
                            outcome="failure",
                            evidence={
                                "failure_type": verification.failure_type or "postcondition_mismatch",
                                "reason": verification.reason,
                                "verification_status": "failure",
                                "action_type": action.action,
                                "target_description": action_memory.target_description,
                                "step_number": step_number,
                                "task_type": self.task_type,
                            },
                        )
                        if updated_memory_ids:
                            await self.emit_event("memory_event", {
                                "stage": "reliability_update",
                                "memory_ids": sorted(updated_memory_ids),
                                "decision": "ATTRIBUTABLE_FAILURE",
                                "reason": "The memory-guided action matched its stored alternative but failed its verification.",
                                "step_number": step_number,
                            })
                    self.pending_memory_failure = {
                        "action": action_memory,
                        "snapshot": current_snapshot,
                        "failure_type": verification.failure_type,
                        "reason": verification.reason,
                        "step_number": step_number,
                    }

                self.action_history.append(
                    {
                        "step": step_number,
                        "action": action.action,
                        "detail": _action_detail(action),
                        "status": "success" if verification.success else "failure",
                        "reason": verification.reason,
                        "change": verification.change_summary,
                    }
                )

                await self.emit_event(
                    "verification",
                    {
                        "step_number": step_number,
                        "status": "success" if verification.success else "failure",
                        "failure_type": verification.failure_type,
                        "reason": verification.reason,
                        "change_summary": verification.change_summary,
                        "screenshot_b64": post_screenshot_b64,
                    },
                )

                if verification.success:
                    last_failure_reason = None
                    consecutive_failures = 0
                    break  # Success — proceed to next step

                # ── FAILURE HANDLING ─────────────────────────────────────────
                consecutive_failures += 1
                retry_count += 1
                last_failure_reason = f"{verification.failure_type}: {verification.reason}"

                await self.emit_event(
                    "retry",
                    {
                        "step_number": step_number,
                        "retry_count": retry_count,
                        "reason": last_failure_reason,
                        "consecutive_failures": consecutive_failures,
                    },
                )

                # Login wall — must ask user, stop retrying
                if verification.failure_type == "login_required":
                    self.pending_memory_failure = None
                    self._user_response_future = asyncio.get_running_loop().create_future()
                    self._awaiting_user = True
                    self._browser_handoff_allowed = True
                    self._browser_handoff_revision = 0
                    self._browser_handoff_signature = None
                    await self.emit_event(
                        "ask_user",
                        {
                            "prompt": "The website requires you to sign in. Use the paused browser tab to enter your credentials directly on the site, then resume WebMind. Do not paste passwords into this chat.",
                            "step_number": step_number,
                            "is_login": True,
                            "can_take_over": True,
                        },
                    )
                    try:
                        user_ans = await asyncio.wait_for(self._user_response_future, timeout=300.0)
                        if user_ans.get("user_input"):
                            last_user_input = user_ans["user_input"]
                        await self.emit_event(
                            "user_resumed",
                            {"step_number": step_number, "message": "User handled login. Resuming task."},
                        )
                        consecutive_failures = 0
                        last_failure_reason = None
                        break
                    except asyncio.TimeoutError:
                        return WorkerExecutionOutcome(
                            success=False,
                            status="failed",
                            failure_type="login_required",
                            error_message="Timed out waiting for user login.",
                            steps_executed=step_number,
                            token_usage=self.total_usage,
                            extracted_data=self.extracted_data,
                            visited_urls=self.visited_urls,
                        )
                    finally:
                        self._awaiting_user = False
                        self._browser_handoff_allowed = False
                        self._browser_handoff_signature = None

                # Hard blocks (CAPTCHA etc.) — cannot recover
                if verification.failure_type == "blocked":
                    self.pending_memory_failure = None
                    self._user_response_future = asyncio.get_running_loop().create_future()
                    self._awaiting_user = True
                    self._browser_handoff_allowed = True
                    self._browser_handoff_revision = 0
                    self._browser_handoff_signature = None
                    await self.emit_event("ask_user", {
                        "prompt": "The website is showing a CAPTCHA or access check. Complete it yourself in the paused browser tab, then resume WebMind. WebMind will not solve or bypass the challenge.",
                        "step_number": step_number,
                        "is_login": False,
                        "can_take_over": True,
                        "requires_approval": False,
                    })
                    try:
                        user_ans = await asyncio.wait_for(self._user_response_future, timeout=300.0)
                        if not user_ans.get("approved"):
                            return WorkerExecutionOutcome(
                                success=False,
                                status="cancelled",
                                error_message="User cancelled the browser handoff.",
                                steps_executed=step_number,
                                token_usage=self.total_usage,
                                extracted_data=self.extracted_data,
                                visited_urls=self.visited_urls,
                            )
                        if user_ans.get("user_input"):
                            last_user_input = user_ans["user_input"]
                        await self.emit_event("user_resumed", {
                            "step_number": step_number,
                            "message": "User completed the browser check. Re-observing the page.",
                        })
                        consecutive_failures = 0
                        last_failure_reason = None
                        break
                    except asyncio.TimeoutError:
                        return WorkerExecutionOutcome(
                            success=False,
                            status="failed",
                            failure_type="blocked",
                            error_message="Timed out waiting for the user to complete the website check.",
                            steps_executed=step_number,
                            token_usage=self.total_usage,
                            extracted_data=self.extracted_data,
                            visited_urls=self.visited_urls,
                        )
                    finally:
                        self._awaiting_user = False
                        self._browser_handoff_allowed = False
                        self._browser_handoff_signature = None

                # Slow down on repeated failures (don't hammer the page)
                if consecutive_failures >= CONSECUTIVE_FAILURE_PAUSE_THRESHOLD:
                    await asyncio.sleep(1.5)

            # If retries exhausted without a success or break
            if consecutive_failures > settings.MAX_RETRIES_PER_STEP:
                logger.warning(f"Exhausted retries on step {step_number}. Reason: {last_failure_reason}")
                return WorkerExecutionOutcome(
                    success=False,
                    status="failed",
                    failure_type=(
                        "loop_detected"
                        if "loop" in (last_failure_reason or "")
                        else "reasoning_error"
                    ),
                    error_message=f"Step failed after {settings.MAX_RETRIES_PER_STEP} retries: {last_failure_reason}",
                    steps_executed=step_number,
                    token_usage=self.total_usage,
                    extracted_data=self.extracted_data,
                    visited_urls=self.visited_urls,
                )

        logger.warning(f"Step budget exhausted ({self.max_steps} steps) on tier={self.tier}.")
        return WorkerExecutionOutcome(
            success=False,
            status="failed",
            failure_type="reasoning_error",
            error_message=f"Step budget exhausted ({self.max_steps} steps) without finishing.",
            steps_executed=step_number,
            token_usage=self.total_usage,
            extracted_data=self.extracted_data,
            visited_urls=self.visited_urls,
        )


def _action_detail(action) -> str:
    """Returns a short human-readable description of the action taken."""
    name = action.action
    if name == "goto":
        return f"→ {action.url or '?'}"
    if name == "click":
        return f"element [{action.element_id}]"
    if name == "type":
        return f"text entry into [{action.element_id}] (value redacted)"
    if name == "select":
        return f"selection in [{action.element_id}] (value redacted)"
    if name == "press":
        return f"key '{action.key}'"
    if name == "scroll":
        return f"{action.direction or 'down'} {action.amount or 600}px"
    if name == "scroll_to":
        return f"to element [{action.element_id}]"
    if name == "hover":
        return f"over element [{action.element_id}]"
    if name == "extract":
        return f"'{(action.schema_or_description or '')[:40]}'"
    if name == "finish":
        return "DONE"
    return ""


def _redacted_action_for_trace(action) -> Dict[str, Any]:
    """Keep live traces useful without broadcasting entered values."""
    payload = action.model_dump()
    if action.action == "type" and action.text:
        payload["text"] = "[REDACTED]"
    if action.action == "select" and action.value:
        payload["value"] = "[REDACTED]"
    return payload
