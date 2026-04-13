"""Tests for MergeGate API routes."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from api.deps import get_current_agent
from api.main import app


@pytest.fixture
def mock_agent():
    return {
        "id": "01234567-0123-7890-abcd-0123456789ab",
        "agent_name": "TestAgent",
        "model": "test-model",
    }


@pytest.fixture
async def mg_client(mock_agent):
    """Test client with auth override and mocked DB for MergeGate routes."""
    from httpx import AsyncClient, ASGITransport

    mock_conn = AsyncMock()
    mock_conn.fetch = AsyncMock(return_value=[])
    mock_conn.fetchrow = AsyncMock(return_value=None)
    mock_conn.fetchval = AsyncMock(return_value=0)
    mock_conn.execute = AsyncMock()

    # Support db.transaction() context manager
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    mock_conn.transaction = MagicMock(return_value=mock_tx)

    mock_cm = AsyncMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_cm)
    app.state.pool = mock_pool

    app.dependency_overrides[get_current_agent] = lambda: mock_agent

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        yield ac, mock_conn

    app.dependency_overrides.pop(get_current_agent, None)


class TestTaskListing:
    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)

        resp = await client.get("/api/v1/mergegate/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_list_tasks_returns_items(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": "mg_task_0001",
                    "title": "Fix Cache Bug",
                    "difficulty": "medium",
                    "category": ["bugfix"],
                    "is_solvable": True,
                    "max_duration_s": 600,
                }
            ]
        )
        mock_conn.fetchval = AsyncMock(return_value=1)

        resp = await client.get("/api/v1/mergegate/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["id"] == "mg_task_0001"
        assert data["items"][0]["title"] == "Fix Cache Bug"
        assert data["items"][0]["difficulty"] == "medium"
        assert data["items"][0]["category"] == ["bugfix"]
        assert data["total"] == 1

    @pytest.mark.asyncio
    async def test_list_tasks_pagination(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=25)

        resp = await client.get("/api/v1/mergegate/tasks?limit=5&offset=10")
        assert resp.status_code == 200
        data = resp.json()
        assert data["limit"] == 5
        assert data["offset"] == 10
        assert data["total"] == 25

    @pytest.mark.asyncio
    async def test_list_tasks_filter_difficulty(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)

        resp = await client.get("/api/v1/mergegate/tasks?difficulty=hard")
        assert resp.status_code == 200
        # Verify the query was called (we can't easily check SQL params with mocks,
        # but at least verify the endpoint accepts the filter without error)

    @pytest.mark.asyncio
    async def test_list_tasks_requires_auth(self):
        """Without auth override, should get 401."""
        from httpx import AsyncClient, ASGITransport

        mock_conn = AsyncMock()
        mock_conn.fetchval = AsyncMock(return_value=1)
        mock_cm = AsyncMock()
        mock_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock(return_value=mock_cm)
        app.state.pool = mock_pool

        # Remove any auth override
        app.dependency_overrides.pop(get_current_agent, None)

        async with AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        ) as ac:
            resp = await ac.get("/api/v1/mergegate/tasks")
            assert resp.status_code == 401


class TestTaskDetail:
    @pytest.mark.asyncio
    async def test_get_task_found(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "mg_task_0001",
                "title": "Fix Cache Bug",
                "difficulty": "medium",
                "category": ["bugfix"],
                "description": "Template description",
                "is_solvable": True,
                "max_duration_s": 600,
                "is_active": True,
            }
        )

        resp = await client.get("/api/v1/mergegate/tasks/mg_task_0001")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == "mg_task_0001"
        # Should NOT include spec_text or variant_id (anti-cherry-picking)
        assert "spec_text" not in data
        assert "variant_id" not in data

    @pytest.mark.asyncio
    async def test_get_task_not_found(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(return_value=None)

        resp = await client.get("/api/v1/mergegate/tasks/mg_task_9999")
        assert resp.status_code == 404


class TestSessionCreation:
    @pytest.mark.asyncio
    async def test_create_session_with_task_id(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        _ = mock_agent["id"]  # ensure agent context is available

        # Mock variant lookup
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                # First call: find a variant for the task
                {
                    "id": "mg_task_0001_v001",
                    "task_id": "mg_task_0001",
                    "spec_text": "Fix the cache bug in src/cache.py...",
                    "resolved_checks": {"tests": ["pytest tests/"]},
                    "repo_snapshot": "file:///var/variants/mg_task_0001_v001.tar.gz",
                },
                # Second call: get task max_duration_s
                {
                    "max_duration_s": 600,
                },
            ]
        )
        mock_conn.execute = AsyncMock()

        resp = await client.post(
            "/api/v1/mergegate/sessions",
            json={"task_id": "mg_task_0001"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "session_id" in data
        assert data["variant_id"] == "mg_task_0001_v001"
        assert "spec_text" in data
        assert data["spec_text"] == "Fix the cache bug in src/cache.py..."
        assert "repo_download_url" in data
        assert "deadline" in data
        assert "submission_contract" in data
        assert "submit_url" in data["submission_contract"]

    @pytest.mark.asyncio
    async def test_create_session_use_next(self, mg_client, mock_agent):
        client, mock_conn = mg_client

        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {
                    "id": "mg_task_0002_v003",
                    "task_id": "mg_task_0002",
                    "spec_text": "Refactor the auth module...",
                    "resolved_checks": {"tests": ["pytest"]},
                    "repo_snapshot": "file:///var/variants/mg_task_0002_v003.tar.gz",
                },
                {"max_duration_s": 300},
            ]
        )
        mock_conn.execute = AsyncMock()

        resp = await client.post(
            "/api/v1/mergegate/sessions",
            json={"use_next": True},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["variant_id"] == "mg_task_0002_v003"

    @pytest.mark.asyncio
    async def test_create_session_with_prediction(self, mg_client, mock_agent):
        client, mock_conn = mg_client

        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {
                    "id": "mg_task_0001_v001",
                    "task_id": "mg_task_0001",
                    "spec_text": "Fix the cache bug...",
                    "resolved_checks": {},
                    "repo_snapshot": "file:///var/variants/t.tar.gz",
                },
                {"max_duration_s": 600},
            ]
        )
        mock_conn.execute = AsyncMock()

        resp = await client.post(
            "/api/v1/mergegate/sessions",
            json={
                "task_id": "mg_task_0001",
                "prediction": {
                    "confidence": 0.85,
                    "reasoning": "Standard cache bug",
                    "estimated_difficulty": "medium",
                },
            },
        )
        assert resp.status_code == 201
        # Verify execute was called for prediction insert
        # (at least 3 calls: session insert, prediction insert, event insert)
        assert mock_conn.execute.call_count >= 3

    @pytest.mark.asyncio
    async def test_create_session_no_variant_404(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(return_value=None)

        resp = await client.post(
            "/api/v1/mergegate/sessions",
            json={"task_id": "mg_task_9999"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_create_session_no_task_no_next_400(self, mg_client):
        client, mock_conn = mg_client

        resp = await client.post(
            "/api/v1/mergegate/sessions",
            json={},
        )
        assert resp.status_code == 400


class TestSubmission:
    @pytest.mark.asyncio
    async def test_submit_patch_success(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        agent_id = mock_agent["id"]

        # Mock: session exists, belongs to agent, is running
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": agent_id,
                "status": "running",
                "variant_id": "mg_task_0001_v001",
            }
        )
        mock_conn.execute = AsyncMock()

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/submit",
            json={
                "submission_mode": "patch",
                "patch_text": "diff --git a/src/cache.py b/src/cache.py\n--- a/src/cache.py\n+++ b/src/cache.py\n@@ -1 +1 @@\n-old\n+new",
                "proof_bundle": {
                    "schema_version": "1.0",
                    "tests_run": [{"name": "test_cache", "passed": True}],
                    "files_changed": [
                        {"path": "src/cache.py", "change_type": "modified", "summary": "Fixed TTL"}
                    ],
                    "assumptions": ["TTL is in seconds"],
                    "correctness_argument": "The bug was a strict inequality check that should have been non-strict. Changed > to >= on line 42.",
                    "rollback_plan": "revert commit",
                    "residual_risks": ["concurrent access"],
                    "not_verified": ["load testing"],
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "scoring"
        assert "session_id" in data

    @pytest.mark.asyncio
    async def test_submit_refusal(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        agent_id = mock_agent["id"]

        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": agent_id,
                "status": "running",
                "variant_id": "mg_task_0001_v001",
            }
        )
        mock_conn.execute = AsyncMock()

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/submit",
            json={
                "submission_mode": "refusal",
                "proof_bundle": {
                    "schema_version": "1.0",
                    "submission_mode": "refusal",
                    "correctness_argument": "This task cannot be completed because the constraints are contradictory - it requires network access but the sandbox has no network.",
                },
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "scoring"

    @pytest.mark.asyncio
    async def test_submit_wrong_session_404(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(return_value=None)

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-nosession1/submit",
            json={
                "submission_mode": "patch",
                "patch_text": "diff...",
                "proof_bundle": {"schema_version": "1.0"},
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_submit_not_owner_404(self, mg_client, mock_agent):
        client, mock_conn = mg_client

        # Session exists but belongs to different agent
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": "99999999-9999-9999-9999-999999999999",
                "status": "running",
                "variant_id": "mg_task_0001_v001",
            }
        )

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/submit",
            json={
                "submission_mode": "patch",
                "patch_text": "diff...",
                "proof_bundle": {"schema_version": "1.0"},
            },
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_submit_already_submitted_409(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        agent_id = mock_agent["id"]

        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": agent_id,
                "status": "submitted",  # already submitted
                "variant_id": "mg_task_0001_v001",
            }
        )

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/submit",
            json={
                "submission_mode": "patch",
                "patch_text": "diff...",
                "proof_bundle": {"schema_version": "1.0"},
            },
        )
        assert resp.status_code == 409


class TestResult:
    @pytest.mark.asyncio
    async def test_get_result_completed(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        agent_id = mock_agent["id"]

        # Mock session lookup
        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                # Session row
                {
                    "id": "01234567-0123-7890-abcd-session00001",
                    "agent_id": agent_id,
                    "status": "completed",
                },
                # Result row
                {
                    "session_id": "01234567-0123-7890-abcd-session00001",
                    "task_success": True,
                    "mergeable": True,
                    "hidden_tests_passed": 10,
                    "hidden_tests_total": 10,
                    "regressions_found": 0,
                    "approval_proxy_score": 0.85,
                    "proof_completeness": 0.90,
                    "review_cost_proxy": 4.2,
                    "review_cost_confidence": "medium",
                    "confidence_declared": 0.85,
                    "confidence_gap": -0.15,
                    "failure_class": None,
                    "failure_severity": None,
                    "failure_detail": None,
                    "failure_signature": None,
                    "is_silent_failure": False,
                    "correctly_refused": None,
                    "refusal_quality": None,
                    "quality_floor_passed": True,
                    "safety_floor_passed": True,
                    "scored_at": "2026-04-10T22:00:00+00:00",
                },
            ]
        )

        resp = await client.get(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/result"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "01234567-0123-7890-abcd-session00001"
        assert data["status"] == "completed"
        assert data["task_success"] is True
        assert data["mergeable"] is True
        assert data["proof_completeness"] == 0.90

    @pytest.mark.asyncio
    async def test_get_result_pending(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        agent_id = mock_agent["id"]

        mock_conn.fetchrow = AsyncMock(
            side_effect=[
                {
                    "id": "01234567-0123-7890-abcd-session00001",
                    "agent_id": agent_id,
                    "status": "running",
                },
                None,  # No result yet
            ]
        )

        resp = await client.get(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/result"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "running"
        assert data["task_success"] is None

    @pytest.mark.asyncio
    async def test_get_result_not_found(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(return_value=None)

        resp = await client.get(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-nosession1/result"
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_result_not_owner(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": "99999999-9999-9999-9999-999999999999",
                "status": "completed",
            }
        )

        resp = await client.get(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/result"
        )
        assert resp.status_code == 404


class TestReflection:
    @pytest.mark.asyncio
    async def test_reflect_success(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        agent_id = mock_agent["id"]

        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": agent_id,
                "status": "completed",
            }
        )
        mock_conn.execute = AsyncMock()

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/reflect",
            json={
                "was_surprised": True,
                "failure_explanation": "Missed the unicode edge case",
                "root_cause_guess": "edge_case_miss",
                "would_change": "Add unicode normalization step",
                "updated_confidence": 0.6,
            },
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_reflect_not_owner(self, mg_client, mock_agent):
        client, mock_conn = mg_client
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": "99999999-9999-9999-9999-999999999999",
                "status": "completed",
            }
        )

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/reflect",
            json={"was_surprised": False},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_reflect_session_still_running(self, mg_client, mock_agent):
        """Reflection should only be allowed after submission or completion."""
        client, mock_conn = mg_client
        agent_id = mock_agent["id"]
        mock_conn.fetchrow = AsyncMock(
            return_value={
                "id": "01234567-0123-7890-abcd-session00001",
                "agent_id": agent_id,
                "status": "running",
            }
        )

        resp = await client.post(
            "/api/v1/mergegate/sessions/01234567-0123-7890-abcd-session00001/reflect",
            json={"was_surprised": False},
        )
        assert resp.status_code == 409


class TestRecentSessions:
    @pytest.mark.asyncio
    async def test_recent_empty(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_conn.fetchval = AsyncMock(return_value=0)

        resp = await client.get("/api/v1/mergegate/sessions/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_recent_with_results(self, mg_client):
        client, mock_conn = mg_client
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": "01234567-0123-7890-abcd-session00001",
                    "variant_id": "mg_task_0001_v001",
                    "status": "completed",
                    "task_success": True,
                    "mergeable": True,
                    "proof_completeness": 0.88,
                    "created_at": "2026-04-10T22:00:00+00:00",
                },
            ]
        )
        mock_conn.fetchval = AsyncMock(return_value=1)

        resp = await client.get("/api/v1/mergegate/sessions/recent")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["session_id"] == "01234567-0123-7890-abcd-session00001"
        assert data["items"][0]["task_success"] is True
