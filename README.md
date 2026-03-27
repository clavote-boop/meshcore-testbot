# MeshCore Bot Fleet

A resilient, self-healing bot fleet for [MeshCore](https://github.com/meshcore-dev/MeshCore) LoRa mesh radio networks. The system runs a central **hub** that owns the serial connection to a USB radio and broadcasts messages to pluggable **bot workers** over a local TCP bus. A **watchdog** and **systemd services** keep everything alive across reboots, USB disconnects, and process crashes.

## Architecture

```
USB Radio (/dev/ttyUSB0)
       │
         mesh-hub.js        ← serial owner, TCP server on 127.0.0.1:7777
                │
                  ┌────┼────┬────┬────┬────┬────┬────┐
                    │    │    │    │    │    │    │    │
                    quote weather quake quake- gas  surf test
                     bot   bot  bot-v2 alert  bot  bot  bot
                            │
                              hub-client.js      ← shared TCP client module used by every bot
                              ```

                              **mesh-hub.js** — Connects to the radio via `@liamcottle/meshcore.js`, manages channels, and relays incoming messages to every connected bot over a JSON-over-TCP protocol. It also sends flood adverts periodically so the node is visible on the mesh.

                              **hub-client.js** — Lightweight TCP client that each bot imports. Handles reconnection, JSON framing, and provides helpers like `sendChannelTextMessage()`.

                              **Bot plugins** — Each bot file (`bot-*.js`) connects to the hub, listens for channel messages, and responds when triggered by a keyword or schedule.

                              ## Bots

                              | Bot | Trigger | What it does |
                              |-----|---------|-------------|
                              | **bot-quotebot.js** | `!quote` | Responds with a random quote and the requester's path distance in miles |
                              | **bot-weatherbot.js** | `!weather` | Returns current conditions + 7-day forecast via Google weather API |
                              | **bot-quakebot-v2.js** | scheduled | Polls USGS for earthquakes in configured regions and posts alerts |
                              | **bot-quakealert.js** | scheduled | Dedicated alerting for significant quakes (M4.0+) |
                              | **bot-gasbot.js** | `!gas` | Returns local gas prices |
                              | **bot-surfbot.js** | `!surf` | Returns local surf conditions |
                              | **bot-testbot.js** | `!test` / `!ping` | Diagnostic echo bot for verifying the mesh link |

                              ## Prerequisites

                              - **Node.js** ≥ 18 (ES modules)
                              - A MeshCore-compatible USB radio (CP2102 / CP2104 USB-to-UART bridge) on `/dev/ttyUSB0`
                              - Linux host (tested on Ubuntu/WSL2)
                              - User must be in the `dialout` group: `sudo usermod -aG dialout $USER`

                              ## Quick Start

                              ```bash
                              # Clone and install
                              git clone https://github.com/clavote-boop/meshcore-testbot.git
                              cd meshcore-testbot
                              npm install

                              # Create .env with your keys
                              cp .env.example .env   # then edit

                              # Start everything
                              chmod +x start_hub.sh
                              ./start_hub.sh
                              ```

                              ## Environment Variables (.env)

                              ```
                              TELEGRAM_BOT_TOKEN=...        # Optional: Telegram integration
                              TELEGRAM_CHAT_ID=...          # Optional: Telegram chat for alerts
                              DEFAULT_LAT=37.2713           # Default latitude for weather/quake lookups
                              DEFAULT_LON=-121.8366         # Default longitude
                              GOOGLE_API_KEY=...            # Google API key for weather
                              GUZMAN_SECRET=...             # Channel secret (hex)
                              SERIAL_PORT=/dev/ttyUSB0      # Serial device path
                              HUB_PORT=7777                 # TCP port for hub ↔ bot communication
                              ```

                              ## Production Deployment (systemd + watchdog)

                              For unattended operation the fleet is managed by two systemd user services and a bash watchdog.

                              ### watchdog.sh

                              Runs in a 60-second loop and handles:
                              - **USB radio recovery** — detects if `/dev/ttyUSB0` disappears (common in WSL2) and attempts a `usbipd.exe` rebind
                              - **Hub heartbeat** — kills and restarts the hub if it stops writing a heartbeat file
                              - **Hub process guard** — restarts `mesh-hub.js` via `sg dialout` if the process dies
                              - **Bot process guard** — restarts any bot that is not running

                              ### Systemd services

                              ```ini
                              # meshcore-watchdog.service — runs the watchdog loop
                              [Unit]
                              Description=MeshCore Watchdog (USB reconnect + process monitor)
                              After=network-online.target

                              [Service]
                              Type=simple
                              ExecStart=/bin/bash -c 'while true; do /home/joe/meshcore-bots/watchdog.sh; sleep 60; done'
                              Restart=always
                              RestartSec=30

                              [Install]
                              WantedBy=default.target
                              ```

                              ```ini
                              # openclaw-gateway.service — OpenClaw AI agent bridge
                              [Unit]
                              Description=OpenClaw Gateway
                              After=network-online.target

                              [Service]
                              Type=simple
                              ExecStart=...
                              Restart=always
                              RestartSec=10

                              [Install]
                              WantedBy=default.target
                              ```

                              Enable and start:

                              ```bash
                              systemctl --user daemon-reload
                              systemctl --user enable meshcore-watchdog.service
                              systemctl --user start meshcore-watchdog.service
                              ```

                              ### Fleet Status Check

                              ```bash
                              # Quick status
                              systemctl --user status meshcore-watchdog.service
                              pgrep -a "node mesh-hub"
                              pgrep -a "node bot-"
                              ls -la /dev/ttyUSB0
                              ```

                              ## File Overview

                              | File | Purpose |
                              |------|---------|
                              | `mesh-hub.js` | Serial connection owner + TCP message bus |
                              | `hub-client.js` | Shared TCP client for bots |
                              | `bot-quotebot.js` | Quote-of-the-day bot with path distance |
                              | `bot-weatherbot.js` | Weather forecast bot |
                              | `bot-reporter.js` | Periodic mesh status reporter |
                              | `quote_engine.js` | Quote selection logic |
                              | `quotes_feed.json` | Quote database |
                              | `mesh_data.json` | Mesh network metadata and channel config |
                              | `start_hub.sh` | One-shot startup script |
                              | `watchdog.sh` | Production watchdog (USB + process recovery) |
                              | `package.json` | Node.js dependencies (`@liamcottle/meshcore.js`, `dotenv`) |

                              ## OpenClaw Integration

                              The fleet is managed through [OpenClaw](https://openclaw.ai), an AI agent platform. The OpenClaw gateway service connects to the MeshCore hub and provides:
                              - Multi-channel auto-response via the AI agent
                              - Fleet monitoring and diagnostics from the dashboard
                              - Ability to issue commands (`fleet status?`) to check all components

                              The bridge code lives in the separate [meshcore-openclaw-bridge](https://github.com/clavote-boop/meshcore-openclaw-bridge) repo.

                              ## Credits & Acknowledgments

This project builds on the work of the [MeshCore](https://github.com/meshcore-dev/MeshCore) community (MIT License). In particular:

- **[meshcore-dev/MeshCore](https://github.com/meshcore-dev/MeshCore)** — The MeshCore firmware and protocol for LoRa packet radios. Our companion radio runs their firmware.
- **[meshcore-dev/meshcore.js](https://github.com/meshcore-dev/meshcore.js)** (by [@liamcottle](https://github.com/liamcottle)) — The JavaScript library that provides the serial/TCP/BLE connection API used by `mesh-hub.js` and all bot plugins. MIT License.
- **[OpenClaw](https://openclaw.ai)** — AI agent gateway that powers the fleet management and auto-response capabilities.

Bot connection patterns (command handling, channel messaging, flood adverts) are adapted from the `meshcore.js` examples with modifications for the hub/plugin architecture and OpenClaw integration.

## License

MIT — see [LICENSE](LICENSE) for details.

Portions of this code use [`@liamcottle/meshcore.js`](https://github.com/meshcore-dev/meshcore.js) which is also MIT licensed.
