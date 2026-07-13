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

# Python hub mode: when flag present, watchdog does NOT manage the JS hub
# (meshhub.py runs as a systemd service instead). USB rebind + bots still managed.
USE_PY_HUB=""
if [ -f "$BOTDIR/.use-python-hub" ]; then USE_PY_HUB=1; fi

# Check USB radio device
HEARTBEAT="/tmp/mesh-hub-heartbeat"
STALE_MINS=15
if [ ! -e /dev/ttyUSB0 ]; then
  log "USB radio disconnected - attempting rebind"
  [ -z "$USE_PY_HUB" ] && kill $(pgrep -f "$HUB") 2>/dev/null
  sleep 1
  usbipd.exe attach --wsl --busid 1-2 2>/dev/null 2>/dev/null
  sleep 5
  if [ -e /dev/ttyUSB0 ]; then
    log "USB radio restored"
    [ -z "$USE_PY_HUB" ] && sg dialout -c "cd $BOTDIR && nohup node $HUB >> $LOGDIR/mesh-hub.log 2>&1 &"
    sleep 2
  else
    log "ERROR: USB radio not restored"
  fi
fi

if [ -z "$USE_PY_HUB" ]; then
# Check hub heartbeat staleness
if [ -f "$HEARTBEAT" ] && [ -n "$(find "$HEARTBEAT" -mmin +$STALE_MINS 2>/dev/null)" ]; then
  log "Hub heartbeat stale (>$STALE_MINS min) - restarting hub"
  kill $(pgrep -f "$HUB") 2>/dev/null
  sleep 1
  sg dialout -c "cd $BOTDIR && nohup node $HUB >> $LOGDIR/mesh-hub.log 2>&1 &"
  sleep 2
  rm -f "$HEARTBEAT"
fi

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
fi

# Dashboard kill switch: hold bots down while flag present
KILLED=""
if [ -f "$BOTDIR/.killed" ]; then KILLED=1; log "Kill flag set - bots held down by dashboard"; fi

# Check bots (skipped when kill flag set)
if [ -z "$KILLED" ]; then
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
fi
