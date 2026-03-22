#!/usr/bin/env bash
# start_hub.sh — Start the mesh-hub and all bot plugins
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "Starting MeshCore Hub architecture..."

# Start the hub (serial connection owner)
nohup node mesh-hub.js >> hub.log 2>&1 &
echo "Started mesh-hub.js (PID $!) -> hub.log"
sleep 2

# Start bot plugins
nohup node bot-quotebot.js >> quotebot.log 2>&1 &
echo "Started bot-quotebot.js (PID $!) -> quotebot.log"

nohup node bot-weatherbot.js >> weatherbot.log 2>&1 &
echo "Started bot-weatherbot.js (PID $!) -> weatherbot.log"

nohup node bot-reporter.js >> reporter.log 2>&1 &
echo "Started bot-reporter.js (PID $!) -> reporter.log"

echo "All processes started."
