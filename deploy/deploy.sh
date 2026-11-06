#!/usr/bin/env bash
# Run ON THE MAC. Ships a branch of truck_buddy to the truckbuddy-api box:
#   bash deploy.sh [branch]            (default: main)
# Steps: assemble .env (never printed) -> sync compose files -> stream backend/
# of origin/<branch> into /usr/local/bin/truckbuddy-deploy, the same command
# GitHub Actions calls (build truckbuddy-api:<sha7>, compose up, /api/health
# gate, automatic rollback to the previous image on failure).
set -euo pipefail
BRANCH="${1:-main}"
HERE="$(cd "$(dirname "$0")" && pwd)"
HOST="${TB_HOST:?set TB_HOST to the static IP}"
KEY="${TB_KEY:-$HOME/.ssh/truckbuddy-aws.pem}"
GEN="$HOME/.truckbuddy/generated.env"        # JWT_SECRET, TESTER_CODE, POSTGRES_PASSWORD
THIRD="$HOME/truckbuddy-secrets.env"         # OPENAI/HERE/ORS/GOOGLE keys from the owner
SSH="ssh -i $KEY -o StrictHostKeyChecking=accept-new ubuntu@$HOST"

[ -f "$THIRD" ] || { echo "missing $THIRD (third-party keys)"; exit 1; }
if [ ! -f "$GEN" ]; then
  umask 077; mkdir -p "$(dirname "$GEN")"
  { echo "JWT_SECRET=$(openssl rand -hex 32)"
    echo "TESTER_CODE=$(openssl rand -hex 4 | tr a-f A-F)"
    echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)"; } > "$GEN"
  chmod 600 "$GEN"
fi
set -a; . "$GEN"; set +a

# .env streamed over ssh, never echoed or written outside ~/.truckbuddy.
{ cat "$THIRD"
  echo "JWT_SECRET=$JWT_SECRET"
  echo "TESTER_CODE=$TESTER_CODE"
  echo "POSTGRES_PASSWORD=$POSTGRES_PASSWORD"
  echo "DATABASE_URL=postgresql://truckbuddy:$POSTGRES_PASSWORD@db:5432/truckbuddy?sslmode=disable"
  echo "OPEN_ACCESS=1"
  echo "CORS_ORIGINS=*"
  echo "RATE_PER_10MIN=300"
  echo "RATE_PER_DAY=3000"
  echo "PARKING_CACHE_DIR=/data/parking_cache"
} | $SSH 'umask 077; cat > /opt/truckbuddy/.env'

scp -i "$KEY" -q "$HERE/docker-compose.yml" "$HERE/Caddyfile" "$HERE/rollback.sh" "ubuntu@$HOST:/opt/truckbuddy/"

# Same path GitHub Actions uses: the tracked backend/ of the branch, as a tarball,
# into the root-owned deploy command on the box.
git -C "$HERE/.." fetch -q origin "$BRANCH"
SHA=$(git -C "$HERE/.." rev-parse --short=7 "origin/$BRANCH")
git -C "$HERE/.." archive --format=tar.gz "origin/$BRANCH:backend" \
  | $SSH "sudo /usr/local/bin/truckbuddy-deploy $SHA"
