#!/usr/bin/env bash
# Idle watcher: stops THIS Fly Machine when no OpenCode session is active for
# IDLE_MINUTES, or after MAX_UPTIME_HOURS no matter what (hard backstop).
# "Stop" = POST to the in-guest Machines API unix socket; the machine goes to
# `stopped` and compute billing stops immediately (per-second billing).
set -u

IDLE_MINUTES="${IDLE_MINUTES:-15}"
MAX_UPTIME_HOURS="${MAX_UPTIME_HOURS:-12}"
OC_PORT="${OC_PORT:-4096}"
STATE_DIR=/data/state

LOG() { echo "$(date -Is) watcher: $*"; }

stop_machine() {
  LOG "stopping machine ($1)"
  curl -sS --unix-socket /.fly/api -X POST \
    "http://flaps/v1/apps/${FLY_APP_NAME}/machines/${FLY_MACHINE_ID}/stop" || true
}

started="$(cat "$STATE_DIR/started_at" 2>/dev/null || date +%s)"
idle_since=""

while true; do
  now=$(date +%s)

  if (( now - started >= MAX_UPTIME_HOURS * 3600 )); then
    stop_machine "max uptime ${MAX_UPTIME_HOURS}h reached"
    exit 0
  fi

  active=0
  if [ -n "${OPENCODE_PASSWORD:-}" ]; then
    resp=$(curl -sf -u "opencode:${OPENCODE_PASSWORD}" \
      "http://127.0.0.1:${OC_PORT}/api/session/active" 2>/dev/null || true)
    if [ -n "$resp" ]; then
      active=$(printf '%s' "$resp" | jq 'length' 2>/dev/null || echo 0)
    fi
  fi

  if [ "$active" -gt 0 ]; then
    if [ -n "$idle_since" ]; then LOG "activity resumed ($active active session(s))"; fi
    idle_since=""
  else
    if [ -z "$idle_since" ]; then
      idle_since=$now
      LOG "no active sessions; idle timer started (${IDLE_MINUTES} min)"
    fi
    if (( now - idle_since >= IDLE_MINUTES * 60 )); then
      stop_machine "idle for ${IDLE_MINUTES} min"
      exit 0
    fi
  fi

  sleep 30
done
