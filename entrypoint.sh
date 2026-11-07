#!/usr/bin/env bash
# VM entrypoint: starts the OpenCode server and the idle watcher, then sleeps.
# The machine is stopped by watcher.sh via the in-guest Machines API socket
# (or manually with `fly machine stop`); compute billing is per-second.
set -u
export PATH="/root/.local/bin:${PATH}"

mkdir -p /data/logs /data/state /root/work

# OpenCode server password: use the OPENCODE_PASSWORD Fly secret if set,
# otherwise generate one. Either way it is recoverable via:
#   fly machine exec <id> -a <app> -- cat /data/state/opencode_password
if [ -z "${OPENCODE_PASSWORD:-}" ]; then
  OPENCODE_PASSWORD="$(openssl rand -hex 24)"
  export OPENCODE_PASSWORD
fi
umask 077
printf '%s' "$OPENCODE_PASSWORD" > /data/state/opencode_password

# Persist the OpenCode data dir (session DB etc.) on the volume so session
# history survives stop/start. Rootfs itself is wiped on stop.
mkdir -p /root/.local/share /data/opencode-data
ln -sfn /data/opencode-data /root/.local/share/opencode

# Boot timestamp for the watcher's max-uptime backstop.
date +%s > /data/state/started_at

# 0.0.0.0 inside the VM is fine: the app exposes no fly services, so the port
# is reachable only over the WireGuard private network (fly proxy / fly ssh).
nohup opencode serve --hostname 0.0.0.0 --port 4096 >/data/logs/opencode-serve.log 2>&1 &
nohup /usr/local/bin/watcher.sh >/data/logs/watcher.log 2>&1 &

echo "entrypoint: opencode server on :4096, watcher running, pid $$"
exec sleep infinity
