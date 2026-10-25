import logging
from typing import Any, Dict, List, Optional
from app.schemas import AgentTier, FailureType

logger = logging.getLogger(__name__)

# Capability-related failure types that trigger escalation
CAPABILITY_FAILURE_TYPES = {
    "reasoning_error",
    "loop_detected",
    "no_state_change",
    "element_not_found",
}

# Non-capability failures (should NOT escalate)
NON_CAPABILITY_FAILURE_TYPES = {
    "blocked",
    "timeout",
    "quota_exceeded",
    "rate_limit_exceeded",
}

TIER_ORDER: List[AgentTier] = ["FAST", "GENERAL", "REASONING"]


class EscalationDecision:
    def __init__(
        self,
        should_escalate: bool,
        next_tier: Optional[AgentTier] = None,
        reason: str = "",
        carried_state: Optional[Dict[str, Any]] = None,
    ):
        self.should_escalate = should_escalate
        self.next_tier = next_tier
        self.reason = reason
        self.carried_state = carried_state or {}


def get_next_tier(current_tier: AgentTier) -> Optional[AgentTier]:
    """Returns the next escalated tier in sequence or None if already at REASONING."""
    try:
        idx = TIER_ORDER.index(current_tier)
        if idx + 1 < len(TIER_ORDER):
            return TIER_ORDER[idx + 1]
    except ValueError:
        pass
    return None


def evaluate_escalation(
    current_tier: AgentTier,
    failure_type: Optional[FailureType],
    error_message: Optional[str],
    extracted_data: Dict[str, Any],
    visited_urls: List[str],
    previous_escalations: int = 0,
) -> EscalationDecision:
    """Determines whether a failed run should escalate to a stronger agent tier."""
    if previous_escalations >= 2:
        return EscalationDecision(
            should_escalate=False,
            reason="Maximum escalation depth (FAST -> GENERAL -> REASONING) reached.",
        )

    # 1. Non-capability failures: DO NOT escalate
    if failure_type in NON_CAPABILITY_FAILURE_TYPES:
        return EscalationDecision(
            should_escalate=False,
            reason=f"Non-capability failure ({failure_type}): {error_message}. Halting escalation to avoid wasted API calls.",
        )

    if error_message and any(k in error_message.lower() for k in ["quota", "429", "resource_exhausted", "ratelimit", "rate limit"]):
        return EscalationDecision(
            should_escalate=False,
            reason=f"LLM API rate limit or quota exceeded: {error_message}. Halting escalation.",
        )

    # 2. Check if current tier can be escalated
    next_tier = get_next_tier(current_tier)
    if not next_tier:
        return EscalationDecision(
            should_escalate=False,
            reason=f"Already running at highest capability tier ({current_tier}).",
        )

    # 3. Check for capability failure indicators
    is_capability_issue = False
    escalation_reason = ""

    if failure_type in CAPABILITY_FAILURE_TYPES:
        is_capability_issue = True
        escalation_reason = f"Verification capability failure detected: '{failure_type}' ({error_message})"
    elif error_message and ("budget exhausted" in error_message.lower() or "retries exhausted" in error_message.lower()):
        is_capability_issue = True
        escalation_reason = f"Agent step budget exhausted on tier {current_tier}. Escalating to {next_tier} with deeper planning."

    if is_capability_issue:
        carried_state = {
            "initial_extracted_data": extracted_data,
            "initial_visited_urls": visited_urls,
            "failed_tier": current_tier,
            "failure_reason": escalation_reason,
        }
        logger.info(f"Escalation triggered: {current_tier} -> {next_tier}. Reason: {escalation_reason}")
        return EscalationDecision(
            should_escalate=True,
            next_tier=next_tier,
            reason=escalation_reason,
            carried_state=carried_state,
        )

    return EscalationDecision(
        should_escalate=False,
        reason=f"Failure '{failure_type}' does not meet capability escalation criteria.",
    )
