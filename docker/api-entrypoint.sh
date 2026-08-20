#!/bin/sh
set -eu

export AIMM_SECRETS_DIR="${AIMM_SECRETS_DIR:-/run/aimm-secrets}"

eval "$(python -m api.control_plane_secrets --export-shell)"

if [ -z "${AIMM_API_KEY:-}" ] || [ -z "${AIMM_AUTH_SECRET:-}" ]; then
  echo "control-plane: AIMM_API_KEY and AIMM_AUTH_SECRET are required (no defaults)." >&2
  exit 1
fi
if [ -z "${DATABASE_URL:-}" ]; then
  echo "control-plane: DATABASE_URL is required (set it or let secrets-init generate POSTGRES_PASSWORD)." >&2
  exit 1
fi

exec "$@"
