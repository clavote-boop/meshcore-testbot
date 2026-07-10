#!/bin/bash
# Reproducible deploy of the mesh services (Clem chatbot + hub + dashboard).
# systemd USER services; persistence = `enable` + user lingering + WSL auto-start task.
set -e
DEST=~/.config/systemd/user
mkdir -p "$DEST"
cp "$(dirname "$0")/meshtalk-chatbot.service" "$DEST/meshtalk-chatbot.service"
# services start at boot without an interactive login:
loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now meshtalk-chatbot.service
echo "installed + enabled. status:"
systemctl --user is-active  meshtalk-chatbot.service
systemctl --user is-enabled meshtalk-chatbot.service
echo "Linger: $(loginctl show-user "$USER" --property=Linger)"
