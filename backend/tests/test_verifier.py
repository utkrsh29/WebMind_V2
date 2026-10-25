import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agent.verifier import BrowserVerifier
from app.browser.dom_snapshot import DOMSnapshot
from app.schemas import ActionSchema, ExpectedStateSchema


@pytest.mark.asyncio
async def test_verifier_url_and_text_checks():
    verifier = BrowserVerifier()
    mock_session = MagicMock()

    prev_snapshot = DOMSnapshot(
        url="https://duckduckgo.com/",
        title="DuckDuckGo",
        elements=[],
        text_summary="Search the web freely",
    )
    current_snapshot = DOMSnapshot(
        url="https://duckduckgo.com/?q=laptop",
        title="laptop at DuckDuckGo",
        elements=[{"id": 1, "tag": "a", "role": "link", "name": "Best Laptops", "rect": {}}],
        text_summary="Here are the top results for laptops with 16GB RAM",
    )

    action = ActionSchema(action="type", element_id=1, text="laptop", submit=True)
    expected = ExpectedStateSchema(url_contains="q=laptop", text_present="results for laptops")

    res = await verifier.verify(
        session=mock_session,
        action=action,
        expected_state=expected,
        previous_snapshot=prev_snapshot,
        current_snapshot=current_snapshot,
    )

    assert res.success is True
    assert res.failure_type is None


@pytest.mark.asyncio
async def test_verifier_detects_no_state_change():
    verifier = BrowserVerifier()
    mock_session = MagicMock()

    snapshot = DOMSnapshot(
        url="https://example.com/",
        title="Example Page",
        elements=[],
        text_summary="Static content that never changes",
    )

    action = ActionSchema(action="click", element_id=1)

    res = await verifier.verify(
        session=mock_session,
        action=action,
        expected_state=None,
        previous_snapshot=snapshot,
        current_snapshot=snapshot,  # Exact same state!
    )

    assert res.success is False
    assert res.failure_type == "no_state_change"


@pytest.mark.asyncio
async def test_verifier_detects_loop():
    verifier = BrowserVerifier()
    mock_session = MagicMock()

    snapshot = DOMSnapshot(
        url="https://example.com/search",
        title="Search",
        elements=[],
        text_summary="Search results",
    )
    action = ActionSchema(action="scroll", direction="down")

    # First attempt
    await verifier.verify(mock_session, action, None, None, snapshot)
    # Second attempt (same action on same state)
    res = await verifier.verify(mock_session, action, None, None, snapshot)

    assert res.success is False
    assert res.failure_type == "loop_detected"


@pytest.mark.asyncio
async def test_verifier_detects_bot_block():
    verifier = BrowserVerifier()
    mock_session = MagicMock()

    blocked_snapshot = DOMSnapshot(
        url="https://example.com/blocked",
        title="Security Check",
        elements=[],
        text_summary="Please complete the CAPTCHA to verify you are human.",
    )
    action = ActionSchema(action="goto", url="https://example.com/blocked")

    res = await verifier.verify(mock_session, action, None, None, blocked_snapshot)

    assert res.success is False
    assert res.failure_type == "blocked"
