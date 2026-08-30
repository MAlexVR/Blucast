#!/bin/bash
# BluCast Virtual Camera Consumer Watcher
# Counts processes READING from /dev/video10.
# Server opens O_WRONLY (lsof: 'w'), browsers open O_RDWR (lsof: 'u').

VCAM_DEVICE="${1:-/dev/video10}"
CONSUMERS_FILE="/tmp/blucast/consumers"
DISABLED_FLAG="/tmp/blucast/camera_disabled"

mkdir -p /tmp/blucast
echo "0" > "$CONSUMERS_FILE"

count_with_lsof() {
    lsof "$VCAM_DEVICE" 2>/dev/null | awk '
        NR > 1 && $4 ~ /[0-9]+[ru]$/ { pids[$2] = 1 }
        END { print length(pids) }
    '
}

count_with_fuser() {
    local pids total n
    pids=$(fuser "$VCAM_DEVICE" 2>/dev/null) || true
    total=$(echo "$pids" | wc -w)
    n=$((total - 1))
    [ $n -lt 0 ] && n=0
    echo "$n"
}

if command -v lsof &>/dev/null; then
    COUNT_FN="count_with_lsof"
elif command -v fuser &>/dev/null; then
    COUNT_FN="count_with_fuser"
else
    while true; do echo "0" > "$CONSUMERS_FILE"; sleep 5; done
    exit 0
fi

while true; do
    if [ -f "$DISABLED_FLAG" ]; then
        # User turned the virtual camera off from the app: report zero
        # consumers so the server drops to its idle frame even if an app
        # (e.g. a video call) is still holding the device open.
        echo "0" > "$CONSUMERS_FILE"
        sleep 1
        continue
    fi
    if [ ! -e "$VCAM_DEVICE" ]; then
        echo "0" > "$CONSUMERS_FILE"
        sleep 2
        continue
    fi
    n=$($COUNT_FN)
    [[ "$n" =~ ^[0-9]+$ ]] || n=0
    echo "$n" > "$CONSUMERS_FILE"
    sleep 1
done
