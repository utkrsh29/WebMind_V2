"""Persistent memory lifecycle tests with local SQLite and deterministic vectors."""

from uuid import uuid4
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.agent.memory import (
    ExperienceMemoryService,
    evaluate_applicability,
    is_attributable_memory_failure,
    redact_sensitive_text,
)
from app.agent.memory_schema import (
    ExperienceMemoryData,
    MemoryAction,
    MemoryAlternative,
    MemoryFailure,
    MemoryPageContext,
    MemoryRetrievalContext,
)
from app.agent.worker import AgentWorker
from app.agent.llm import TokenUsage
from app.browser.dom_snapshot import DOMSnapshot, capture_dom_snapshot
from app.browser.session import BrowserSession
from app.models import Base, ExperienceMemoryModel, MemoryAccessModel, MemoryEvidenceModel
from app.schemas import ActionSchema, ExpectedStateSchema, HumanBrowserActionRequest, PlannerOutputSchema


@pytest.fixture
async def memory_db(tmp_path):
    db_file = tmp_path / "memory-test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield engine, factory
    await engine.dispose()


def recovered_experience(**overrides):
    values = dict(
        task_description="Find a Pune hotel and apply free cancellation filter",
        task_type="filtering",
        website_domain="booking.example",
        page_url_pattern="/search/hotels",
        page_context=MemoryPageContext(
            page_title="Hotel search results",
            relevant_elements=["button: Filter", "checkbox: Free cancellation"],
            available_controls=["button", "checkbox"],
            page_state_summary="Hotel results with filter controls",
        ),
        action_attempted=MemoryAction(
            action_type="click", target_description="text Filter", selector_strategy="text locator"
        ),
        failure=MemoryFailure(
            failure_type="element_not_found",
            reason="Text locator did not match an interactive control",
            verification_evidence="No filter panel appeared",
        ),
        successful_alternative=MemoryAlternative(
            action_type="click",
            target_description="button Filter",
            selector_strategy="visible role locator",
            recovery_steps=["Re-inspect controls", "Click the visible Filter button"],
        ),
        outcome="recovered",
    )
    values.update(overrides)
    return ExperienceMemoryData(**values)


def retrieval_context(**overrides):
    values = dict(
        task_description="Find a Pune hotel with free cancellation and use the filter",
        task_type="filtering",
        website_domain="booking.example",
        page_url_pattern="/search/hotels",
        page_context=MemoryPageContext(
            page_title="Hotel search results",
            relevant_elements=["button: Filter", "checkbox: Free cancellation"],
            available_controls=["button", "checkbox"],
            page_state_summary="Hotel results with filter controls",
        ),
        intended_action="click",
    )
    values.update(overrides)
    return MemoryRetrievalContext(**values)


async def insert_experience(service, *, task_id=None, evidence_key=None, **overrides):
    item = recovered_experience(**overrides)
    result = await service.record_experience(
        item,
        task_id=task_id or str(uuid4()),
        evidence_key=evidence_key or f"initial:{uuid4()}",
        evidence_outcome="success" if item.outcome == "recovered" else item.outcome,
    )
    assert result
    return result


@pytest.mark.asyncio
async def test_failure_and_verified_alternative_are_persisted(memory_db, monkeypatch):
    _, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service)
    async with factory() as session:
        memory = await session.get(ExperienceMemoryModel, memory_id)
        assert memory.failure["failure_type"] == "element_not_found"
        assert memory.successful_alternative["target_description"] == "button Filter"
        assert memory.outcome == "recovered"
        assert memory.success_count == 1


@pytest.mark.asyncio
async def test_memory_survives_database_engine_restart(memory_db, monkeypatch):
    engine, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service)
    database_url = str(engine.url)
    await engine.dispose()
    restarted_engine = create_async_engine(database_url)
    restarted_factory = async_sessionmaker(restarted_engine, expire_on_commit=False)
    try:
        async with restarted_factory() as session:
            record = await session.get(ExperienceMemoryModel, memory_id)
            assert record is not None
            assert record.outcome == "recovered"
    finally:
        await restarted_engine.dispose()


