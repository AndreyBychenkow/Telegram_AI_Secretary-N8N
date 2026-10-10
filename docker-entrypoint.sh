#!/bin/sh
set -e

# Render.com automatically injects RENDER_EXTERNAL_HOSTNAME, RENDER_EXTERNAL_URL, and PORT
if [ -n "$RENDER_EXTERNAL_HOSTNAME" ] && [ -z "$N8N_HOST" ]; then
  export N8N_HOST="$RENDER_EXTERNAL_HOSTNAME"
fi

if [ -n "$RENDER_EXTERNAL_URL" ] && [ -z "$WEBHOOK_URL" ]; then
  case "$RENDER_EXTERNAL_URL" in
    */) export WEBHOOK_URL="$RENDER_EXTERNAL_URL" ;;
    *)  export WEBHOOK_URL="${RENDER_EXTERNAL_URL}/" ;;
  esac
fi

export N8N_PORT="${PORT:-5678}"
export N8N_LISTEN_ADDRESS="${N8N_LISTEN_ADDRESS:-0.0.0.0}"
export N8N_PROTOCOL="${N8N_PROTOCOL:-https}"
export GENERIC_TIMEZONE="${GENERIC_TIMEZONE:-Europe/Moscow}"
export TZ="${TZ:-Europe/Moscow}"
export N8N_BLOCK_ENV_ACCESS_IN_NODE="${N8N_BLOCK_ENV_ACCESS_IN_NODE:-false}"

mkdir -p /home/node/.n8n

# Automatically import workflow.json on first boot (or after ephemeral container reset)
if [ -f "/home/node/workflow.json" ] && [ ! -f "/home/node/.n8n/.workflow_imported" ]; then
  echo "[init] Importing Telegram AI Secretary workflow into n8n..."
  if n8n import:workflow --input=/home/node/workflow.json; then
    touch /home/node/.n8n/.workflow_imported
    echo "[init] Workflow imported successfully."
  else
    echo "[init] Warning: automatic workflow import failed; you can import workflow.json manually in n8n UI."
  fi
fi

exec n8n start
