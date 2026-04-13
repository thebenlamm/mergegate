#!/bin/bash
# MergeGate Demo: Seed tasks, run agents, generate profiles
#
# Prerequisites:
#   - PostgreSQL running with mergegate database
#   - DATABASE_URL set (default: postgresql://localhost/mergegate)
#   - ANTHROPIC_API_KEY set (for Claude models)
#   - OPENAI_API_KEY set (for OpenAI models, optional)
#   - pip install -e ".[dev]"
#   - alembic upgrade head
#
# Usage:
#   ./scripts/demo_mergegate.sh

set -e

echo "=== MergeGate Demo ==="
echo ""

# Step 1: Seed tasks
echo "[1/5] Seeding MergeGate tasks..."
python scripts/seed_mergegate_tasks.py --verify --clean

# Step 2: Start API server in background
echo ""
echo "[2/5] Starting API server..."
uvicorn api.main:app --port 8000 &
API_PID=$!
echo "Waiting for API server..."
for i in $(seq 1 30); do
    if curl -sf http://localhost:8000/api/v1/health > /dev/null 2>&1; then
        echo "API server ready."
        break
    fi
    sleep 1
done

cleanup() {
    echo "Stopping API server..."
    kill $API_PID 2>/dev/null || true
}
trap cleanup EXIT

# Step 3: Run Claude agent
echo ""
echo "[3/5] Running Claude agent..."
python scripts/run_agent.py \
    --model claude-sonnet-4-20250514 \
    --provider anthropic \
    --all-tasks \
    --agent-name "claude-sonnet-4"

# Step 4: Run GPT-4o agent (optional)
if [ -n "$OPENAI_API_KEY" ]; then
    echo ""
    echo "[4/5] Running GPT-4o agent..."
    python scripts/run_agent.py \
        --model gpt-4o \
        --provider openai \
        --all-tasks \
        --agent-name "gpt-4o"
else
    echo ""
    echo "[4/5] Skipping GPT-4o (no OPENAI_API_KEY set)"
fi

# Step 5: Generate comparison profile
echo ""
echo "[5/5] Generating delegation profiles..."
python scripts/generate_profile.py --compare

echo ""
echo "=== Demo Complete ==="
