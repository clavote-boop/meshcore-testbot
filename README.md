# MeshCore Mesh Radio Bot Hub

A headless Node.js hub architecture for MeshCore mesh radio bots. One hub owns the serial connection to the radio; all bots connect via TCP on port 7777. Bots operate independently and can be restarted without affecting others.

## Architecture

The hub (mesh-hub.js) holds exclusive access to the serial port (/dev/ttyUSB0) and exposes a TCP server on port 7777. Each bot connects as a TCP client via hub-client.js. Messages are queued with 1.5s delay to avoid radio contention. Max message size is 190 bytes.

## Bots

### Quotebot (bot-quotebot.js)
- Returns random quotes on request. Includes requester name and path distance in miles. Uses up to 3 transmissions to avoid truncation.

### Weatherbot (bot-weatherbot.js)
- Returns current weather conditions. Includes requester name in response.

### Quakebot (bot-quakebot-v2.js)
- Reports earthquakes from USGS. Scheduled reports at 0600 and 1800 to #earthquake channel. Ends with a cute quip unless M5.5+ (then emergency info). All distances in miles.

### QuakeAlert (bot-quakealert.js)
- Real-time earthquake alerting with tiered system. Monitors USGS feed continuously. Alerts to appropriate channels based on magnitude.

### Gasbot (bot-gasbot.js)
- Finds nearby gas stations with prices and Google Maps links. Uses Google Places API (New) searchNearby. Shows 4 stations per page, up to 20 total. User types Y for next page. Each station shows: name, regular unleaded price, distance in miles, and a Google Maps search URL. Supports location search: "gasbot Los Angeles".

## Channels

- Ch 0 Public (Tier 3+ alerts)
- Ch 1 #test
- Ch 4 GUZMAN
- Ch 7 #earthquake-bayarea (PUBLIC)
- Ch 8 #earthquake-la (PUBLIC)
- Ch 9 #earthquake-sd (PUBLIC)
- Ch 10 #earthquake (PUBLIC)

## Setup

1. Clone repo
2. npm install
3. Copy .env.example to .env and fill in values
4. Start hub first: sg dialout -c "node mesh-hub.js"
5. Start bots: node bot-quotebot.js etc.

## Environment Variables

- SERIAL_PORT
- HUB_PORT
- MY_NODE_NAME
- DEFAULT_LAT
- DEFAULT_LON
- GOOGLE_API_KEY
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID

## Tech Stack

- Node.js
- @liamcottle/meshcore.js
- Google Places API (New)
- USGS Earthquake API
- Open-Meteo Geocoding API

Built by Clem Heavyside.