@pytest.mark.asyncio
async def test_similar_task_retrieves_and_gates_memory(memory_db, monkeypatch):
    _, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service)
    retrieved = await service.retrieve_for_step(retrieval_context(), task_id="next-task", step_number=1)
    assert [item["memory"]["memory_id"] for item in retrieved] == [memory_id]
    assert retrieved[0]["gate"]["decision"] == "USE"
    assert retrieved[0]["included_in_planner"] is True


def test_unrelated_task_is_rejected():
    memory = _model()
    decision = evaluate_applicability(
        memory,
        retrieval_context(
            task_description="Write a poem about autumn",
            task_type="other",
            website_domain="poetry.example",
            page_url_pattern="/compose",
            page_context=MemoryPageContext(page_title="Poem editor", available_controls=["textarea"]),
        ),
        task_similarity=0.02,
    )
    assert decision.decision == "IGNORE"


def test_same_domain_can_use_and_cross_domain_is_cautious():
    memory = _model()
    same = evaluate_applicability(memory, retrieval_context(), task_similarity=0.95)
    cross = evaluate_applicability(
        memory,
        retrieval_context(website_domain="another-booking.example"),
        task_similarity=0.95,
    )
    assert same.decision == "USE"
    assert same.website_match is True
    assert cross.decision == "CAUTIOUS"
    assert cross.website_match is False


def test_cautious_guidance_requires_fresh_inspection():
    memory_payload = {
        "memory": {
            "memory_id": "memory-1",
            "outcome": "recovered",
            "reliability_score": 0.6,
            "page_context": {"page_title": "Hotel search"},
            "action_attempted": {"action_type": "click", "target_description": "Filter text"},
            "successful_alternative": {"action_type": "click", "target_description": "Filter icon"},
            "verified_workflow": [],
        },
        "gate": {"decision": "CAUTIOUS", "reason": "Inspect the current page."},
        "included_in_planner": True,
    }
    hints = AgentWorker._planner_memory_hints([memory_payload])
    assert len(hints) == 1
    assert "Re-identify all controls from this page" in hints[0]
    assert "CAUTIOUS" in hints[0]


def test_ignore_memory_is_not_passed_to_planner():
    memory_payload = {
        "memory": {"memory_id": "ignore-me"},
        "gate": {"decision": "IGNORE", "reason": "Unrelated"},
        "included_in_planner": False,
    }
    assert AgentWorker._planner_memory_hints([memory_payload]) == []


@pytest.mark.asyncio
async def test_verified_reuse_increases_reliability(memory_db, monkeypatch):
    _, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service)
    before = (await _read_memory(factory, memory_id)).reliability_score
    await service.retrieve_for_step(retrieval_context(), task_id="reuse-1", step_number=1)
    updated = await service.record_feedback(
        task_id="reuse-1", step_number=1, memory_ids=[memory_id], outcome="success",
        evidence={"verification_status": "success", "target_description": "button Filter"},
    )
    after = await _read_memory(factory, memory_id)
    assert updated == [memory_id]
    assert after.success_count == 2
    assert after.reliability_score > before


@pytest.mark.asyncio
async def test_attributable_misleading_reuse_decreases_reliability(memory_db, monkeypatch):
    _, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service)
    before = (await _read_memory(factory, memory_id)).reliability_score
    await service.retrieve_for_step(retrieval_context(), task_id="reuse-2", step_number=1)
    await service.record_feedback(
        task_id="reuse-2", step_number=1, memory_ids=[memory_id], outcome="failure",
        evidence={"failure_type": "element_not_found", "reason": "matching control disappeared"},
    )
    after = await _read_memory(factory, memory_id)
    assert after.failure_count == 1
    assert after.reliability_score < before


