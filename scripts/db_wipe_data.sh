#!/usr/bin/env bash
set -euo pipefail
DB_SERVICE=${DB_SERVICE:-db}
DB_NAME=${DB_NAME:-liliumshare}
DB_USER=${DB_USER:-lilium}

# Remove all rows 
docker compose exec -T "$DB_SERVICE" psql -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 <<'SQL'
TRUNCATE TABLE friendships, users RESTART IDENTITY CASCADE;
SQL

echo "Data wiped (tables intact)."

