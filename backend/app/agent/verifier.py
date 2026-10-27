import hashlib
import logging
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple
from app.browser.dom_snapshot import DOMSnapshot
from app.browser.session import BrowserSession
from app.schemas import ActionSchema, ExpectedStateSchema, FailureType

logger = logging.getLogger(__name__)

# Keywords indicating bot detection / access block
BLOCKED_KEYWORDS = [
    "captcha",
    "cf-browser-verification",
    "are you a human",
    "verify you are human",
    "access denied",
    "incident id",
    "bot detection",
    "unusual traffic",
    "security check",
    "just a moment",  # Cloudflare
]

# Keywords indicating login / authentication wall
LOGIN_KEYWORDS = [
    "please log in",
    "please sign in",
    "sign in to continue",
    "please log in to continue",
    "you must be logged in",
    "login required",
    "authentication required",
    "create an account to continue",
    "log in to your account",
    "sign up to continue",
]

# Actions that inherently don't change DOM state
NO_CHANGE_EXEMPT_ACTIONS = {"scroll", "scroll_to", "hover", "wait", "screenshot", "extract", "press"}


class VerificationResult:
    def __init__(
        self,
        success: bool,
        failure_type: Optional[FailureType] = None,
        reason: str = "",
        state_signature: str = "",
        change_summary: Optional[str] = None,
    ):
        self.success = success
        self.failure_type = failure_type
        self.reason = reason
        self.state_signature = state_signature
        self.change_summary = change_summary  # What actually changed

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "failure_type": self.failure_type,
            "reason": self.reason,
            "state_signature": self.state_signature,
            "change_summary": self.change_summary,
        }


def compute_state_signature(snapshot: DOMSnapshot) -> str:
    """Creates a deterministic hash of page state to detect stalled loops."""
    form_state = [
        (el.get("id"), el.get("value", ""), el.get("checked"))
        for el in snapshot.elements
        if el.get("value") or el.get("checked") is not None
    ]
    raw = f"{snapshot.url}|{snapshot.title}|{len(snapshot.elements)}|{snapshot.text_summary[:500]}|{form_state}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def compute_diff_summary(prev: DOMSnapshot, curr: DOMSnapshot) -> str:
    """Generates a human-readable diff of what changed between two snapshots."""
    parts = []
    if prev.url != curr.url:
        parts.append(f"URL changed: {prev.url} → {curr.url}")
    if prev.title != curr.title:
        parts.append(f"Title changed: '{prev.title}' → '{curr.title}'")

    prev_elem_ids = {el["id"] for el in prev.elements}
    curr_elem_ids = {el["id"] for el in curr.elements}
    added = len(curr_elem_ids - prev_elem_ids)
    removed = len(prev_elem_ids - curr_elem_ids)
    if added or removed:
        parts.append(f"Elements: +{added} added, -{removed} removed")

    prev_values = {
        el.get("id"): el.get("value", "")
        for el in prev.elements
        if el.get("value")
    }
    curr_values = {
        el.get("id"): el.get("value", "")
        for el in curr.elements
        if el.get("value")
    }
    changed_values = sum(
        1 for element_id in (prev_values.keys() | curr_values.keys())
        if prev_values.get(element_id) != curr_values.get(element_id)
    )
    if changed_values:
        parts.append(f"Input values changed for {changed_values} element(s)")

    # Text diff (simple word-level)
    prev_words = set(prev.text_summary.lower().split())
    curr_words = set(curr.text_summary.lower().split())
    new_words = curr_words - prev_words
    if len(new_words) > 3:
        sample = list(new_words)[:8]
        parts.append(f"New content keywords: {', '.join(sample)}")

    return "; ".join(parts) if parts else "Minor or no visible change"


