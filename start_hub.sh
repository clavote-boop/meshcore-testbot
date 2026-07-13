#!/usr/bin/env bash
# start_hub.sh — Start the mesh-hub and all bot plugins
# Uses sg dialout to ensure serial port access
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "Starting MeshCore Hub architecture..."

# Kill any existing processes
kill $(pgrep -f mesh-hub) 2>/dev/null || true
kill $(pgrep -f "bot-quotebot\|bot-weatherbot\|bot-quakebot\|bot-quakealert") 2>/dev/null || true
sleep 1

# Start the hub (serial connection owner) with dialout group
sg dialout -c "nohup node mesh-hub.js >> /tmp/mesh-hub.log 2>&1 &"
echo "Started mesh-hub.js -> /tmp/mesh-hub.log"
sleep 3

# Start bot plugins
nohup node bot-quotebot.js >> /tmp/bot-quotebot.log 2>&1 &
echo "Started bot-quotebot.js (PID $!) -> /tmp/bot-quotebot.log"

nohup node bot-weatherbot.js >> /tmp/bot-weatherbot.log 2>&1 &
echo "Started bot-weatherbot.js (PID $!) -> /tmp/bot-weatherbot.log"

nohup node bot-quakebot-v2.js >> /tmp/bot-quakebot-v2.log 2>&1 &
echo "Started bot-quakebot-v2.js (PID $!) -> /tmp/bot-quakebot-v2.log"

nohup node bot-quakealert.js >> /tmp/bot-quakealert.log 2>&1 &
echo "Started bot-quakealert.js (PID $!) -> /tmp/bot-quakealert.log"

echo "All processes started."
pgrep -af "mesh-hub\|bot-"
