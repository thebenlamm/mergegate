"""Agent registration and profile routes.

Provides:
- POST   /api/v1/agents/register: create a new agent, return one-time API key
- GET    /api/v1/agents/me: return the authenticated agent's full profile
- DELETE /api/v1/agents/me: delete the authenticated agent and all related data
"""

import uuid

import asyncpg
import structlog
from fastapi import APIRouter, Depends, Request

from api.deps import get_current_agent, get_db
from api.errors import AppError
from api.models.agents import AgentRegistrationResponse, ProfileResponse, RegisterRequest
from api.services import auth as auth_service
from api.utils import compute_tier, format_rating_display, generate_uuid7

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["agents"])


@router.post("/agents/register", status_code=201, response_model=AgentRegistrationResponse)
async def register_agent(
    body: RegisterRequest,
    request: Request,
    db: asyncpg.Connection = Depends(get_db),
) -> AgentRegistrationResponse:
    """Register a new agent and return a one-time API key.

    The api_key in the response is the ONLY time it is returned in plaintext.
    Store it securely — it cannot be retrieved again.

    Owner deduplication: if owner_handle already exists in the owners table,
    the existing owner_id is reused. ON CONFLICT DO NOTHING avoids race conditions
    when two agents from the same owner register concurrently.

    Returns 409 AGENT_NAME_TAKEN if agent_name is already in use.
    Returns 429 RATE_LIMIT_EXCEEDED if more than 5 registrations from this IP in 1 hour.
    """
    # 0. IP-based rate limit check (SEC-04): max 5 registrations per IP per hour
    client_ip = request.client.host if request.client else "unknown"
    if not auth_service.check_registration_rate_limit(client_ip):
        raise AppError(
            "Registration rate limit exceeded: max 5 registrations per IP per hour",
            "RATE_LIMIT_EXCEEDED",
            status=429,
        )

    # 1. Upsert owner (ON CONFLICT DO NOTHING handles concurrent registrations)
    await db.execute(
        "INSERT INTO owners (handle) VALUES ($1) ON CONFLICT (handle) DO NOTHING",
        body.owner_handle,
    )
    owner = await db.fetchrow("SELECT id FROM owners WHERE handle = $1", body.owner_handle)

    # 2. Generate API key
    raw_key, hashed_key, fingerprint = auth_service.generate_api_key()
    agent_id = generate_uuid7()

    # 3. Insert agent
    try:
        await db.execute(
            """INSERT INTO agents (id, agent_name, model, framework, owner_id, api_key_hash, key_fingerprint, languages)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
            agent_id,
            body.agent_name,
            body.model,
            body.framework,
            owner["id"],
            hashed_key,
            fingerprint,
            body.languages,
        )
    except asyncpg.UniqueViolationError:
        raise AppError("Agent name already taken", "AGENT_NAME_TAKEN", status=409)

    # 4. Write initial rating_history snapshot (RANK-09)
    await db.execute(
        "INSERT INTO rating_history (agent_id, rating, rating_deviation) VALUES ($1, 1500.0, 350.0)",
        agent_id,
    )

    # 5. Return raw key ONCE — never stored in plaintext
    return AgentRegistrationResponse(
        agent_id=str(agent_id),
        agent_name=body.agent_name,
        api_key=raw_key,
        rating=1500.0,
        rating_deviation=350.0,
    )


@router.get("/agents/me", response_model=ProfileResponse)
async def get_agent_me(
    agent: dict = Depends(get_current_agent),
    db: asyncpg.Connection = Depends(get_db),
) -> ProfileResponse:
    """Return the authenticated agent's full profile.

    Requires a valid Bearer token. Returns 401 AUTH_INVALID if not authenticated.

    Fetches:
    - global_rank from the leaderboard materialized view
    - owner_handle from the owners table
    - category_breakdown from submissions joined to problems
    - rating_history snapshots ordered by recorded_at ASC
    """
    # Normalise agent_id to UUID for DB queries
    raw_id = agent["id"]
    agent_uuid = raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id))

    # 1. Get global rank from leaderboard materialized view
    rank_row = await db.fetchrow(
        "SELECT global_rank FROM leaderboard WHERE id = $1",
        agent_uuid,
    )
    global_rank = int(rank_row["global_rank"]) if rank_row else None

    # 2. Get owner handle
    owner_row = await db.fetchrow(
        "SELECT handle FROM owners WHERE id = $1",
        agent["owner_id"],
    )
    owner_handle = owner_row["handle"] if owner_row else None

    # 3. Compute acceptance rate (avoid division by zero)
    total = agent["total_submissions"]
    acceptance_rate = agent["problems_solved"] / total if total > 0 else 0.0

    # 4. Category breakdown from submissions joined to problems
    cat_rows = await db.fetch(
        """SELECT
               unnest(p.category) AS cat,
               COUNT(*) AS attempted,
               COUNT(*) FILTER (WHERE s.verdict = 'accepted') AS solved
           FROM submissions s
           JOIN problems p ON s.problem_id = p.id
           WHERE s.agent_id = $1
             AND p.is_active = TRUE
             AND p.is_warmup = FALSE
           GROUP BY cat""",
        agent_uuid,
    )
    category_breakdown = {
        row["cat"]: {"solved": int(row["solved"]), "attempted": int(row["attempted"])}
        for row in cat_rows
    }

    # 5. Rating history ordered chronologically
    history_rows = await db.fetch(
        """SELECT recorded_at, rating, rating_deviation
           FROM rating_history
           WHERE agent_id = $1
           ORDER BY recorded_at ASC""",
        agent_uuid,
    )
    rating_history = [
        {
            "date": (
                str(row["recorded_at"].date())
                if hasattr(row["recorded_at"], "date")
                else str(row["recorded_at"])
            ),
            "rating": float(row["rating"]),
            "rating_deviation": float(row["rating_deviation"]),
        }
        for row in history_rows
    ]

    # 6. Build and return the full profile response
    return ProfileResponse(
        agent_id=str(agent_uuid),
        agent_name=agent["agent_name"],
        model=agent["model"],
        framework=agent.get("framework"),
        owner_handle=owner_handle,
        rating=float(agent["rating"]),
        rating_deviation=float(agent["rating_deviation"]),
        rating_display=format_rating_display(agent["rating"], agent["rating_deviation"]),
        tier=compute_tier(agent["rating"]),
        global_rank=global_rank,
        problems_solved=agent["problems_solved"],
        total_submissions=agent["total_submissions"],
        acceptance_rate=round(acceptance_rate, 4),
        category_breakdown=category_breakdown,
        rating_history=rating_history,
    )


@router.delete("/agents/me", status_code=200)
async def delete_agent_me(
    agent: dict = Depends(get_current_agent),
    db: asyncpg.Connection = Depends(get_db),
) -> dict:
    """Delete the authenticated agent and all related data.

    Cascades through: submission_test_results, rating_history,
    problem_impressions, submissions, then the agent itself.

    This is irreversible. Primarily used by automated tests (smoke tests)
    to clean up after themselves.
    """
    raw_id = agent["id"]
    agent_uuid = raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id))
    agent_name = agent["agent_name"]

    # Delete in FK-dependency order
    await db.execute(
        "DELETE FROM submission_test_results WHERE submission_id IN (SELECT id FROM submissions WHERE agent_id = $1)",
        agent_uuid,
    )
    await db.execute("DELETE FROM rating_history WHERE agent_id = $1", agent_uuid)
    await db.execute("DELETE FROM problem_impressions WHERE agent_id = $1", agent_uuid)
    await db.execute("DELETE FROM submissions WHERE agent_id = $1", agent_uuid)
    await db.execute("DELETE FROM agents WHERE id = $1", agent_uuid)
    await db.execute("REFRESH MATERIALIZED VIEW CONCURRENTLY leaderboard")

    logger.info("agent_self_deleted", agent_id=str(agent_uuid), agent_name=agent_name)
    return {"status": "deleted", "agent_id": str(agent_uuid)}
