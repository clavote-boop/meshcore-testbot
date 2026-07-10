#!/bin/bash
# Reproducible deploy of the mesh services (Clem chatbot + hub + dashboard).
# systemd USER services; persistence = `enable` + user lingering + WSL auto-start task.
set -e
DEST=~/.config/systemd/user
mkdir -p "$DEST"
cp "$(dirname "$0")/meshspeak-chatbot.service" "$DEST/meshspeak-chatbot.service"
# services start at boot without an interactive login:
loginctl enable-linger "$USER" 2>/dev/null || true
systemctl --user daemon-reload
systemctl --user enable --now meshspeak-chatbot.service
echo "installed + enabled. status:"
systemctl --user is-active  meshspeak-chatbot.service
systemctl --user is-enabled meshspeak-chatbot.service
echo "Linger: $(loginctl show-user "$USER" --property=Linger)"