class BrowserVerifier:
    def __init__(self):
        # Track (action_key, state_signature) pairs to detect loops
        self.state_history: List[Tuple[str, str]] = []
        # Track signatures for consecutive-same-state detection
        self._sig_counter: Counter = Counter()

    def _action_key(self, action: ActionSchema) -> str:
        return f"{action.action}:{action.element_id}:{action.url}:{action.text}"

    def check_for_loops(self, action_key: str, current_sig: str) -> bool:
        """Detects if the agent repeated identical action in the same state ≥ 2 times."""
        count = sum(1 for (act, sig) in self.state_history if act == action_key and sig == current_sig)
        return count >= 2

    async def verify(
        self,
        session: BrowserSession,
        action: ActionSchema,
        expected_state: Optional[ExpectedStateSchema],
        previous_snapshot: Optional[DOMSnapshot],
        current_snapshot: DOMSnapshot,
        action_succeeded: bool = True,
        action_message: str = "",
    ) -> VerificationResult:
        """Verifies whether the executed action produced the expected browser state."""
        current_sig = compute_state_signature(current_snapshot)
        action_key = self._action_key(action)
        self.state_history.append((action_key, current_sig))
        self._sig_counter[current_sig] += 1

        text_lower = current_snapshot.text_summary.lower()
        title_lower = current_snapshot.title.lower()

        # ── 1. Login wall detection ───────────────────────────────────────────
        if any(kw in text_lower or kw in title_lower for kw in LOGIN_KEYWORDS):
            return VerificationResult(
                success=False,
                failure_type="login_required",
                reason="Page requires authentication. Agent should use ask_user(is_login=True) to get credentials.",
                state_signature=current_sig,
            )

        # ── 2. Bot block / CAPTCHA detection ─────────────────────────────────
        if any(kw in text_lower or kw in title_lower for kw in BLOCKED_KEYWORDS):
            return VerificationResult(
                success=False,
                failure_type="blocked",
                reason="Page presents a CAPTCHA, bot block, or access barrier requiring human intervention.",
                state_signature=current_sig,
            )

        # ── 3. Terminal actions always succeed ───────────────────────────────
        if action.action in ("finish", "ask_user"):
            return VerificationResult(
                success=True,
                reason="Terminal or user interaction step completed.",
                state_signature=current_sig,
            )

        # Never infer a successful browser action from the page snapshot when
        # the executor reported that the action itself failed.
        if not action_succeeded:
            message = action_message or f"The {action.action} action did not complete."
            lowered_message = message.lower()
            if "timeout" in lowered_message or "timed out" in lowered_message:
                failure_type = "timeout"
            elif action.action == "type" and "did not retain" in lowered_message:
                failure_type = "no_state_change"
            elif action.action in ("type", "click", "select"):
                failure_type = "element_not_found"
            else:
                failure_type = "no_state_change"
            return VerificationResult(
                success=False,
                failure_type=failure_type,
                reason=message,
                state_signature=current_sig,
            )

        # execute_action verifies typing by reading the exact target control's
        # live value after fill(). Page text, a guessed selector, or a page-wide
        # DOM diff cannot reliably verify values in booking forms and portals.
        if action.action == "type":
            change_summary = (
                compute_diff_summary(previous_snapshot, current_snapshot)
                if previous_snapshot else None
            )
            return VerificationResult(
                success=True,
                reason="The target field retained the requested value.",
                state_signature=current_sig,
                change_summary=change_summary,
            )

        # ── 4. Loop detection ─────────────────────────────────────────────────
        # Repeating a read-only extraction on an unchanged page is still a loop:
        # the worker otherwise marks every extract successful and can spend its
        # entire step budget narrating the same page instead of acting.
        if self.check_for_loops(action_key, current_sig):
            loop_reason = (
                "Repeated extract on an unchanged page. Do not extract again; use an observed browser action, "
                "finish if the requested goal is already verified, or ask the user for help."
                if action.action == "extract"
                else f"Agent repeated identical action '{action.action}' on unchanged page state."
            )
            return VerificationResult(
                success=False,
                failure_type="loop_detected",
                reason=loop_reason,
                state_signature=current_sig,
            )

        # ── 5. No-state-change check (only for DOM-mutating actions) ─────────
        if previous_snapshot is not None and action.action not in NO_CHANGE_EXEMPT_ACTIONS:
            prev_sig = compute_state_signature(previous_snapshot)
            if prev_sig == current_sig:
                diff = compute_diff_summary(previous_snapshot, current_snapshot)
                return VerificationResult(
                    success=False,
                    failure_type="no_state_change",
                    reason=(
                        f"Action '{action.action}' produced no DOM/URL change. "
                        f"Diff: {diff}. Try a different element or strategy."
                    ),
                    state_signature=current_sig,
                    change_summary=diff,
                )

        # ── 6. Expected state checks ──────────────────────────────────────────
        if expected_state:
            # Third-party date pickers and other custom widgets often expose
            # different DOM roles/selectors than the planner guessed. If a click
            # demonstrably changed the live page, replan from that observation
            # instead of rejecting the action solely for a guessed expectation.
            click_changed_page = (
                action.action == "click"
                and previous_snapshot is not None
                and compute_state_signature(previous_snapshot) != current_sig
            )
            if expected_state.url_contains:
                if expected_state.url_contains.lower() not in current_snapshot.url.lower():
                    return VerificationResult(
                        success=False,
                        failure_type="wrong_page",
                        reason=(
                            f"Expected URL to contain '{expected_state.url_contains}', "
                            f"but current URL is '{current_snapshot.url}'."
                        ),
                        state_signature=current_sig,
                    )

            # Typing is verified against the target field in execute_action;
            # these page summaries can omit values from booking form frames.
            if expected_state.text_present and action.action not in ("extract", "type"):
                expected_text = expected_state.text_present.lower()
                visible_text_found = expected_text in text_lower
                if not visible_text_found and not click_changed_page:
                    return VerificationResult(
                        success=False,
                        failure_type="element_not_found",
                        reason=f"Expected text '{expected_state.text_present}' not found on page.",
                        state_signature=current_sig,
                    )

            if expected_state.element_present:
                try:
                    count = await session.page.locator(expected_state.element_present).count()
                    if count == 0 and not click_changed_page:
                        return VerificationResult(
                            success=False,
                            failure_type="element_not_found",
                            reason=f"Expected element '{expected_state.element_present}' not present in DOM.",
                            state_signature=current_sig,
                        )
                except Exception:
                    pass  # Ignore invalid selectors

        # ── 7. Generate change summary for success ────────────────────────────
        change_summary = None
        if previous_snapshot:
            change_summary = compute_diff_summary(previous_snapshot, current_snapshot)

        return VerificationResult(
            success=True,
            reason="Action executed and passed state verification.",
            state_signature=current_sig,
            change_summary=change_summary,
        )
