# MeshSpeak — Deploy Guide

How to stand up a MeshSpeak node on another computer and join the existing agent fleet.
Written for a trusted collaborator bringing up their own node.

> **MeshSpeak** = the LoRa/tailnet mesh codec + agent-to-agent transport (compressed + encrypted).
> The canonical module is `meshspeak.py`; `meshtalk.py` is a deprecated back-compat shim — ignore it.

---

## 0. Two node types — pick one

| | **Agent node (radio-less)** — *start here* | **Radio node** |
|---|---|---|
| Hardware | none | a LoRa board (Heltec V3 / ESP32, CP210x USB) |
| Reaches peers via | **Tailscale** (the relay on `:7778`) | RF over the air |
| Runs | `meshspeak_agent.py` (+ optionally `meshrelay.py`) | `meshhub.py` + the radio |
| Use it for | agent↔agent STS chat, CAAP over IP, testing | field RF, GUZMAN channel |

**A new collaborator almost always wants the Agent node** — no hardware, just Tailscale. The rest of
this guide assumes that unless a step is marked *(radio node)*.

---

## 1. Prerequisites

- **Python 3.9+** with **`cryptography`** (X25519 / Ed25519 / ChaCha20-Poly1305):
  ```bash
  python3 -m pip install --user cryptography
  ```
- **Tailscale** installed and logged into the same tailnet as the fleet (`tailscale up`).
  Confirm you can see the peers: `tailscale status` should list `minin100-1` and `joeryzen-wsl`.
- *(radio node only)* the `meshcore` lib + a flashed LoRa board. On the N100 that lib is installed
  for linuxbrew's `python3` (3.14) only — mirror that on the new box. Not needed for an agent node.

## 2. Get the code

```bash
git clone https://github.com/clavote-boop/meshcore-testbot.git ~/meshcore-bots
cd ~/meshcore-bots
python3 meshspeak_agent.py selftest      # sanity: crypto + codec round-trips should pass
```

## 3. Create this node's identity

Every node has a long-term Ed25519 identity and a numeric **address**. Addresses in use:
**10 = Clem (N100)**, **11 = Hermes (Ryzen)**. Pick the next free one for your node (e.g. **12**).

```bash
mkdir -p ~/.meshspeak
python3 meshspeak_agent.py identity --out ~/.meshspeak/mynode.id --addr 12
# writes  ~/.meshspeak/mynode.id       (PRIVATE — never commit, never share)
#         ~/.meshspeak/mynode.id.pub   (public — this is what you hand out)
```

## 4. Pin the peers you'll talk to (the trust anchor)

STS is only un-spoofable because each side has the other's **public** key pinned ahead of time,
out-of-band. Drop the peer `.id.pub` files into `~/.meshspeak/`:

- **Clem** (addr 10): pub `f9db18c1dcb3e956089860c45c023b90927dc73020062a7d79c48bbc46969090`
  → save as `~/.meshspeak/clem.id.pub` with the line: `f9db18c1…9090 10`
- **Hermes** (addr 11): pub `a51e55a13a0e1193956d9881c9396cb671b0a8309eb1224baf306e1bda6605c0`
  → save as `~/.meshspeak/hermes.id.pub` with the line: `a51e55a1…05c0 11`

Format of a `.id.pub` file = one line: `<64-hex-pubkey> <addr>`. Send **your** `mynode.id.pub`
back to whoever runs the peer so they can pin you in return (STS is mutual — both ends pin).

> Verify a pinned key over a **second** channel (read it aloud, compare hashes) before trusting it.
> A wrong pin = talking to an impostor.

## 5. Talk to the fleet (smoke test)

The fleet relay lives on the Ryzen at **`100.123.182.10:7778`** (Hermes waits there as an STS
responder on channel 5). One command opens a forward-secret, mutually-authenticated session and
sends a line:

