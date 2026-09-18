#!/usr/bin/env bash

set -e

SELF="$(realpath "$0")"
LAB1="$(dirname "$SELF")"
API="$LAB1/api/api"
LAUNCHER="$LAB1/seccomp-launcher"

# мы уже внутри namespaces
if [[ "$1" == "--inside-ns" ]]; then
    hostname lab1-api

    # в новом net namespace loopback не поднят
    ip link set lo up

    echo "pid=$$"
    echo "uid=$(id -u)"
    echo "hostname=$(hostname)"

    # все что требовало прав уже сделали, теперь их можно отобрать
    # launcher сверху еще повесит seccomp
    exec setpriv \
        --bounding-set=-all \
        --inh-caps=-all \
        --ambient-caps=-all \
        --no-new-privs \
        "$LAUNCHER" "$API"
fi

# сначала переезжаем из manager в api cgroup
if [[ "$1" == "--enter-api" ]]; then
    echo $$ > "$BASE/api/cgroup.procs"

    echo "cgroup:"
    cat /proc/self/cgroup

    # теперь собственно изоляция
    exec unshare \
        --user \
        --map-root-user \
        --mount \
        --uts \
        --ipc \
        --pid \
        --net \
        --fork \
        --mount-proc \
        "$SELF" --inside-ns
fi

# systemd уже создал нам отдельный scope и отдал его поддерево
if [[ "$1" == "--delegated" ]]; then
    CG="$(awk -F: '{print $3}' /proc/self/cgroup)"
    BASE="/sys/fs/cgroup$CG"

    echo "cgroup: $BASE"

    # DelegateSubgroup у меня не заработал как хотелось, делаем manager сами
    mkdir "$BASE/manager"

    # cat здесь использовать нельзя, иначе он сам может попасть в cgroup.procs
    mapfile -t scope_pids < "$BASE/cgroup.procs"

    for pid in "${scope_pids[@]}"; do
        echo "$pid" > "$BASE/manager/cgroup.procs"
    done

    # в корне никого быть не должно
    mapfile -t left_pids < "$BASE/cgroup.procs"

    if ((${#left_pids[@]} != 0)); then
        echo "в корне scope остались процессы:" >&2
        printf '%s\n' "${left_pids[@]}" >&2
        exit 1
    fi

    # разрешаем детям пользоваться нужными контроллерами
    echo '+cpu +memory +pids' > "$BASE/cgroup.subtree_control"

    mkdir "$BASE/api"

    # при выходе убиваем весь workload, а не только внешний unshare
    cleanup() {
        trap - EXIT

        if [[ -e "$BASE/api/cgroup.kill" ]]; then
            echo 1 > "$BASE/api/cgroup.kill" 2>/dev/null || true
        fi

        if [[ -n "${workload_pid:-}" ]]; then
            wait "$workload_pid" 2>/dev/null || true
        fi
    }

    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # лимиты из предыдущей части
    echo 32M > "$BASE/api/memory.max"
    echo 0 > "$BASE/api/memory.swap.max"
    echo '50000 100000' > "$BASE/api/cpu.max"
    echo 20 > "$BASE/api/pids.max"

    echo "memory.max=$(cat "$BASE/api/memory.max")"
    echo "cpu.max=$(cat "$BASE/api/cpu.max")"
    echo "pids.max=$(cat "$BASE/api/pids.max")"

    # дальше процесс уйдет в api cgroup, namespaces и станет api
    BASE="$BASE" "$SELF" --enter-api &
    workload_pid=$!

    # ждем пока появится api
    for _ in {1..50}; do
        mapfile -t api_pids < "$BASE/api/cgroup.procs"

        for pid in "${api_pids[@]}"; do
            if [[ -r "/proc/$pid/comm" ]] && [[ "$(cat "/proc/$pid/comm")" == "api" ]]; then
                API_PID="$pid"
                break 2
            fi
        done

        sleep 0.1
    done

    if [[ -z "${API_PID:-}" ]]; then
        echo "api так и не запустился" >&2
        exit 1
    fi

    echo
    echo "api host pid: $API_PID"
    echo "health:"
    echo "sudo nsenter -t $API_PID --net curl http://127.0.0.1:8080/health"

    # ждем именно внешний workload
    if wait "$workload_pid"; then
        status=0
    else
        status=$?
    fi

    exit "$status"
fi

# запуск начинается

cd "$LAB1/api"
go build -o api .

cd "$LAB1"

gcc -Wall -Wextra -Werror -O2 \
    -o seccomp-launcher \
    seccomp-launcher.c \
    -lseccomp

# уникальное имя, чтобы случайно не наткнуться на старый скоуп
UNIT="lab1-$$.scope"

exec systemd-run \
    --user \
    --scope \
    --collect \
    --unit="$UNIT" \
    --property=Delegate=yes \
    "$SELF" --delegated