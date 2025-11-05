#!/usr/bin/env bash
# Build the entire Vagrant-based project with local end-to-end.
# Usage:
#   ./build_project.sh
#   ./build_project.sh --vagrant-up
#   ./build_project.sh --bootstrap-demo
#   ./build_project.sh --vagrant-up --bootstrap-demo
#
# Steps:
#  - (optional) vagrant up (creates 3 VMs and provisions DB/API/Edge)
#  - chooses correct base URL (prefers Edge VM IP over localhost, to avoid stray services)
#  - waits for /health
#  - sets up Python venv + installs frontend deps
#  - (optional) bootstraps 3 demo users

set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

DO_VAGRANT_UP=0
DO_BOOTSTRAP=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --vagrant-up)     DO_VAGRANT_UP=1; shift;;
    --bootstrap-demo) DO_BOOTSTRAP=1; shift;;
    -h|--help)
      sed -n '2,40p' "$0"; exit 0;;
    *) echo "Unknown arg: $1" >&2; exit 2;;
  esac
done

NETCFG_V="$ROOT/backend/network_config.vagrant.json"
if [[ ! -f "$NETCFG_V" ]]; then
  echo "ERROR: $NETCFG_V not found. Please create it." >&2
  exit 1
fi

json_get() {
  python3 - "$NETCFG_V" "$1" "${2:-}" <<'PY'
import json, sys, pathlib
p, path, dv = sys.argv[1], sys.argv[2].split("."), sys.argv[3] if len(sys.argv)>3 else ""
try:
    data = json.loads(pathlib.Path(p).read_text())
except Exception:
    data = {}
node = data
for k in path:
    if isinstance(node, dict) and k in node:
        node = node[k]
    else:
        print(dv); break
else:
    print(node if isinstance(node, (str,int,float)) else dv)
PY
}

EDGE_IP="$(json_get 'edge.ip' '192.168.56.30')"
EDGE_PORT="$(json_get 'edge.port' '18080')"
FWD_IP="$(json_get 'host.forward_ip' '127.0.0.1')"
FWD_PORT="$(json_get 'host.forward_http' '18080')"

HTTP_LOCAL="http://${FWD_IP}:${FWD_PORT}"
HTTP_EDGE="http://${EDGE_IP}:${EDGE_PORT}"

if [[ $DO_VAGRANT_UP -eq 1 ]]; then
  echo "==> Bringing up Vagrant VMs…"
  vagrant up
  echo
fi

is_up() { curl -fsS -m 2 "$1/health" >/dev/null 2>&1; }

choose_base() {
  if is_up "$HTTP_EDGE"; then
    echo "$HTTP_EDGE"; return
  fi
  if is_up "$HTTP_LOCAL"; then
    echo "$HTTP_LOCAL"; return
  fi
  echo "$HTTP_EDGE"
}

BASE="$(choose_base)"
if [[ "$BASE" == "$HTTP_LOCAL" ]]; then
  echo "==> Using localhost forward ($BASE). If this isn't the Edge VM, stop any local service on ${FWD_PORT}."
else
  echo "==> Using Edge VM IP ($BASE) (preferred)."
fi

echo "==> Waiting for backend /health at: $BASE"
ATTEMPTS=180
until curl -fsS "${BASE}/health" >/dev/null 2>&1; do
  ATTEMPTS=$((ATTEMPTS-1))
  if [[ $ATTEMPTS -le 0 ]]; then
    echo "ERROR: Backend did not become healthy at $BASE" >&2
    echo "Tip: check 'vagrant ssh vm-api -c \"sudo docker logs --tail=200 lilium-backend\"'" >&2
    exit 1
  fi
  sleep 1
done
echo "OK: Backend is healthy at $BASE"
echo

echo "==> Setting up Python venv (./venv) and installing frontend/requirements.txt …"
if [[ ! -d "venv" ]]; then
  python3 -m venv venv || {
    echo "python3 -m venv failed; on Debian/Ubuntu: sudo apt install -y python3-venv" >&2
    exit 1
  }
fi
VENV_PY="$ROOT/venv/bin/python"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -r frontend/requirements.txt
echo "OK: Python deps installed."
echo

if [[ $DO_BOOTSTRAP -eq 1 ]]; then
  echo "==> Bootstrapping demo users (A,B,C)…"
  export LILIUM_NETCFG="$NETCFG_V"
  "$VENV_PY" scripts/bootstrap_local_triple_user.py --base "$BASE"
  echo "OK: Demo users bootstrapped."
  echo
fi

echo "==> Done."
echo "Backend: $BASE"
echo "WS:      ${BASE/https:/wss:}"
echo
echo "To run GUIs:"
echo "  source venv/bin/activate"
echo "  python3 frontend/gui.py"
