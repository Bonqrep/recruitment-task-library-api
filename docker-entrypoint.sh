#!/bin/sh
set -eu

if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "[api] Applying database migrations..."
    alembic upgrade head
    echo "[api] Migrations complete; starting application..."
fi
exec "$@"
