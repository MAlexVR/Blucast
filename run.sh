#!/bin/bash
set -euo pipefail

GHCR_IMAGE="ghcr.io/andrei9383/blucast:latest"
LOCAL_IMAGE="localhost/blucast:latest"
VCAM_DEVICE="/dev/video10"
SHARED_DIR="/tmp/blucast"
INSTALL_DIR="$HOME/.local/share/blucast"

# Passed by the autostart .desktop entry so the app opens straight to the
# tray on login instead of popping its window open every time.
START_MINIMIZED=0
if [ "${1:-}" = "--autostart" ]; then
    START_MINIMIZED=1
fi

if command -v podman &>/dev/null; then
    CONTAINER_CMD="podman"
elif command -v docker &>/dev/null; then
    CONTAINER_CMD="docker"
else
    echo "Error: podman or docker required"; exit 1
fi

# Prefer a locally-built image (e.g. from ./install.sh) over the published
# one, so contributors testing their own build don't need to retag anything.
if $CONTAINER_CMD image exists "$LOCAL_IMAGE" 2>/dev/null || \
   $CONTAINER_CMD inspect "$LOCAL_IMAGE" &>/dev/null 2>&1; then
    IMAGE_NAME="$LOCAL_IMAGE"
else
    IMAGE_NAME="$GHCR_IMAGE"
    if ! $CONTAINER_CMD inspect "$IMAGE_NAME" &>/dev/null 2>&1; then
        echo "Pulling BluCast image..."
        $CONTAINER_CMD pull "$IMAGE_NAME"
    fi
fi

MODULE_JUST_LOADED=0
if [ ! -e "$VCAM_DEVICE" ]; then
    echo "Loading virtual camera module..."
    MODULE_JUST_LOADED=1
    if lsmod | grep -q '^v4l2loopback'; then
        sudo -n modprobe -r v4l2loopback 2>/dev/null || \
            pkexec modprobe -r v4l2loopback 2>/dev/null || true
        sleep 1
    fi
    if sudo -n modprobe v4l2loopback devices=1 video_nr=10 \
        card_label="BluCast Virtual Camera" exclusive_caps=1 \
        max_buffers=2 max_openers=10 2>/dev/null; then
        sleep 1
    elif command -v pkexec &>/dev/null; then
        if ! pkexec modprobe v4l2loopback devices=1 video_nr=10 \
            card_label="BluCast Virtual Camera" exclusive_caps=1 \
            max_buffers=2 max_openers=10; then
            echo "Error: Cannot load v4l2loopback module."
            echo "Run: sudo modprobe v4l2loopback devices=1 video_nr=10 card_label='BluCast Virtual Camera' exclusive_caps=1"
            exit 1
        fi
        sleep 1
    else
        echo "Error: Cannot load v4l2loopback module."
        echo "Run: sudo modprobe v4l2loopback devices=1 video_nr=10 card_label='BluCast Virtual Camera' exclusive_caps=1"
        exit 1
    fi
fi

[ -e "$VCAM_DEVICE" ] || { echo "Error: $VCAM_DEVICE not found"; exit 1; }

sudo -n chmod 666 "$VCAM_DEVICE" 2>/dev/null || chmod 666 "$VCAM_DEVICE" 2>/dev/null || true
sudo -n udevadm trigger --action=change "$VCAM_DEVICE" 2>/dev/null || true
sleep 1

# Only bounce PipeWire/portals the first time the module is (re)loaded, so
# apps like Spotify don't lose their audio stream on every BluCast launch.
if [ "$MODULE_JUST_LOADED" = "1" ]; then
    for svc in wireplumber.service xdg-desktop-portal.service \
               xdg-desktop-portal-gtk.service xdg-desktop-portal-gnome.service; do
        systemctl --user restart "$svc" 2>/dev/null || true
    done
    sleep 2
fi

mkdir -p "$SHARED_DIR"
echo "0" > "$SHARED_DIR/consumers"
rm -f "$SHARED_DIR/preview.jpg" "$SHARED_DIR/cmd.pipe"

xhost +local: 2>/dev/null || true

WATCHER_PID=""
if [ -x "$INSTALL_DIR/vcam_watcher.sh" ]; then
    "$INSTALL_DIR/vcam_watcher.sh" "$VCAM_DEVICE" &
    WATCHER_PID=$!
fi

cleanup() {
    [ -n "$WATCHER_PID" ] && kill "$WATCHER_PID" 2>/dev/null || true
    rm -f "$SHARED_DIR/consumers" "$SHARED_DIR/preview.jpg" "$SHARED_DIR/cmd.pipe" \
          "$SHARED_DIR/server.pid" "$SHARED_DIR/.xauth"
}
trap cleanup EXIT

