#!/bin/sh
set -eu

echo "[api] Applying database migrations..."
alembic upgrade head

echo "[api] Migrations complete; starting application..."
exec "$@"
