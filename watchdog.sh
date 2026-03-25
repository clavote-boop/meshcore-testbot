#!/bin/bash
# MeshCore Bot Fleet Watchdog
# Checks processes and restarts if missing, logging to /tmp/meshcore-watchdog.log

BOTDIR="/home/joe/meshcore-bots"
LOGDIR="/tmp"
WATCHDOG_LOG="$LOGDIR/meshcore-watchdog.log"
HUB="mesh-hub.js"
BOTS=("bot-quotebot.js" "bot-weatherbot.js" "bot-quakebot-v2.js" "bot-quakealert.js" "bot-gasbot.js" "bot-surfbot.js" "bot-testbot.js")

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $1" >> "$WATCHDOG_LOG"
}

# Check hub
if ! pgrep -f "$HUB" > /dev/null 2>&1; then
    log "Hub not running, restarting..."
    sg dialout -c "cd $BOTDIR && nohup node $HUB >> $LOGDIR/mesh-hub.log 2>&1 &"
    sleep 2
    if pgrep -f "$HUB" > /dev/null 2>&1; then
        log "Hub restarted (PID $(pgrep -f $HUB))"
    else
        log "ERROR: Hub failed to restart"
    fi
fi

# Check bots
for bot in "${BOTS[@]}"; do
    if ! pgrep -f "$bot" > /dev/null 2>&1; then
        log "${bot} not running, restarting..."
        logname="${bot%.js}"
        nohup bash -c "cd $BOTDIR && node $bot" >> "$LOGDIR/$logname.log" 2>&1 &
        sleep 1
        if pgrep -f "$bot" > /dev/null 2>&1; then
            log "${bot} restarted (PID $(pgrep -f $bot))"
        else
            log "ERROR: ${bot} failed to restart"
        fi
    fi
done