if [ "$CONTAINER_CMD" = "podman" ]; then
    GPU_ARGS="--device nvidia.com/gpu=all"
else
    GPU_ARGS="--gpus all"
fi

CAMERA_ARGS=""
for cam in /dev/video*; do
    [ -e "$cam" ] && CAMERA_ARGS="$CAMERA_ARGS --device $cam:$cam"
done

XAUTH_ARGS=""
XAUTH_FILE="$SHARED_DIR/.xauth"
if command -v xauth &>/dev/null && [ -n "${DISPLAY:-}" ]; then
    touch "$XAUTH_FILE"
    xauth nlist "$DISPLAY" 2>/dev/null | sed -e 's/^..../ffff/' \
        | xauth -f "$XAUTH_FILE" nmerge - 2>/dev/null || true
    if [ -s "$XAUTH_FILE" ]; then
        XAUTH_ARGS="-v $XAUTH_FILE:/root/.Xauthority:ro -e XAUTHORITY=/root/.Xauthority"
    fi
fi
if [ -z "$XAUTH_ARGS" ]; then
    for f in "${XAUTHORITY:-}" "$HOME/.Xauthority"; do
        if [ -n "$f" ] && [ -f "$f" ]; then
            XAUTH_ARGS="-v $f:/root/.Xauthority:ro"
            break
        fi
    done
fi

DBUS_ARGS=""
if [ -n "${DBUS_SESSION_BUS_ADDRESS:-}" ]; then
    DBUS_SOCKET="${DBUS_SESSION_BUS_ADDRESS#unix:path=}"
    DBUS_SOCKET="${DBUS_SOCKET%%,*}"
    if [ -S "$DBUS_SOCKET" ]; then
        DBUS_ARGS="-v $DBUS_SOCKET:$DBUS_SOCKET -e DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"
    fi
fi

CONFIG_DIR="$HOME/.config/blucast"
mkdir -p "$CONFIG_DIR"

AUTOSTART_DIR="$HOME/.config/autostart"
mkdir -p "$AUTOSTART_DIR"

echo "Starting BluCast..."

# Qt's native "wayland" QPA plugin reports isSystemTrayAvailable()=False —
# it doesn't implement StatusNotifierItem detection, so the tray icon never
# registers no matter what GNOME extension is enabled. Route through XWayland
# (QT_QPA_PLATFORM=xcb) instead, whose SNI tray support is the well-tested
# path; XWayland is present in any GNOME Wayland session, so this costs
# nothing besides skipping native-Wayland-only rendering niceties.
GUI_ARGS=(
    -e "QT_QPA_PLATFORM=xcb"
    -e "DISPLAY=${DISPLAY:-:0}"
    -e XDG_RUNTIME_DIR=/tmp/runtime-root
    -v "/tmp/.X11-unix:/tmp/.X11-unix:rw"
)

# --userns=keep-id maps the container's root to our own host UID, which is
# required for the D-Bus session bus's SASL EXTERNAL auth to succeed (the
# bus only accepts connections whose kernel-verified UID matches the session
# owner) — without it, the tray icon silently fails to register at all.
# That mapping means /root (owned by uid 0 in the image) is no longer
# traversable by our UID, so we replace it wholesale with a host-owned
# directory and layer the real settings/autostart paths on top of it.
CONTAINER_HOME="$INSTALL_DIR/container-home"
mkdir -p "$CONTAINER_HOME"

$CONTAINER_CMD run --rm \
    --security-opt label=disable \
    --userns=keep-id \
    $GPU_ARGS \
    $CAMERA_ARGS \
    -e NVIDIA_DRIVER_CAPABILITIES=all \
    -e NVIDIA_VISIBLE_DEVICES=all \
    -e QT_LOGGING_RULES="*.debug=false" \
    -e HOST_RUN_SCRIPT="$INSTALL_DIR/run.sh" \
    -e BLUCAST_START_MINIMIZED="$START_MINIMIZED" \
    "${GUI_ARGS[@]}" \
    $XAUTH_ARGS \
    $DBUS_ARGS \
    -v "$HOME:/host_home:ro" \
    -v "$CONTAINER_HOME:/root:rw" \
    -v "$CONFIG_DIR:/root/.config/blucast:rw" \
    -v "$AUTOSTART_DIR:/root/.config/autostart:rw" \
    -v "$SHARED_DIR:$SHARED_DIR:rw" \
    -v "$INSTALL_DIR/control_panel.py:/app/control_panel.py:ro" \
    -v "/dev/dri:/dev/dri" \
    --ipc=host \
    --network host \
    "$IMAGE_NAME" 2>&1
