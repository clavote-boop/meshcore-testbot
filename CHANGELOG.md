# Changelog

## 2026-07-11 — MeshSpeak family, CAAP-over-mesh, holographic overlays

The mesh stack grew from a plaintext chatbot into an interconnecting module family:
forward-secret agent comms, reliable capsule transport, a tailnet fallback, and a
holographic (any-subset-recoverable) image/data codec set.

### Added
- **meshcs** (`86befcf`) — compressed-sensing image overlay: sparse DCT coefficients captured
  as random linear measurements, recovered from **any** fragment subset via Orthogonal
  Matching Pursuit. Random single fragment → whole image (RMS 17 vs meshcanvas's 148).
- **meshphoto** (`25a7f2b`) — photo-optimized color codec: YCbCr, 2× chroma subsampling,
  perceptual frequency-weighted quantization. 48×48 color photo in ~2 fragments.
- **meshfountain** (`1b1b7ef`) — LT fountain erasure overlay: byte-exact recovery of text /
  memory / capsules from any sufficient droplet subset. Verified over the live hub (aired on
  #test, dropped 3 of 8 fragments, exact recovery).
- **meshrelay** (`3efa066`) + `meshrelay.service` — radio-less tailnet MeshSpeak relay on
  `:7778` for cross-node STS over IP while the LoRa link is down.
- **meshcanvas** (`704b316`) — DCT grayscale holographic image overlay (prefix-graceful).
- **meshspeak_agent STS mode** (`5d12f57`) — forward-secret, no-PSK agent sessions
  (`identity` / `send-sts` / `recv-sts`), 3-message Ed25519+X25519 handshake.
- **meshspeak_agent** (`27ecf08`) — agent-to-agent messaging: human text in, compressed +
  encrypted over the mesh, decoded by the peer holding the same key.
- **caap_mesh ARQ** (`6d6c561`) — reliable full-CAAP-capsule transfer via selective-repeat
  (`send-capsule` / `recv-capsule`) so a ~13 KB / ~80-fragment Profile-A capsule lands.

### Changed
- **MeshTalk → MeshSpeak** (`5aee4e0`) — canonical rename across module, imports, classes,
  chatbot file, and systemd unit; a deprecated `meshtalk.py` shim is kept for back-compat.
- **Hub local relay** (`5fa522c`) — relay every locally-sent frame to sibling clients (own-key
  agent-to-agent, hub need not hold the key); a send with no radio now logs + returns a
  `send_error` instead of silently dropping.
- **Chatbot channel-conditional persona** (`0a432ef`) — guarded on Public (ch0), cooperative
  with Clavote Heavyside + Bob on every other channel.
- **Chatbot voice** (`91a4ce7`) — rotate sign-offs, drop reflexive "73", less jargon.
- **Allowlist** (`73664bf`) — added `Clavote Heavyside` (the Ryzen Hermes edge node).

### Removed
- Stray `meshspeak_test_send.py` (`1c4ef74`) — an empty tracked file that had accumulated a
  pasted session summary in the working tree.
