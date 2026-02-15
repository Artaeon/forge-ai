#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
# Forge Smoke Tests
# Run these to verify your setup works end-to-end.
# Usage: bash tests/smoke_test.sh
# ═══════════════════════════════════════════════════════════════

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
DIM='\033[2m'
RESET='\033[0m'

PASS=0
FAIL=0
SKIP=0

pass()  { echo -e "${GREEN}  ✅ PASS${RESET} $1"; ((PASS++)); }
fail()  { echo -e "${RED}  ❌ FAIL${RESET} $1: $2"; ((FAIL++)); }
skip()  { echo -e "${YELLOW}  ⏭  SKIP${RESET} $1: $2"; ((SKIP++)); }

echo ""
echo "═══════════════════════════════════════════════════"
echo "  ⚡ Forge Smoke Tests"
echo "═══════════════════════════════════════════════════"
echo ""

# ─── 1. Version ───
echo -e "${DIM}─── Version ───${RESET}"
VERSION=$(forge --version 2>&1)
if echo "$VERSION" | grep -q "forge v"; then
    pass "forge --version → $VERSION"
else
    fail "forge --version" "$VERSION"
fi

# ─── 2. Config ───
echo -e "${DIM}─── Config ───${RESET}"
CONFIG=$(forge config 2>&1)
if echo "$CONFIG" | grep -q "Agent Status"; then
    pass "forge config loads successfully"
else
    fail "forge config" "config output unexpected"
fi

# Count available agents
AVAIL=$(echo "$CONFIG" | grep -c "✅ Ready" || true)
echo -e "  ${DIM}Available agents: ${AVAIL}${RESET}"

# ─── 3. Agents ───
echo -e "${DIM}─── Agents ───${RESET}"
AGENTS=$(forge agents 2>&1)
if echo "$AGENTS" | grep -q "claude-sonnet\|gemini\|copilot"; then
    pass "forge agents lists agents"
else
    fail "forge agents" "unexpected output"
fi

# ─── 4. Single agent: Claude ───
echo -e "${DIM}─── Single Agent: Claude Sonnet ───${RESET}"
if command -v claude &>/dev/null; then
    RESULT=$(forge run -a claude-sonnet "Reply with exactly: FORGE_TEST_OK" 2>&1)
    if echo "$RESULT" | grep -qi "FORGE_TEST_OK\|success\|result"; then
        pass "forge run -a claude-sonnet"
    else
        fail "forge run -a claude-sonnet" "unexpected output"
    fi
else
    skip "claude-sonnet" "claude CLI not found"
fi

# ─── 5. Single agent: Gemini ───
echo -e "${DIM}─── Single Agent: Gemini ───${RESET}"
if command -v gemini &>/dev/null; then
    RESULT=$(forge run -a gemini "Reply with exactly: FORGE_TEST_OK" 2>&1)
    if echo "$RESULT" | grep -qi "FORGE_TEST_OK\|success\|result"; then
        pass "forge run -a gemini"
    else
        fail "forge run -a gemini" "unexpected output"
    fi
else
    skip "gemini" "gemini CLI not found"
fi

# ─── 6. Parallel mode ───
echo -e "${DIM}─── Multi-Agent: Parallel ───${RESET}"
if command -v claude &>/dev/null && command -v gemini &>/dev/null; then
    RESULT=$(forge run --mode parallel -a claude-sonnet -a gemini "What is 2+2? Reply in one word." 2>&1)
    if echo "$RESULT" | grep -qi "Agent Results\|four\|4\|result"; then
        pass "forge run --mode parallel (claude + gemini)"
    else
        fail "parallel mode" "unexpected output"
    fi
else
    skip "parallel mode" "needs both claude and gemini"
fi

# ─── 7. Chain mode ───
echo -e "${DIM}─── Multi-Agent: Chain ───${RESET}"
if command -v claude &>/dev/null && command -v gemini &>/dev/null; then
    RESULT=$(forge run --mode chain -a gemini -a claude-sonnet "Write a haiku about coding" 2>&1)
    if echo "$RESULT" | grep -qi "Final Output\|round\|haiku"; then
        pass "forge run --mode chain (gemini → claude)"
    else
        fail "chain mode" "unexpected output"
    fi
else
    skip "chain mode" "needs both claude and gemini"
fi

# ─── 8. Init template ───
echo -e "${DIM}─── Project Init ───${RESET}"
TMPDIR=$(mktemp -d)
RESULT=$(forge init flask-api --dir "$TMPDIR" 2>&1)
if [ -f "$TMPDIR/app.py" ] && [ -f "$TMPDIR/requirements.txt" ]; then
    pass "forge init flask-api (created app.py + requirements.txt)"
else
    fail "forge init flask-api" "files not created"
fi
rm -rf "$TMPDIR"

# ─── Summary ───
echo ""
echo "═══════════════════════════════════════════════════"
echo -e "  Results: ${GREEN}${PASS} passed${RESET}, ${RED}${FAIL} failed${RESET}, ${YELLOW}${SKIP} skipped${RESET}"
echo "═══════════════════════════════════════════════════"
echo ""

if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
