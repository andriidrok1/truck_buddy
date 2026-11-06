#!/usr/bin/env bash
# Runs ON THE BOX (deploy.sh copies it to /opt/truckbuddy/rollback.sh).
#   bash /opt/truckbuddy/rollback.sh            -> back to .previous-sha
#   bash /opt/truckbuddy/rollback.sh <sha7>     -> explicit target (preferred)
#   DRY_RUN=1 bash /opt/truckbuddy/rollback.sh  -> print the plan only
# Images are kept locally per sha, so rollback is a compose restart, no rebuild.
# The postgres and /data volumes are untouched; schema changes are NOT reverted.
set -euo pipefail
cd /opt/truckbuddy
TARGET="${1:-$(cat .previous-sha 2>/dev/null || true)}"
CURRENT="$(cat .current-sha 2>/dev/null || echo none)"
[ -n "$TARGET" ] || { echo "no target and no .previous-sha"; exit 1; }
docker image inspect "truckbuddy-api:$TARGET" >/dev/null || { echo "image truckbuddy-api:$TARGET not on this box"; exit 1; }
echo "rollback: $CURRENT -> $TARGET"
[ "${DRY_RUN:-0}" = 1 ] && exit 0
TB_SHA=$TARGET POSTGRES_PASSWORD=$(grep ^POSTGRES_PASSWORD= .env | cut -d= -f2-) docker compose up -d api
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1/api/health >/dev/null; then
    echo "$CURRENT" > .previous-sha; echo "$TARGET" > .current-sha
    echo "rolled back to $TARGET, /api/health OK"; exit 0
  fi
  sleep 2
done
echo "rolled back to $TARGET but /api/health is not answering"; exit 1
