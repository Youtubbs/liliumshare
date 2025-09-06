#!/usr/bin/env bash
# Rebuilds everything (unless --dontclean), applies schema, starts backend, and (optionally) bootstraps test users.
# Usage:
#   scripts/rebuild_from_scratch.sh
#   scripts/rebuild_from_scratch.sh --no-cache
#   scripts/rebuild_from_scratch.sh --test
#   scripts/rebuild_from_scratch.sh --dontclean
#   scripts/rebuild_from_scratch.sh --no-cache --test --dontclean
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

NO_CACHE=0
DO_TEST=0
DONT_CLEAN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-cache)  NO_CACHE=1; shift;;
    --test)      DO_TEST=1; shift;;
    --dontclean) DONT_CLEAN=1; shift;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

# ---- network config ----
export LILIUM_NETCFG="${LILIUM_NETCFG:-$ROOT/backend/network_config.json}"
if [[ ! -f "$LILIUM_NETCFG" ]]; then
  echo "WARN: $LILIUM_NETCFG not found; falling back to localhost:18080 defaults" >&2
fi

read_json () {
  python3 - "$1" "$2" <<'PY'
import json, os, sys, pathlib
p = os.environ.get("LILIUM_NETCFG", "")
defval = sys.argv[2]
keypath = sys.argv[1].split(".")
data = {}
try:
    data = json.loads(open(p,"r").read()) if p and pathlib.Path(p).exists() else {}
except Exception:
    data = {}
node = data
for k in keypath:
    if isinstance(node, dict) and k in node:
        node = node[k]
    else:
        print(defval); sys.exit(0)
print(node if isinstance(node, str) else defval)
PY
}

HTTP_BASE="$(read_json 'backend.http_base' 'http://localhost:18080')"
WS_BASE="$(read_json 'backend.ws_base' "$(echo "$HTTP_BASE" | sed -E 's|^http|ws|' | sed -E 's|/$||')/ws")"

echo "Using backend:"
echo "  HTTP_BASE=$HTTP_BASE"
echo "  WS_BASE=$WS_BASE"
echo "  LILIUM_NETCFG=$LILIUM_NETCFG"
echo

die() {
  echo "ERROR: $*" >&2
  echo "---- backend logs (tail) ----"
  docker compose logs --no-color --tail=120 backend || true
  exit 1
}

# ---- clean (optional) ----
if [[ "$DONT_CLEAN" -eq 1 ]]; then
  echo "Skipping clean step (--dontclean)."
else
  echo "Stopping/removing containers & volumes…"
  docker compose down -v || true
fi

# ---- build images ----
echo "Building Docker images…"
if [[ "$NO_CACHE" -eq 1 ]]; then
  docker compose build --no-cache
else
  docker compose build
fi

# ---- start db only & wait ----
echo "Starting postgres (db) …"
docker compose up -d db

echo "Waiting for postgres to be ready…"
ATTEMPTS=60
until docker compose exec -T db pg_isready -U lilium -d liliumshare -h 127.0.0.1 >/dev/null 2>&1; do
  ATTEMPTS=$((ATTEMPTS-1))
  if [[ $ATTEMPTS -le 0 ]]; then
    echo "Postgres did not become ready in time." >&2
    docker compose logs --no-color db | tail -n 200
    exit 1
  fi
  sleep 1
done
echo "Postgres is ready."

# ---- safety schema patch (idempotent) ----
echo "Applying safety schema (idempotent)…"
docker compose exec -T db psql -U lilium -d liliumshare -v ON_ERROR_STOP=1 <<'SQL'
CREATE TABLE IF NOT EXISTS users (
  pubkey TEXT PRIMARY KEY,
  nickname TEXT,
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE IF NOT EXISTS friendships (
  host_pubkey   TEXT NOT NULL,
  friend_pubkey TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending', -- 'pending' | 'accepted'
  permissions   JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMP DEFAULT now(),
  PRIMARY KEY (host_pubkey, friend_pubkey),
  FOREIGN KEY (host_pubkey)   REFERENCES users(pubkey) ON DELETE CASCADE,
  FOREIGN KEY (friend_pubkey) REFERENCES users(pubkey) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS connkeys (
  host_pubkey   TEXT NOT NULL,
  friend_pubkey TEXT NOT NULL,
  conn_key      TEXT NOT NULL,
  created_at    TIMESTAMP DEFAULT now(),
  PRIMARY KEY (host_pubkey, friend_pubkey),
  FOREIGN KEY (host_pubkey)   REFERENCES users(pubkey) ON DELETE CASCADE,
  FOREIGN KEY (friend_pubkey) REFERENCES users(pubkey) ON DELETE CASCADE
);
SQL

# ---- run Node migration (also idempotent) ----
echo "Running migration container (node src/migrate.js)…"
docker compose run --rm migrate || die "Migration container failed."

# ---- start backend ----
echo "Starting backend…"
docker compose up -d backend

# ---- verify /health ----
echo "Waiting for backend /health on $HTTP_BASE …"
ATTEMPTS=60
until curl -fsS "$HTTP_BASE/health" >/dev/null 2>&1; do
  ATTEMPTS=$((ATTEMPTS-1))
  if [[ $ATTEMPTS -le 0 ]]; then
    die "Backend did not respond healthy."
  fi
  sleep 1
done
echo "Backend healthy."

# ---- Python venv (./venv) and requirements ----
echo "Setting up Python venv in ./venv …"
if [[ ! -d "venv" ]]; then
  if ! command -v python3 >/dev/null 2>&1; then
    die "python3 is required but not found in PATH."
  fi
  # Create the venv
  python3 -m venv venv || {
    echo "python3 -m venv failed. On Debian/Ubuntu you may need: sudo apt install -y python3-venv" >&2
    exit 1
  }
fi

VENV_PY="$ROOT/venv/bin/python"
VENV_PIP="$ROOT/venv/bin/pip"

# Make sure pip exists inside venv
"$VENV_PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
if ! "$VENV_PIP" --version >/dev/null 2>&1; then
  echo "Installing pip into venv…"
  "$VENV_PY" -m ensurepip --upgrade || true
fi

echo "Upgrading pip and installing frontend requirements…"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r frontend/requirements.txt

# ---- optional test bootstrap (A,B,C) ----
if [[ "$DO_TEST" -eq 1 ]]; then
  echo "Bootstrapping triple users (A,B,C)…"
  export LILIUM_NETCFG
  "$VENV_PY" scripts/bootstrap_local_triple_user.py --base "$HTTP_BASE" || die "Triple bootstrap failed."

  echo
  echo "Verifying friends/list endpoints…"
  curl -fsS "$HTTP_BASE/health" >/dev/null || die "/health failed after bootstrap."
fi

echo
echo "Rebuild complete."
echo "Backend: $HTTP_BASE"
echo "WS:      $WS_BASE"
echo
echo "Type in 'source venv/bin/activate' before running any python scripts."
