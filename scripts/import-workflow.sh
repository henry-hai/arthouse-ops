#!/usr/bin/env bash
#
# import-workflow.sh
# Imports the credential scaffolds and both workflows into the running n8n
# container. Safe to run more than once: n8n upserts by id, so re-running
# updates in place instead of creating duplicates.
#
# Prerequisite: the stack is up (docker compose up -d) and healthy.

set -euo pipefail

# Always operate from the repo root, regardless of where this is called from.
cd "$(dirname "$0")/.."

COMPOSE="docker compose"
SERVICE="n8n"
URL="http://localhost:5678"

echo "==> Checking that the n8n container is running and healthy..."

if ! $COMPOSE ps --status running "$SERVICE" >/dev/null 2>&1; then
  echo "ERROR: the '$SERVICE' service is not running."
  echo "Start it first with:  docker compose up -d"
  exit 1
fi

# Wait up to ~2 minutes for the /healthz endpoint to report ready.
ready=""
for i in $(seq 1 24); do
  if $COMPOSE exec -T "$SERVICE" wget -q -O /dev/null http://localhost:5678/healthz 2>/dev/null; then
    ready="yes"
    break
  fi
  echo "    ...waiting for n8n to become healthy ($i/24)"
  sleep 5
done

if [ -z "$ready" ]; then
  echo "ERROR: n8n did not become healthy in time."
  echo "Check the logs with:  docker compose logs -f n8n"
  exit 1
fi
echo "    n8n is healthy."

echo "==> Copying credential and workflow files into the container..."
$COMPOSE cp ./workflows/credentials.json          "$SERVICE":/tmp/credentials.json
$COMPOSE cp ./workflows/arthouse-ops-errors.json  "$SERVICE":/tmp/arthouse-ops-errors.json
$COMPOSE cp ./workflows/arthouse-ops.json         "$SERVICE":/tmp/arthouse-ops.json

echo "==> Importing credentials..."
$COMPOSE exec -T "$SERVICE" n8n import:credentials --input=/tmp/credentials.json

echo "==> Importing the error workflow first (so the main workflow can link to it)..."
$COMPOSE exec -T "$SERVICE" n8n import:workflow --input=/tmp/arthouse-ops-errors.json

echo "==> Importing the main workflow..."
$COMPOSE exec -T "$SERVICE" n8n import:workflow --input=/tmp/arthouse-ops.json

echo ""
echo "============================================================"
echo "Import complete."
echo ""
echo "Open the editor:   $URL"
echo ""
echo "NEXT STEP: complete the two browser OAuth logins."
echo "Follow docs/04-oauth-setup.md to connect:"
echo "  1. ArtHouse Google Sheets  (Google Sheets OAuth2)"
echo "  2. ArtHouse Gmail          (Gmail OAuth2)"
echo ""
echo "Until those two are connected, the Google Sheets and Gmail"
echo "nodes will show a red 'credential not connected' warning."
echo "============================================================"
