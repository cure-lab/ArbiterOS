#!/usr/bin/env bash
set -euo pipefail

OUT_FILE="${OUT_FILE:-stack.env}"
INIT_USER_EMAIL="${INIT_USER_EMAIL:-admin@example.com}"
INIT_USER_NAME="${INIT_USER_NAME:-Admin}"
INIT_USER_PASSWORD="${INIT_USER_PASSWORD:-ArbiterOS}"

ORG_ID="${ORG_ID:-arbiteros-org}"
ORG_NAME="${ORG_NAME:-ArbiterOS}"
PROJECT_ID="${PROJECT_ID:-arbiteros-proj}"
PROJECT_NAME="${PROJECT_NAME:-ArbiterOS}"

usage() {
  cat <<'EOF'
Generate stack.env with random secrets for ArbiterOS + Langfuse.

Usage:
  ./scripts/generate-stack-env.sh [-o stack.env] [--email you@example.com] [--name "Your Name"] [--password "pass"]

Env overrides (optional):
  OUT_FILE, INIT_USER_EMAIL, INIT_USER_NAME, INIT_USER_PASSWORD,
  ORG_ID, ORG_NAME, PROJECT_ID, PROJECT_NAME

Requires:
  - openssl
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    -o|--out)
      OUT_FILE="$2"; shift 2;;
    --email)
      INIT_USER_EMAIL="$2"; shift 2;;
    --name)
      INIT_USER_NAME="$2"; shift 2;;
    --password)
      INIT_USER_PASSWORD="$2"; shift 2;;
    -h|--help)
      usage; exit 0;;
    *)
      echo "Unknown arg: $1" >&2
      usage; exit 1;;
  esac
done

need() { command -v "$1" >/dev/null 2>&1 || { echo "Missing dependency: $1" >&2; exit 1; }; }
need openssl

rand_b64() { openssl rand -base64 "$1" | tr -d '\n'; }
rand_hex() { openssl rand -hex "$1" | tr -d '\n'; }

rand_alnum() {
  # Generate URL/env friendly token, length=$1
  local len="$1"
  openssl rand -base64 $((len * 2)) | tr -dc 'A-Za-z0-9' | head -c "$len"
}

# Reuse existing POSTGRES_PASSWORD from OUT_FILE if present, otherwise generate once
existing_postgres_password=""
if [[ -f "$OUT_FILE" ]]; then
  existing_postgres_password="$(grep -E '^POSTGRES_PASSWORD=' "$OUT_FILE" | head -n1 | cut -d'=' -f2- || true)"
fi
if [[ -n "$existing_postgres_password" ]]; then
  postgres_password="$existing_postgres_password"
else
  postgres_password="$(rand_alnum 24)"
fi
redis_auth="$(rand_alnum 24)"
clickhouse_password="$(rand_alnum 24)"
minio_password="$(rand_alnum 24)"

nextauth_secret="$(rand_b64 32)"
salt="$(rand_b64 32)"
encryption_key="$(rand_hex 32)" # 32 bytes => 64 hex chars

pk="pk-lf-$(rand_alnum 24)"
sk="sk-lf-$(rand_alnum 24)"

cat > "$OUT_FILE" <<EOF
POSTGRES_USER=postgres
POSTGRES_PASSWORD=${postgres_password}
POSTGRES_DB=postgres
POSTGRES_VERSION=17

REDIS_AUTH=${redis_auth}

CLICKHOUSE_USER=clickhouse
CLICKHOUSE_PASSWORD=${clickhouse_password}

MINIO_ROOT_USER=minio
MINIO_ROOT_PASSWORD=${minio_password}

NEXTAUTH_URL=http://localhost:3000
NEXTAUTH_SECRET=${nextauth_secret}
SALT=${salt}
ENCRYPTION_KEY=${encryption_key}

TELEMETRY_ENABLED=true
NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=

LANGFUSE_INIT_ORG_ID=${ORG_ID}
LANGFUSE_INIT_ORG_NAME=${ORG_NAME}
LANGFUSE_INIT_PROJECT_ID=${PROJECT_ID}
LANGFUSE_INIT_PROJECT_NAME=${PROJECT_NAME}
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${pk}
LANGFUSE_INIT_PROJECT_SECRET_KEY=${sk}
LANGFUSE_INIT_USER_EMAIL=${INIT_USER_EMAIL}
LANGFUSE_INIT_USER_NAME=${INIT_USER_NAME}
LANGFUSE_INIT_USER_PASSWORD=${INIT_USER_PASSWORD}

ARBITEROS_LANGFUSE_BASE_URL=http://langfuse-web:3000
EOF

echo "Wrote ${OUT_FILE}"
echo "Langfuse init keys:"
echo "  LANGFUSE_INIT_PROJECT_PUBLIC_KEY=${pk}"
echo "  LANGFUSE_INIT_PROJECT_SECRET_KEY=${sk}"
echo "Langfuse init user:"
echo "  LANGFUSE_INIT_USER_EMAIL=${INIT_USER_EMAIL}"
echo "  LANGFUSE_INIT_USER_PASSWORD=${INIT_USER_PASSWORD}"

