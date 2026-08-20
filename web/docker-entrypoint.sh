#!/bin/sh
set -eu

SECRETS_DIR="${AIMM_SECRETS_DIR:-/run/aimm-secrets}"

if [ -z "${AIMM_API_KEY:-}" ] && [ -f "${SECRETS_DIR}/api_key" ]; then
  AIMM_API_KEY="$(tr -d '\n\r' < "${SECRETS_DIR}/api_key")"
  export AIMM_API_KEY
fi

if [ -z "${AIMM_API_KEY:-}" ]; then
  echo "web: AIMM_API_KEY is required (no default). Wait for secrets-init or set AIMM_API_KEY." >&2
  exit 1
fi

exec "$@"
