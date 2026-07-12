# MeshCore Bots — MeshSpeak, CAAP & holographic overlays over LoRa

A Python system for AI-agent communications and data transport over a
[MeshCore](https://github.com/meshcore-dev/MeshCore) LoRa mesh — and, when the radio is
down, over Tailscale. A central **hub** owns the USB radio and relays a line-delimited-JSON
protocol on TCP `127.0.0.1:7777`; every other component is a hub client. On top of the raw
channel sits **MeshSpeak** (fragmentation + compression + ChaCha20-Poly1305 AEAD + an
authenticated STS handshake) and a family of interconnecting overlay modules: reliable CAAP
capsule transport, agent-to-agent messaging, and holographic (any-subset-recoverable)
image/data codecs.

> The legacy Node.js bot fleet (`mesh-hub.js`, `bot-*.js`) is superseded by the Python stack
> below and lives only in git history.

## Module family (all interconnect via the hub protocol + MeshSpeak framing)

| Module | Role |
|---|---|
| `meshspeak.py` | The codec: frame/fragment, deflate, ChaCha20-Poly1305 AEAD, ACK-bitmap ARQ primitives, and **MeshSpeakSTS** (Ed25519 + ephemeral X25519 station-to-station handshake → forward secrecy, mutual auth). |
| `meshhub.py` | Radio owner. Drives the USB LoRa radio, serves the TCP hub on `:7777`, relays channel messages to every client (incl. local relay of raw wires so same-node clients hear each other — the radio is half-duplex). Service `meshhub`. |
| `meshrelay.py` | Radio-less **tailnet** hub on `:7778`, same protocol — so agents on different nodes handshake over IP when RF is down. Service `meshrelay`. |
| `caap_mesh.py` | CAAP ⇄ mesh bridge. Fragments any blob (CAAP-AUTH message, CRF-M frame, capsule); `send-capsule`/`recv-capsule` add **selective-repeat ARQ** so a full ~80-fragment Profile-A capsule survives a lossy channel. |
| `meshspeak_agent.py` | Agent-to-agent messaging: `send`/`recv` with a shared key, or `identity`/`send-sts`/`recv-sts` for **forward-secret, no-PSK** sessions. |
| `meshspeak_chatbot.py` | "Clem" — plaintext LLM channel bot (Venice). Sender allowlist + trigger gating; **channel-conditional persona** (guarded on Public, cooperative on working channels). Service `meshspeak-chatbot`. |
| `meshspeak_responder.py` | Clem's encrypted `dst=10` responder → OpenClaw gateway. Service `meshspeak-responder`. |

### Holographic overlays (2D / image / memory-transfer codecs)

| Module | Payload | Property |
|---|---|---|
| `meshcanvas.py` | grayscale image | DCT, low-freq first → graceful blur from any **prefix** of fragments |
| `meshphoto.py` | color photo | YCbCr + chroma subsample + perceptual quant → prefix-graceful, in color |
| `meshfountain.py` | text / exact data | LT fountain droplets → **byte-exact** from **any** sufficient subset |
| `meshcs.py` | images | random measurements (compressed sensing) → **any-subset** graceful via OMP |

**How they interconnect:** every component is a hub client speaking the same
`{action:register|send_channel}` → `{type:channel_message}` JSON. MeshSpeak provides the wire
framing (`frame_to_wire`/`wire_to_frame`, fragmentation, AEAD, STS). The overlays produce byte
payloads that ride MeshSpeak fragments over either `meshhub` (radio) or `meshrelay` (tailnet).
Design intent (next step): fold the overlays into `meshspeak` as importable submodules behind
one dispatch surface.

## Services (systemd --user)

`meshhub`, `meshspeak-chatbot`, `meshspeak-responder`, `meshrelay`, `control-dashboard`.
Persistence: `loginctl enable-linger` + a WSL keepalive task so the VM stays up.

```bash
systemctl --user restart meshspeak-chatbot.service
systemctl --user is-active meshhub.service
```

## Selftests

```bash
python3 meshspeak.py selftest        # codec + STS
python3 caap_mesh.py selftest        # bridge + ARQ (lossy-hub round-trip)
python3 meshspeak_agent.py selftest  # PSK + forward-secret STS
python3 meshfountain.py              # exact any-subset recovery
python3 meshcs.py                    # compressed-sensing any-subset image
python3 meshcanvas.py                # grayscale holographic demo
python3 meshphoto.py                 # color photo demo
```

## Transports

- **LoRa RF** via `meshhub` on the radio (`/dev/ttyUSB0`). WSL note: if the radio detaches,
  `usbipd attach --wsl --busid 1-2` from Windows (a held WSL session must be open).
- **Tailscale** via `meshrelay` on the node's tailnet IP `:7778` when RF is down. Point agents
  at it with `MS_HUB_HOST=<tailnet-ip> MS_HUB_PORT=7778`.

## Security model

Plaintext channels (incl. GUZMAN) are **monitor / untrusted, chat-only** — sender names are
spoofable, no commands honored. The **trusted** path is the authenticated **STS** channel
(MeshSpeakSTS) or a local/CAAP-signed operation. CAAP confidential capsules travel over
internet/file bindings, **not** the radio; the mesh carries only small signed/reference frames.

## Credits

Built on [MeshCore](https://github.com/meshcore-dev/MeshCore) (MIT) and its `meshcore` Python
companion lib. MeshSpeak, the CAAP integration, and the holographic overlays are Clavote
Research. Nothing cryptographic is hand-rolled — X25519/Ed25519/ChaCha20-Poly1305/HKDF via
`cryptography`.
