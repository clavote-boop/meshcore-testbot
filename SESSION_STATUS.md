# MeshCore Bot Session Status — 2026-03-22

## SYSTEM STATUS: ALL STABLE AND RUNNING
- Hub (mesh-hub.js): Running on port 7777, push listener + 30s poll, dedup mutex fixed
- Quotebot (bot-quotebot.js): Running, responds to "Quotebot" trigger
- Weatherbot (bot-weatherbot.js): Running, responds to "Weatherbot" or "Weatherbot <location>"
- Dashboard: localhost:3000, connected and logging
- Data file: mesh_data.json (619 msgs, 76 nodes, 66 quotebot reqs as of session end)
- Git: clean, all pushed to github.com/clavote-boop/meshcore-testbot master

## NEXT TASK: SEND LAUNCH ANNOUNCEMENT
Send this 4-line announcement to MC #test channel (channel messages, NOT DM):

Line 1: Hi #test channel. Two new bots available from Clem Heavyside.
Line 2: Quotebot — send "Quotebot" for a random quote. Response includes your path distance to the bot in miles, handy for checking mesh reach.
Line 3: Weatherbot — send "Weatherbot" for local weather at your node location. For other areas send "Weatherbot" followed by a zip code, city, county, or state.
Line 4: Give them a try and have fun. 73 de Clem

## KEY DETAILS
- Serial port: /dev/ttyUSB0, Hub port: 7777
- Node name: Clem Heavyside
- Quotebot triggers: "Quotebot", "!quote", "!q"
- Weatherbot triggers: "Weatherbot" (local), "Weatherbot <location>" (other)
- Default coords: 37.2713, -121.8366 (San Jose area)
- Start hub: sg dialout -c "nohup node /home/joe/meshcore-bots/mesh-hub.js > /tmp/mesh-hub.log 2>&1 &"
- Start bots: nohup node /home/joe/meshcore-bots/bot-quotebot.js > /tmp/bot-quotebot.log 2>&1 &
- Start bots: nohup node /home/joe/meshcore-bots/bot-weatherbot.js > /tmp/bot-weatherbot.log 2>&1 &
- Logs: /tmp/mesh-hub.log, /tmp/bot-quotebot.log, /tmp/bot-weatherbot.log

## DO NOT mention AI in the announcement. Say "Clem Heavyside" only.
## DO NOT ask for feedback in announcement (no way to reply from dashboard yet).

## RECENT COMMITS
- 1debca7 Update mesh_data.json, add .bak to gitignore
- 9722918 Fix fetchingMessages mutex: remove duplicate guard, add finally block in push handler
- c1161e4 Add fetchingMessages mutex to prevent duplicate message dispatch
- e5c4fd3 hub push listener patch
- dfbd6d0 Add path distance (miles) to quotebot response
- 279221e Add @requesterName to quotebot and weatherbot responses, multi-line quotebot
