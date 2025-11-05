#!/usr/bin/env bash
set -euo pipefail
echo "[entrypoint] running migrations…"
node /app/src/migrate.js || { echo "migration failed"; exit 1; }
echo "[entrypoint] starting server…"
exec node /app/src/server.js
