#!/usr/bin/env bash

set -e

MEMORY_MAX=$((100 * 1024 * 1024))
CPU_MAX="50000 100000"
PIDS_MAX=10

APP="$1"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SECCOMP_PROFILE="$SCRIPT_DIR/seccomp.json"

PORT=8080
CGROUP="/sys/fs/cgroup/lab1-$$"

if [ -z "$APP" ]; then
    echo "usage: $0 <executable>"
    exit 1
fi

cleanup() {
    if [ -n "$UNSHARE_PID" ]; then
        kill "$UNSHARE_PID" 2>/dev/null || true
    fi

    sudo rmdir "$CGROUP" 2>/dev/null || true
}

trap cleanup EXIT INT TERM

sudo mkdir "$CGROUP"

echo "$MEMORY_MAX" | sudo tee "$CGROUP/memory.max" > /dev/null
echo "$CPU_MAX"    | sudo tee "$CGROUP/cpu.max" > /dev/null
echo "$PIDS_MAX"   | sudo tee "$CGROUP/pids.max" > /dev/null

unshare \
    -pmnuiU \
    --fork \
    --mount-proc \
    --map-root-user \
    bash -c "
        hostname api
        ip link set lo up

        kill -STOP \$\$

        exec capsh --drop=all --caps='' --no-new-privs -- \
            -c 'exec seccomp-run --profile \"$SECCOMP_PROFILE\" -- \"$APP\"'
    " &

UNSHARE_PID=$!

while [ -z "$PID" ]; do
    PID=$(cat /proc/$UNSHARE_PID/task/$UNSHARE_PID/children 2>/dev/null | awk '{print $1}')
    sleep 0.1
done

echo "host pid: $PID"
echo "cgroup: $CGROUP"

echo "$PID" | sudo tee "$CGROUP/cgroup.procs" > /dev/null

kill -CONT "$PID"

for i in $(seq 1 30); do
    if sudo nsenter -t "$PID" -n \
        curl -fsS "http://127.0.0.1:$PORT/health" > /dev/null 2>&1
    then
        echo "api is healthy"
        break
    fi

    sleep 0.2
done

wait "$UNSHARE_PID"