def test_unrelated_network_failures_do_not_penalize_memory():
    assert not is_attributable_memory_failure("timeout", "Network error: connection reset", True)
    assert not is_attributable_memory_failure("timeout", "Navigation timeout", False)
    assert is_attributable_memory_failure("element_not_found", "No matching visible control", True)


@pytest.mark.asyncio
async def test_feedback_is_deduplicated_per_access(memory_db, monkeypatch):
    _, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service)
    await service.retrieve_for_step(retrieval_context(), task_id="reuse-once", step_number=1)
    evidence = {"verification_status": "success"}
    await service.record_feedback(task_id="reuse-once", step_number=1, memory_ids=[memory_id], outcome="success", evidence=evidence)
    await service.record_feedback(task_id="reuse-once", step_number=1, memory_ids=[memory_id], outcome="success", evidence=evidence)
    record = await _read_memory(factory, memory_id)
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(MemoryEvidenceModel).where(MemoryEvidenceModel.memory_id == memory_id))
    assert record.success_count == 2
    assert count == 2  # one initial verified outcome, one reuse outcome


@pytest.mark.asyncio
async def test_memory_service_database_failure_is_nonfatal(monkeypatch):
    service = ExperienceMemoryService()
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    monkeypatch.setattr(service, "session_factory", None)
    assert await service.retrieve_for_step(retrieval_context(), task_id="failure", step_number=1) == []
    assert await service.record_experience(recovered_experience(), task_id="failure", evidence_key="x", evidence_outcome="success") is None


@pytest.mark.asyncio
async def test_embedding_timeout_falls_back_without_crashing(monkeypatch):
    import time
    from app.agent import memory as memory_module
    service = ExperienceMemoryService()
    monkeypatch.setattr(memory_module.settings, "MEMORY_EMBEDDING_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(memory_module, "compute_embedding_with_model", lambda _: (time.sleep(0.1), _vector())[1])
    vector, model = await service._embed("test")
    assert len(vector) == 384
    assert model == "hash-v1"


@pytest.mark.asyncio
async def test_duplicate_experiences_merge_instead_of_growing(memory_db, monkeypatch):
    _, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    first = await insert_experience(service, task_id="dup-1", evidence_key="dup-1")
    second = await insert_experience(service, task_id="dup-2", evidence_key="dup-2")
    assert first == second
    async with factory() as session:
        count = await session.scalar(select(func.count()).select_from(ExperienceMemoryModel))
    assert count == 1


def test_sensitive_values_are_redacted_before_storage():
    assert redact_sensitive_text("Contact me at jane@example.com") == "Contact me at [REDACTED_EMAIL]"
    assert "4111 1111 1111 1111" not in redact_sensitive_text("card 4111 1111 1111 1111")
    safe = ExperienceMemoryService._sanitize_experience(recovered_experience(
        task_description="Book this for jane@example.com",
        action_attempted=MemoryAction(action_type="type", target_description="email field", action_parameters={"value": "jane@example.com", "input_length": 17}),
    ))
    assert "jane@example.com" not in safe.task_description
    assert "value" not in safe.action_attempted.action_parameters


@pytest.mark.asyncio
async def test_full_local_retrieval_gate_reuse_feedback_and_restart(memory_db, monkeypatch):
    """Run 1 ingest → new service/engine retrieval → verified reuse feedback."""
    engine, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service, task_id="run-1", evidence_key="recovery:run-1")
    database_url = str(engine.url)
    await engine.dispose()

    second_engine = create_async_engine(database_url)
    second_factory = async_sessionmaker(second_engine, expire_on_commit=False)
    second_service = ExperienceMemoryService(second_factory)
    monkeypatch.setattr(second_service, "_embed", lambda _: _async_vector())
    try:
        found = await second_service.retrieve_for_step(retrieval_context(), task_id="run-2", step_number=1)
        assert found[0]["memory"]["memory_id"] == memory_id
        assert found[0]["gate"]["decision"] == "USE"
        assert await second_service.record_feedback(
            task_id="run-2", step_number=1, memory_ids=[memory_id], outcome="success",
            evidence={"verification_status": "success", "target_description": "button Filter"},
        ) == [memory_id]
        different = evaluate_applicability(
            await _read_memory(second_factory, memory_id),
            retrieval_context(
                task_description="Draft an email",
                website_domain="mail.example",
                page_url_pattern="/compose",
                page_context=MemoryPageContext(page_title="Compose email", available_controls=["textbox"]),
            ),
            task_similarity=0.05,
        )
        assert different.decision == "IGNORE"
        assert (await _read_memory(second_factory, memory_id)).success_count == 2
    finally:
        await second_engine.dispose()


