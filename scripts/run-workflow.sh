#!/usr/bin/env bash
#
# run-workflow.sh
# Manually triggers one execution of the main workflow through the n8n CLI,
# without opening the browser. Useful for a quick end-to-end test.
#
# WARNING: with BACKFILL_MODE=true this runs the full backfill, which classifies
# every Contact Us message and therefore spends Anthropic API credit (roughly
# $2 to $4 for the ~18k backlog). You are prompted before it runs. Pass --yes to
# skip the prompt.

set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose"
SERVICE="n8n"
WORKFLOW_ID="arthouseOpsMain"

AUTO="no"
if [ "${1:-}" = "--yes" ] || [ "${1:-}" = "-y" ]; then
  AUTO="yes"
fi

if ! $COMPOSE ps --status running "$SERVICE" >/dev/null 2>&1; then
  echo "ERROR: the '$SERVICE' service is not running. Start it with: docker compose up -d"
  exit 1
fi

echo "This will execute the '$WORKFLOW_ID' workflow now."
echo "If BACKFILL_MODE=true in your .env, this spends Anthropic API credit."
if [ "$AUTO" != "yes" ]; then
  printf "Type 'run' to continue: "
  read -r answer
  if [ "$answer" != "run" ]; then
    echo "Aborted."
    exit 0
  fi
fi

echo "==> Executing workflow $WORKFLOW_ID ..."
$COMPOSE exec -T "$SERVICE" n8n execute --id "$WORKFLOW_ID"
echo "==> Done. Check your Google Sheet tabs and 'docker compose logs -f n8n' for detail."
