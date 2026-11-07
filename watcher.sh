#!/usr/bin/env bash
# Idle watcher: stops THIS Fly Machine when no OpenCode session is running and
# no session has been updated within IDLE_MINUTES — or after MAX_UPTIME_HOURS
# no matter what (hard backstop).
#
# Stop = POST to the in-guest Machines API unix socket (/.fly/api); the machine
# reaches `stopped` and compute billing stops immediately (per-second billing).
#
# Idle semantics: "busy" = any session in /api/session/active is `running`,
# OR any session's time.updated is within the idle window (so reading results
# / thinking between prompts does not trigger a stop). This covers multiple
# sessions in different repos: any activity anywhere resets the timer.
set -u

IDLE_MINUTES="${IDLE_MINUTES:-15}"
MAX_UPTIME_HOURS="${MAX_UPTIME_HOURS:-12}"
OC_PORT="${OC_PORT:-4096}"
OC_API="http://127.0.0.1:${OC_PORT}"
AUTH="opencode:${OPENCODE_PASSWORD:-}"
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
  now_s=$(date +%s)

  if (( now_s - started >= MAX_UPTIME_HOURS * 3600 )); then
    stop_machine "max uptime ${MAX_UPTIME_HOURS}h reached"
    exit 0
  fi

  # Fail-open: if the API is unreachable or unparseable, treat as busy so we
  # never stop a machine that might be working (max-uptime still backstops).
  busy=1
  if [ -n "${OPENCODE_PASSWORD:-}" ]; then
    active_json=$(curl -sf -u "$AUTH" "$OC_API/api/session/active" 2>/dev/null || true)
    sessions_json=$(curl -sf -u "$AUTH" "$OC_API/api/session" 2>/dev/null || true)
    if [ -n "$active_json" ] && [ -n "$sessions_json" ]; then
      running=$(printf '%s' "$active_json" | jq '.data | length' 2>/dev/null || echo -1)
      # time.updated is epoch ms; first page of the session list is enough here
      last_updated_ms=$(printf '%s' "$sessions_json" | jq '[.data[].time.updated] | max // 0' 2>/dev/null || echo -1)
      if [ "$running" -ge 0 ] && [ "$last_updated_ms" -ge 0 ]; then
        now_ms=$(( now_s * 1000 ))
        if [ "$running" -gt 0 ] || (( now_ms - last_updated_ms < IDLE_MINUTES * 60 * 1000 )); then
          busy=1
        else
          busy=0
        fi
      fi
    fi
  fi

  if [ "$busy" -eq 1 ]; then
    if [ -n "$idle_since" ]; then LOG "activity detected; idle timer reset"; fi
    idle_since=""
  else
    if [ -z "$idle_since" ]; then
      idle_since=$now_s
      LOG "no running sessions, none updated recently; idle timer started (${IDLE_MINUTES} min)"
    fi
    if (( now_s - idle_since >= IDLE_MINUTES * 60 )); then
      stop_machine "idle for ${IDLE_MINUTES} min"
      exit 0
    fi
  fi

  sleep 30
done
