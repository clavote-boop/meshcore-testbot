#!/usr/bin/env bash
# Persistent, auto-resetting meshimage receiver for any fleet node. Always listening; re-arms after
# every image or idle timeout; archives each decoded image with a timestamp. Fully env-driven so the
# same file works on JOERYZEN (root, venv) and the N100 (systemd --user, /home/joe, system python).
#
#   MS_HUB_HOST     hub/relay host   (default 127.0.0.1)   -- read by meshimage.py
#   MS_HUB_PORT     hub/relay port   (default 7778)        -- read by meshimage.py
#   MESHIMG_CH      channel index    (default 7)
#   MESHIMG_LABEL   label for logs/archive files (default relay)
#   MESHIMG_PY      python to use    (default python3; JOERYZEN sets /root/caap-venv/bin/python)
#   MESHIMG_DIR     dir containing meshimage.py (default: this script's dir)
#   MESHIMG_ARCHIVE decoded-image archive dir (default $HOME/received_images)
DIR="${MESHIMG_DIR:-$(cd "$(dirname "$0")" && pwd)}"
cd "$DIR" || { echo "no dir $DIR"; exit 1; }
PY="${MESHIMG_PY:-python3}"
CH="${MESHIMG_CH:-7}"
LABEL="${MESHIMG_LABEL:-relay}"
ARCHIVE="${MESHIMG_ARCHIVE:-$HOME/received_images}"
LOG="${MESHIMG_LOG:-$HOME/.meshspeak/${LABEL}_imgrecv.log}"
mkdir -p "$ARCHIVE" "$(dirname "$LOG")"
echo "[$(date +%F\ %T)] meshimage receiver ($LABEL) up: ${MS_HUB_HOST:-127.0.0.1}:${MS_HUB_PORT:-7778} ch$CH -- always on" >> "$LOG"
while true; do
  OUT="$ARCHIVE/${LABEL}_latest.json"
  if "$PY" meshimage.py recv --channel "$CH" --timeout 900 --out "$OUT" >> "$LOG" 2>&1; then
    TS=$(date +%Y%m%d_%H%M%S)
    cp "$OUT" "$ARCHIVE/${LABEL}_${TS}.json" 2>/dev/null
    echo "[$(date +%F\ %T)] $LABEL: IMAGE RECEIVED -> ${LABEL}_${TS}.json" >> "$LOG"
  fi
  sleep 1   # tiny gap, then re-arm (auto-reset)
done
