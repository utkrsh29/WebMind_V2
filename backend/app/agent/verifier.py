import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple
from app.browser.dom_snapshot import DOMSnapshot
from app.browser.session import BrowserSession
from app.schemas import ActionSchema, ExpectedStateSchema, FailureType

logger = logging.getLogger(__name__)

# Keywords indicating bot detection or wall
BLOCKED_KEYWORDS = [
    "captcha",
    "cf-browser-verification",
    "are you a human",
    "verify you are human",
    "access denied",
    "incident id",
    "please log in to continue",
    "sign in to continue",
    "bot detection",
    "unusual traffic",
]


class VerificationResult:
    def __init__(
        self,
        success: bool,
        failure_type: Optional[FailureType] = None,
        reason: str = "",
        state_signature: str = "",
    ):
        self.success = success
        self.failure_type = failure_type
        self.reason = reason
        self.state_signature = state_signature

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "failure_type": self.failure_type,
            "reason": self.reason,
            "state_signature": self.state_signature,
        }


def compute_state_signature(snapshot: DOMSnapshot) -> str:
    """Creates a deterministic hash of page state to detect stalled loops."""
    raw = f"{snapshot.url}|{snapshot.title}|{len(snapshot.elements)}|{snapshot.text_summary[:400]}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


class BrowserVerifier:
    def __init__(self):
        self.state_history: List[Tuple[str, str]] = []  # List of (action_key, state_signature)

    def check_for_loops(self, action_key: str, current_state_sig: str) -> bool:
        """Detects if the agent executed the same action in the same state previously."""
        count = sum(1 for (act, sig) in self.state_history if act == action_key and sig == current_state_sig)
        return count >= 2

    async def verify(
        self,
        session: BrowserSession,
        action: ActionSchema,
        expected_state: Optional[ExpectedStateSchema],
        previous_snapshot: Optional[DOMSnapshot],
        current_snapshot: DOMSnapshot,
    ) -> VerificationResult:
        """Verifies whether the executed action produced the expected browser state."""
        current_sig = compute_state_signature(current_snapshot)
        action_key = f"{action.action}:{action.element_id}:{action.url}:{action.text}"
        self.state_history.append((action_key, current_sig))

        # 1. Check for Loop Detection
        if self.check_for_loops(action_key, current_sig):
            return VerificationResult(
                success=False,
                failure_type="loop_detected",
                reason=f"Agent repeated identical action '{action.action}' on unchanged page state.",
                state_signature=current_sig,
            )

        # 2. Check for Bot Blocks / CAPTCHA / Paywall
        text_lower = current_snapshot.text_summary.lower()
        title_lower = current_snapshot.title.lower()
        if any(kw in text_lower or kw in title_lower for kw in BLOCKED_KEYWORDS):
            return VerificationResult(
                success=False,
                failure_type="blocked",
                reason="Page presents a CAPTCHA, bot block, or login wall requiring human intervention.",
                state_signature=current_sig,
            )

        # 3. If action is finish or ask_user, programmatic state change is not required
        if action.action in ("finish", "ask_user"):
            return VerificationResult(
                success=True,
                reason="Terminal or user interaction step succeeded.",
                state_signature=current_sig,
            )

        # 4. Check for No State Change on navigation or interaction
        if previous_snapshot is not None:
            prev_sig = compute_state_signature(previous_snapshot)
            if action.action in ("goto", "click", "type") and prev_sig == current_sig:
                # In some cases clicking a tab or typing may not change DOM if failed
                return VerificationResult(
                    success=False,
                    failure_type="no_state_change",
                    reason=f"Action '{action.action}' produced no discernible state or DOM change.",
                    state_signature=current_sig,
                )

        # 5. Programmatic checks based on expected_state
        if expected_state:
            # Check url_contains
            if expected_state.url_contains:
                if expected_state.url_contains.lower() not in current_snapshot.url.lower():
                    return VerificationResult(
                        success=False,
                        failure_type="wrong_page",
                        reason=f"Expected URL to contain '{expected_state.url_contains}', but was '{current_snapshot.url}'.",
                        state_signature=current_sig,
                    )

            # Check text_present
            if expected_state.text_present:
                if expected_state.text_present.lower() not in text_lower:
                    return VerificationResult(
                        success=False,
                        failure_type="element_not_found",
                        reason=f"Expected text '{expected_state.text_present}' was not found in page content.",
                        state_signature=current_sig,
                    )

            # Check element_present
            if expected_state.element_present:
                try:
                    present = await session.page.locator(expected_state.element_present).count()
                    if present == 0:
                        return VerificationResult(
                            success=False,
                            failure_type="element_not_found",
                            reason=f"Expected element selector '{expected_state.element_present}' not present in DOM.",
                            state_signature=current_sig,
                        )
                except Exception:
                    # Ignore invalid selector syntax
                    pass

        return VerificationResult(
            success=True,
            reason="Action successfully executed and passed state verification.",
            state_signature=current_sig,
        )
