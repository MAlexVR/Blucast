#!/bin/bash

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo -e "${YELLOW}══════════════════════════════════════${NC}"
echo -e "${YELLOW}     BluCast Uninstaller${NC}"
echo -e "${YELLOW}══════════════════════════════════════${NC}"
echo ""

step() { echo -e "  ${BLUE}→${NC} $*"; }
done_msg() { echo -e "  ${GREEN}✓${NC} $*"; }

step "Stopping BluCast containers..."
for cmd in podman docker; do
    if command -v "$cmd" &>/dev/null; then
        for pattern in blucast "ghcr.io/andrei9383/blucast"; do
            ids=$($cmd ps -q --filter "ancestor=$pattern" 2>/dev/null || true)
            [ -n "$ids" ] && $cmd stop $ids 2>/dev/null || true
        done
    fi
done
done_msg "Containers stopped"

step "Killing watcher processes..."
pkill -f "vcam_watcher" 2>/dev/null || true
done_msg "Watchers killed"

step "Unloading v4l2loopback module..."
sudo modprobe -r v4l2loopback 2>/dev/null || true
done_msg "Module unloaded"

step "Removing v4l2loopback system config..."
sudo rm -f /etc/modules-load.d/v4l2loopback.conf
sudo rm -f /etc/modprobe.d/v4l2loopback.conf
sudo rm -f /etc/udev/rules.d/83-blucast-vcam.rules
sudo rm -f /etc/sudoers.d/blucast-v4l2loopback
sudo udevadm control --reload-rules 2>/dev/null || true
done_msg "System config removed"

step "Removing user configuration..."
rm -rf "$HOME/.config/blucast"
done_msg "User config removed"

step "Removing desktop entry..."
rm -f "$HOME/.local/share/applications/blucast.desktop"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
done_msg "Desktop entry removed"

step "Removing installed app files..."
rm -f "$HOME/.local/bin/blucast"
rm -rf "$HOME/.local/share/blucast"
done_msg "App files removed"

step "Cleaning runtime files..."
rm -rf /tmp/blucast
done_msg "Runtime files cleaned"

step "Removing container images..."
for cmd in podman docker; do
    if command -v "$cmd" &>/dev/null; then
        for img in "blucast:latest" "localhost/blucast:latest" "ghcr.io/andrei9383/blucast:latest"; do
            $cmd rmi "$img" 2>/dev/null || true
        done
    fi
done
done_msg "Container images removed"

step "Refreshing media services..."
for svc in wireplumber.service xdg-desktop-portal.service \
           xdg-desktop-portal-gtk.service xdg-desktop-portal-gnome.service; do
    systemctl --user restart "$svc" 2>/dev/null || true
done
sleep 1
done_msg "Media services refreshed"

echo ""
echo -e "${GREEN}══════════════════════════════════════${NC}"
echo -e "${GREEN}     BluCast uninstalled!${NC}"
echo -e "${GREEN}══════════════════════════════════════${NC}"
echo ""
echo -e "  To reinstall: ${BLUE}curl -fsSL https://raw.githubusercontent.com/MAlexVR/Blucast/main/scripts/install-remote.sh | bash${NC}"
echo ""
