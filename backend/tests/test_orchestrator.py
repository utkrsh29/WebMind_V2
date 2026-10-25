import pytest
from app.orchestrator.escalation import evaluate_escalation, get_next_tier


def test_tier_escalation_sequence():
    assert get_next_tier("FAST") == "GENERAL"
    assert get_next_tier("GENERAL") == "REASONING"
    assert get_next_tier("REASONING") is None


def test_capability_failure_escalates_with_state():
    carried_data = {"laptops_found": ["Model X", "Model Y"]}
    visited = ["https://site1.com", "https://site2.com"]

    decision = evaluate_escalation(
        current_tier="FAST",
        failure_type="reasoning_error",
        error_message="Model could not resolve multi-column table",
        extracted_data=carried_data,
        visited_urls=visited,
        previous_escalations=0,
    )

    assert decision.should_escalate is True
    assert decision.next_tier == "GENERAL"
    assert decision.carried_state["initial_extracted_data"] == carried_data
    assert decision.carried_state["initial_visited_urls"] == visited


def test_budget_exhaustion_escalates():
    decision = evaluate_escalation(
        current_tier="GENERAL",
        failure_type=None,
        error_message="Agent step budget exhausted (25 steps) on tier GENERAL.",
        extracted_data={},
        visited_urls=[],
        previous_escalations=1,
    )

    assert decision.should_escalate is True
    assert decision.next_tier == "REASONING"


def test_non_capability_failure_does_not_escalate():
    # Blocked by CAPTCHA / bot wall should NOT escalate
    decision = evaluate_escalation(
        current_tier="FAST",
        failure_type="blocked",
        error_message="Page presents a CAPTCHA requiring human intervention.",
        extracted_data={},
        visited_urls=[],
        previous_escalations=0,
    )

    assert decision.should_escalate is False
    assert decision.next_tier is None
    assert "Halting escalation" in decision.reason