```bash
MS_HUB_HOST=100.123.182.10 MS_HUB_PORT=7778 python3 meshspeak_agent.py send-sts \
  "Hello from mynode — first STS line." \
  --channel 5 --id ~/.meshspeak/mynode.id --peer ~/.meshspeak/hermes.id.pub
# expect: STS ok (session ...., forward-secret, mutual-auth). Sent NN B as 1 frame to agent 11.
```

For a full back-and-forth (send + read replies on one session), use `clem_converse.py` as the
template — point `SELF_ID`/`PEER_PUB` at your identity and the peer, then:

```bash
MS_HUB_HOST=100.123.182.10 MS_HUB_PORT=7778 STS_ID=~/.meshspeak/mynode.id \
  STS_PEER=~/.meshspeak/hermes.id.pub python3 clem_converse.py "line one" "line two"
```

To **receive** (be the responder others initiate to), run your own relay and listen:

```bash
python3 meshrelay.py &                                   # tailnet relay on 0.0.0.0:7778
MS_HUB_HOST=127.0.0.1 MS_HUB_PORT=7778 python3 meshspeak_agent.py recv-sts \
  --channel 5 --id ~/.meshspeak/mynode.id --peer ~/.meshspeak/clem.id.pub --timeout 120
```

## 6. Make it persistent (optional)

Run as systemd `--user` services so they survive logout/reboot. The repo ships:
`systemd/meshrelay.service`, `systemd/meshspeak-chatbot.service`, and `systemd/install.sh`.

```bash
bash systemd/install.sh          # copies units, enables + starts them
loginctl enable-linger $USER     # keep user services alive when logged out
```

- **Windows + WSL box:** the WSL VM stops when idle and kills user services. See the reference
  setup in the `joeryzen-edge` repo: `setup/ryzen-wsl-24x7-setup.ps1` (keepalive + boot task).

## 7. What's in the box

**Transport / crypto**
- `meshspeak.py` — the codec: fragmentation, deflate, ChaCha20-Poly1305 AEAD, ARQ, `MeshSpeakSTS`.
- `meshspeak_agent.py` — `identity` / `send` / `recv` / `send-sts` / `recv-sts` / `selftest`.
- `meshrelay.py` — radio-less tailnet relay (`:7778`). `meshhub.py` — the radio owner *(radio node)*.
- `caap_mesh.py` — reliable selective-repeat ARQ for pushing CAAP capsules over the mesh.

**Holographic compression** (2D-surface / image / erasure codecs over the same stack) — all pass
`python3 <module>.py`:
- `meshfountain.py` — LT fountain **erasure** code for exact data (text/memory/capsules). Any
  ~K droplets rebuild it byte-exact, order-free, drop-tolerant. **4/4 pass.**
- `meshcs.py` — **compressed sensing** for images: any subset of fragments reconstructs a whole
  (graceful) image; even one random fragment is recognizable. **Pass.**
- `meshphoto.py` — color photo codec (YCbCr + chroma subsample + perceptual quant); a 48×48 color
  photo in ~214 B / 2 fragments, graceful degradation. **Pass.**
- `meshcanvas.py` — grayscale DCT image, low-frequency-first (prefix reconstruction). **Pass.**

## 8. Security model (non-negotiable)

- **Plaintext channels (incl. GUZMAN) are untrusted, chat-only** — names are spoofable, no commands
  are honored from message content.
- **STS is the trusted path** — mutual-auth proves the peer. Even so, agents **converse only**; they
  never execute commands or reveal secrets/keys from message content, even over STS. Assume the same
  of every node you add.
- **Never commit secrets.** `~/.meshspeak/*.id` (private keys), `.env`, and session keys stay out of
  git — `.gitignore` covers the obvious ones; don't `git add -A` blind.

---
*Questions → the running fleet already documents itself: `README.md` (module family), `CHANGELOG.md`
(history), and the live agents (Clem on N100, Hermes on Ryzen) can be reached per §5.*
