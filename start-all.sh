#!/bin/bash
# MeshCore Bot Fleet - Startup Script
# Usage: $0 [start|stop|restart|status]

BOTDIR="/home/joe/meshcore-bots"
LOGDIR="/tmp"
HUB="mesh-hub.js"
BOTS=("bot-quotebot.js" "bot-weatherbot.js" "bot-quakebot-v2.js" "bot-quakealert.js" "bot-gasbot.js" "bot-surfbot.js" "bot-testbot.js")

start_hub() {
    if pgrep -f "$HUB" > /dev/null 2>&1; then
        echo "Hub already running (PID $(pgrep -f $HUB))"
    else
        echo "Starting hub..."
        sg dialout -c "cd $BOTDIR && nohup node $HUB >> $LOGDIR/mesh-hub.log 2>&1 &"
        sleep 2
        if pgrep -f "$HUB" > /dev/null 2>&1; then
            echo "Hub started (PID $(pgrep -f $HUB))"
        else
            echo "ERROR: Hub failed to start"
            return 1
        fi
    fi
}

stop_hub() {
    if pgrep -f "$HUB" > /dev/null 2>&1; then
        echo "Stopping hub..."
        pkill -f "$HUB"
    else
        echo "Hub not running."
    fi
}

start_bots() {
    for bot in "${BOTS[@]}"; do
        logname="${bot%.js}"
        if pgrep -f "$bot" > /dev/null 2>&1; then
            echo "$bot already running (PID $(pgrep -f $bot))"
        else
            echo "Starting $bot..."
            nohup bash -c "cd $BOTDIR && node $bot" >> "$LOGDIR/$logname.log" 2>&1 &
            sleep 1
            if pgrep -f "$bot" > /dev/null 2>&1; then
                echo "$bot started (PID $(pgrep -f $bot))"
            else
                echo "ERROR: $bot failed to start"
            fi
        fi
    done
}

stop_bots() {
    for bot in "${BOTS[@]}"; do
        if pgrep -f "$bot" > /dev/null 2>&1; then
            echo "Stopping $bot..."
            pkill -f "$bot"
        else
            echo "$bot not running."
        fi
    done
}

status() {
    echo "--- Hub status ---"
    if pgrep -f "$HUB" > /dev/null 2>&1; then
        echo "hub running (PID $(pgrep -f $HUB))"
    else
        echo "hub stopped"
    fi
    echo "--- Bots status ---"
    for bot in "${BOTS[@]}"; do
        if pgrep -f "$bot" > /dev/null 2>&1; then
            echo "$bot running (PID $(pgrep -f $bot))"
        else
            echo "$bot stopped"
        fi
    done
}

ACTION="${1:-start}"
case "$ACTION" in
    start)
        start_hub && start_bots
        ;;
    stop)
        stop_bots && stop_hub
        ;;
    restart)
        stop_bots && stop_hub
        start_hub && start_bots
        ;;
    status)
        status
        ;;
    *)
        echo "Usage: $0 [start|stop|restart|status]"
        exit 1
        ;;
esac
