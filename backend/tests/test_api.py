import pytest
from httpx import ASGITransport, AsyncClient
from app.db import init_db
from app.main import app


@pytest.fixture(autouse=True)
async def setup_test_db():
    await init_db()


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/health")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ok"
        assert data["app"] == "WebMind"


@pytest.mark.asyncio
async def test_create_task_and_list(monkeypatch):
    from unittest.mock import AsyncMock
    from app.orchestrator.orchestrator import orchestrator
    monkeypatch.setattr(orchestrator, "execute_task", AsyncMock())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Create a task
        create_res = await client.post(
            "/api/tasks",
            json={"query": "Find laptops under 70k with 16GB RAM"},
        )
        assert create_res.status_code == 200
        data = create_res.json()
        assert "task_id" in data
        task_id = data["task_id"]

        # Fetch task details
        detail_res = await client.get(f"/api/tasks/{task_id}")
        assert detail_res.status_code == 200
        task_data = detail_res.json()
        assert task_data["id"] == task_id
        assert task_data["query"] == "Find laptops under 70k with 16GB RAM"

        # Check list endpoint
        list_res = await client.get("/api/tasks")
        assert list_res.status_code == 200
        tasks = list_res.json()["tasks"]
        assert any(t["id"] == task_id for t in tasks)

        # Check metrics endpoint
        metrics_res = await client.get("/api/metrics")
        assert metrics_res.status_code == 200
        metrics = metrics_res.json()
        assert "success_rate" in metrics
        assert "total_tasks" in metrics