@pytest.mark.asyncio
async def test_memory_api_filters_details_and_confirmed_deletion(memory_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from app.db import get_db
    from app.main import app

    _, factory = memory_db
    service = ExperienceMemoryService(factory)
    monkeypatch.setattr(service, "_embed", lambda _: _async_vector())
    memory_id = await insert_experience(service)

    async def override_db():
        async with factory() as session:
            yield session

    previous_override = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = override_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            listed = await client.get("/api/memories", params={"domain": "booking.example", "outcome": "recovered"})
            assert listed.status_code == 200
            assert listed.json()["total"] == 1
            detail = await client.get(f"/api/memories/{memory_id}")
            assert detail.status_code == 200
            assert detail.json()["failure"]["failure_type"] == "element_not_found"
            rejected_clear = await client.delete("/api/memories")
            assert rejected_clear.status_code == 400
            deleted = await client.delete(f"/api/memories/{memory_id}")
            assert deleted.status_code == 200
            cleared = await client.delete("/api/memories?confirm=true")
            assert cleared.status_code == 200
            assert cleared.json()["deleted_count"] == 0
    finally:
        if previous_override is None:
            app.dependency_overrides.pop(get_db, None)
        else:
            app.dependency_overrides[get_db] = previous_override


@pytest.mark.asyncio
async def test_worker_persists_failure_and_verified_recovery(memory_db, monkeypatch):
    _, factory = memory_db
    events = []

    async def callback(event, data):
        events.append((event, data))

    worker = AgentWorker(
        tier="GENERAL",
        task_query="Find Pune hotels with free cancellation",
        constraints={},
        session=None,
        task_id="worker-recovery-task",
        task_type="filtering",
        event_callback=callback,
    )
    worker.memory = ExperienceMemoryService(factory)
    monkeypatch.setattr(worker.memory, "_embed", lambda _: _async_vector())
    snapshot = DOMSnapshot(
        url="https://booking.example/search/hotels",
        title="Hotel search results",
        elements=[{"id": 4, "tag": "button", "role": "button", "name": "Filter"}],
        text_summary="hotel search results with filter button",
    )
    failure = {
        "action": MemoryAction(action_type="click", target_description="Filter text"),
        "snapshot": snapshot,
        "failure_type": "element_not_found",
        "reason": "Text locator did not match the control",
        "step_number": 2,
    }
    await worker._record_recovery(
        failure,
        MemoryAction(action_type="click", target_description="button Filter"),
        snapshot,
        3,
    )
    async with factory() as session:
        record = (await session.execute(select(ExperienceMemoryModel))).scalar_one()
        evidence = (await session.execute(select(MemoryEvidenceModel))).scalar_one()
        assert record.outcome == "recovered"
        assert record.successful_alternative["target_description"] == "button Filter"
        assert evidence.evidence["verification_status"] == "success"
    assert any(event == "memory_event" and data["stage"] == "learning_update" for event, data in events)


@pytest.mark.asyncio
async def test_human_browser_handoff_fills_live_page_and_rejects_stale_controls():
    browser = BrowserSession(headless=True)
    await browser.start()
    try:
        await browser.page.set_content("<title>Sign in</title><input type='email' placeholder='Guest email'>")
        worker = AgentWorker(
            tier="FAST", task_query="Continue a sign-in flow", constraints={},
            session=browser, task_id="handoff-test", task_type="form_fill",
        )
        worker._awaiting_user = True
        worker._browser_handoff_allowed = True
        first_state = await worker.get_browser_handoff_state()
        old_id = first_state["controls"][0]["element_id"]

        await browser.page.set_content("<title>Sign in</title><input type='email' placeholder='Account email'><input type='text' name='username'><input type='password' name='password'>")
        stale = await worker.perform_human_browser_action(HumanBrowserActionRequest(
            revision=first_state["revision"], action="type", element_id=old_id, text="guest@example.com",
        ))
        assert stale["stale"] is True
        assert "changed" in stale["message"]

        current_control = next(c for c in stale["state"]["controls"] if c["tag"] == "input")
        result = await worker.perform_human_browser_action(HumanBrowserActionRequest(
            revision=stale["state"]["revision"], action="type",
            element_id=current_control["element_id"], text="guest@example.com",
        ))
        assert result["ok"] is True
        assert await browser.page.locator("input[type='email']").input_value() == "guest@example.com"
        await browser.page.locator("input[name='username']").fill("guest-login")
        await browser.page.locator("input[type='password']").fill("correct-horse-secret")
        planner_snapshot = await capture_dom_snapshot(browser.page, include_som_screenshot=False)
        prompt_text = planner_snapshot.to_formatted_prompt()
        assert "guest@example.com" not in prompt_text
        assert "guest-login" not in prompt_text
        assert "correct-horse-secret" not in prompt_text
        assert "[REDACTED]" in prompt_text
        worker._awaiting_user = False
        worker._browser_handoff_allowed = False
        with pytest.raises(RuntimeError):
            await worker.get_browser_handoff_state()
    finally:
        await browser.close()


@pytest.mark.asyncio
async def test_three_run_local_browser_learning_scenario(memory_db, monkeypatch):
    """Local Playwright acceptance: fail/recover, reuse after restart, reject changed page."""
    import asyncio
    from app.agent import memory as memory_module
    from app.config import settings

    engine, factory = memory_db
    monkeypatch.setattr(settings, "MEMORY_ENABLED", True)
    monkeypatch.setattr(memory_module, "compute_embedding_with_model", lambda text: (memory_module._fallback_embedding(text), "hash-v1"))

    async def serve_page(reader, writer):
        try:
            request = await reader.readuntil(b"\r\n\r\n")
            path = request.split(b" ", 2)[1].decode("ascii")
            if path.startswith("/search/hotels"):
                body = ("<!doctype html><title>Hotel search results</title>"
                        "<main><h1>Pune hotel search</h1><button aria-label='Filter icon' "
                        "onclick=\"document.querySelector('#filters').hidden=false\">Filter</button>"
                        "<section id='filters' hidden><label><input type='checkbox'>Free cancellation</label>"
                        "</section></main>").encode()
            else:
                body = ("<!doctype html><title>Email composer</title><main><h1>Compose email</h1>"
                        "<textarea aria-label='Message'></textarea><button>Send</button></main>").encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\n" +
                         f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(serve_page, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    base = f"http://127.0.0.1:{port}"

    async def run_worker(run_number, task, route, fail_first=False, selected_id=None):
        browser = BrowserSession(headless=True)
        await browser.start()
        events = []
        try:
            await browser.page.goto(f"{base}{route}", wait_until="domcontentloaded")
            worker = AgentWorker(
                tier="FAST", task_query=task, constraints={}, session=browser,
                task_id=f"local-run-{run_number}", task_type="filtering" if "hotel" in task.lower() else "other",
                event_callback=lambda event, data: _capture_event(events, event, data),
            )
            worker.memory = ExperienceMemoryService(factory)
            calls = 0

            async def fake_plan(**kwargs):
                nonlocal calls
                calls += 1
                if route.endswith("compose"):
                    return PlannerOutputSchema(
                        thought="Unrelated task uses the live email composer page.",
                        next_action=ActionSchema(action="finish", result={"status": "observed"}),
                    ), TokenUsage()
                if fail_first and calls == 1:
                    return PlannerOutputSchema(
                        thought="Try the text filter label first.",
                        next_action=ActionSchema(action="click", element_id=9999),
                    ), TokenUsage()
                if calls <= (2 if fail_first else 1):
                    if selected_id is not None:
                        assert any(selected_id in hint for hint in kwargs.get("experience_hints", []))
                    element = next(el for el in kwargs["current_snapshot"].elements if "Filter" in (el.get("name") or ""))
                    return PlannerOutputSchema(
                        thought="Use the freshly observed filter control.",
                        next_action=ActionSchema(action="click", element_id=element["id"]),
                        expected_state=ExpectedStateSchema(text_present="Free cancellation"),
                        memory_ids_used=[selected_id] if selected_id else [],
                    ), TokenUsage()
                return PlannerOutputSchema(
                    thought="The filter panel was verified.",
                    next_action=ActionSchema(action="finish", result={"filter_open": True}),
                ), TokenUsage()

            worker.planner.plan_next_step = fake_plan
            return await worker.run(), events
        finally:
            await browser.close()

    try:
        task = "Find a Pune hotel and apply the free cancellation filter"
        run1, events1 = await run_worker(1, task, "/search/hotels", fail_first=True)
        assert run1.success
        async with factory() as session:
            learned = (await session.execute(select(ExperienceMemoryModel))).scalar_one()
            learned_id = learned.id
            assert learned.outcome == "recovered"
            assert learned.success_count == 1
        assert any(event == "memory_event" and data.get("stage") == "learning_update" for event, data in events1)

        # This fresh worker and service represent Run 2 after an application restart.
        run2, events2 = await run_worker(2, task, "/search/hotels", selected_id=learned_id)
        assert run2.success
        decisions2 = [data["decision"] for event, data in events2 if event == "memory_event" and data.get("stage") == "gate_decision"]
        assert "USE" in decisions2
        async with factory() as session:
            learned = await session.get(ExperienceMemoryModel, learned_id)
            assert learned.success_count == 2
            accesses = (await session.execute(select(MemoryAccessModel).where(
                MemoryAccessModel.task_id == "local-run-2",
                MemoryAccessModel.planner_selected.is_(True),
            ))).scalars().all()
            assert accesses

        # The same domain has a materially different route and controls for Run 3.
        run3, events3 = await run_worker(3, "Draft a short email to a colleague", "/compose")
        assert run3.success
        decisions3 = [data["decision"] for event, data in events3 if event == "memory_event" and data.get("stage") == "gate_decision"]
        assert all(decision in ("CAUTIOUS", "IGNORE") for decision in decisions3)
        assert "USE" not in decisions3
    finally:
        server.close()
        await server.wait_closed()
        await engine.dispose()


async def _capture_event(events, event, data):
    events.append((event, data))


def _vector():
    return [1.0, 0.0, 0.0], "test-v1"


async def _async_vector():
    return _vector()


def _model():
    from types import SimpleNamespace
    return SimpleNamespace(
        id="memory-model",
        task_type="filtering",
        domain="booking.example",
        page_url_pattern="/search/hotels",
        page_context={
            "page_title": "Hotel search results",
            "relevant_elements": ["button: Filter", "checkbox: Free cancellation"],
            "available_controls": ["button", "checkbox"],
        },
        successful_alternative={"action_type": "click", "target_description": "button Filter"},
        failure={"failure_type": "element_not_found"},
        verified_workflow=[],
        reliability_score=0.8,
        outcome="recovered",
        updated_at=datetime.now(timezone.utc),
    )


async def _read_memory(factory, memory_id):
    async with factory() as session:
        return await session.get(ExperienceMemoryModel, memory_id)
