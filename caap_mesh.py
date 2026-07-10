#!/usr/bin/env python3
"""
caap_mesh.py — the CAAP <-> MeshCore bridge, extracted.

Copyright (c) 2026 Jose C. Guzman / Clavote Research. All Rights Reserved.

Moves a CAAP artifact (a Profile-AUTH message or a MILCAAP CRF-M frame) across the
LoRa mesh using the SAME path the live stack uses:

    CAAP bytes -> meshtalk.encode() fragments -> frame_to_wire (base64)
              -> mesh-hub TCP 127.0.0.1:7777 {action:send_channel,channelIdx,text}
              -> radio -> ... -> radio -> hub channel_message {raw|text}
              -> wire_to_frame -> meshtalk.decode()/reassemble -> CAAP bytes

The hub owns the serial radio (see meshhub.py / mesh-hub.js); this bridge is a hub
CLIENT, so it needs no radio access of its own and is safe to run anywhere on the box.

AUTH rides UNENCRYPTED (Part 97.113: base64 is framing, not obfuscation — legal on
amateur spectrum). CRF-M is a 109 B MAC frame that fits ONE MeshTalk fragment (the
MILCAAP <=2-packet property). Neither is encrypted at the mesh layer — confidentiality,
when required, is the CAAP capsule's job (Profile A/M) and stays off amateur bands.

A full CAAP (Profile A) capsule is ~13 KB / ~80+ fragments — too many to survive
fire-and-forget on a lossy half-duplex channel. `send-capsule`/`recv-capsule` add
selective-repeat ARQ over MeshTalk's ACK-bitmap primitives so the WHOLE capsule lands:
the sender bursts every fragment, the receiver replies with a bitmap of what it holds,
and the sender resends ONLY the gaps until the receiver ACKs complete. This is how CAAP
confidentiality stays a real capability on the MeshSpeak/MeshTalk mesh.

CLI:
  caap_mesh.py send         --channel IDX --src N --dst N --file BLOB   # best-effort TX
  caap_mesh.py send-capsule --channel IDX --src N --file CAPSULE        # reliable (ARQ)
  caap_mesh.py recv-capsule --channel IDX --out FILE [--me N]           # reassemble + ACK
  caap_mesh.py selftest                                                 # loopback, no radio
"""
import argparse
import json
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meshtalk as ms

HUB_HOST = os.environ.get("CAAP_MESH_HUB_HOST", "127.0.0.1")
HUB_PORT = int(os.environ.get("CAAP_MESH_HUB_PORT", "7777"))
TX_GAP_S = float(os.environ.get("CAAP_MESH_TX_GAP", "2.0"))   # airtime courtesy between frames


def to_wire_fragments(blob, src, dst, msg_id, key=None, session_salt=None):
    """CAAP bytes -> list of base64 channel-text wires. key=None => UNENCRYPTED
    (mandatory for Profile AUTH on amateur spectrum; also how a 109 B CRF-M frame ships
    since its authenticity is its own HMAC, not a mesh-layer cipher)."""
    frames = ms.encode(blob, src=src, dst=dst, msg_id=msg_id,
                       frag_payload_max=ms.WIRE_FRAG_PAYLOAD_MAX,
                       key=key, session_salt=session_salt)
    return [ms.frame_to_wire(f) for f in frames]


class HubClient:
    """Line-delimited-JSON client for the mesh hub (register / send_channel /
    channel_message), matching meshhub.py + the responder's protocol exactly."""
    def __init__(self, host=HUB_HOST, port=HUB_PORT, name="caap-mesh"):
        self.sock = socket.create_connection((host, port), timeout=8)
        self._buf = b""
        self._send({"action": "register", "name": name})

    def _send(self, obj):
        self.sock.sendall((json.dumps(obj) + "\n").encode())

    def send_channel(self, idx, wire):
        self._send({"action": "send_channel", "channelIdx": idx, "text": wire})

    def recv_json(self, timeout=6):
        self.sock.settimeout(timeout)
        while b"\n" not in self._buf:
            try:
                chunk = self.sock.recv(4096)
            except socket.timeout:
                return None
            if not chunk:
                return None
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return json.loads(line) if line.strip() else None

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def send_blob(blob, channel_idx, src, dst, msg_id=None, key=None, session_salt=None,
              host=HUB_HOST, port=HUB_PORT):
    """Fragment `blob` and transmit every wire on `channel_idx` via the hub. Returns the
    number of frames sent. Honors TX_GAP_S between frames (LoRa airtime courtesy)."""
    if msg_id is None:
        msg_id = int.from_bytes(os.urandom(2), "big")
    wires = to_wire_fragments(blob, src, dst, msg_id, key, session_salt)
    hub = HubClient(host, port)
    try:
        for i, w in enumerate(wires):
            hub.send_channel(channel_idx, w)
            if i + 1 < len(wires):
                time.sleep(TX_GAP_S)
    finally:
        hub.close()
    return len(wires)


# --------------------------------------------------------- reliable capsule ARQ
# A full CAAP (Profile A) capsule is ~13 KB / ~80+ MeshTalk fragments. Fire-and-forget on
# a lossy, half-duplex LoRa channel loses most of them — no retransmit means a single
# dropped fragment kills the whole reassembly. This wires MeshTalk's ACK-bitmap primitives
# (build_ack_bitmap / missing_fragments, ARQ_MAX_ROUNDS) into SELECTIVE-REPEAT ARQ so the
# WHOLE capsule arrives: the sender bursts every fragment, the receiver answers with a
# bitmap of what it holds, and the sender resends ONLY the gaps — round after round —
# until the receiver signals a complete bitmap. This is how CAAP-over-MeshSpeak stays a
# real capability (Profile A confidentiality on the mesh), not just a marker frame.
ACK_WAIT_S = float(os.environ.get("CAAP_MESH_ACK_WAIT", "5.0"))       # sender: ACK window/round
ACK_INTERVAL_S = float(os.environ.get("CAAP_MESH_ACK_INTERVAL", "1.5"))  # receiver: min ACK gap


def _read_frame(hub_msg):
    """Pull a MeshTalk frame out of a hub channel_message (raw wire first, text fallback)."""
    if hub_msg.get("type") != "channel_message":
        return None
    raw = hub_msg.get("raw") or ""
    frame = ms.wire_to_frame(raw)
    if frame is None:
        text = hub_msg.get("text", "") or ""
        for cand in [text] + text.split():
            frame = ms.wire_to_frame(cand)
            if frame is not None:
                break
    return frame


def _await_ack(hub, src, msg_id, timeout, settle=0.4):
    """Drain control frames; return the freshest ACK-bitmap ctrl for (src,msg_id). Returns
    early on a complete bitmap or after `settle` seconds with no fresher ACK; None if the
    window closes with no ACK at all (no receiver on the channel)."""
    end = time.time() + timeout
    latest, latest_ts = None, 0.0
    while time.time() < end:
        remaining = end - time.time()
        m = hub.recv_json(timeout=max(0.1, min(0.5, remaining)))
        now = time.time()
        if m is not None:
            frame = _read_frame(m)
            if frame is not None and len(frame) >= 8 and (frame[1] & ms.F_IS_CONTROL):
                ctrl = ms.parse_control(frame)
                if (ctrl["ctrl_type"] == ms.CTRL_ACK_BITMAP
                        and ctrl["src"] == src and ctrl["msg_id"] == msg_id):
                    latest, latest_ts = ctrl, now
                    if not ms.missing_fragments(ctrl):
                        return latest                    # receiver has everything
        if latest is not None and (now - latest_ts) >= settle:
            return latest
    return latest


def send_capsule(blob, channel_idx, src, dst=0xFF, msg_id=None, host=HUB_HOST,
                 port=HUB_PORT, gap=TX_GAP_S, max_rounds=ms.ARQ_MAX_ROUNDS,
                 ack_wait=ACK_WAIT_S, on_log=None):
    """Reliably ship `blob` (e.g. a full CAAP Profile-A capsule) over the mesh with
    selective-repeat ARQ. Returns {delivered, rounds, total, tx_frames}. `delivered` is
    True only once a receiver ACKs a complete bitmap; with no cooperating receiver it
    bursts each round and returns delivered=False (best-effort, like send_blob)."""
    if msg_id is None:
        msg_id = int.from_bytes(os.urandom(2), "big")
    wires = to_wire_fragments(blob, src, dst, msg_id)
    total = len(wires)
    log = on_log or (lambda *_: None)
    hub = HubClient(host, port, name="caap-capsule-tx")
    delivered, rounds_used, tx = False, 0, 0
    try:
        to_send = list(range(total))
        for rnd in range(max_rounds):
            rounds_used = rnd + 1
            log(f"round {rounds_used}: TX {len(to_send)}/{total} fragment(s)")
            for i in to_send:
                hub.send_channel(channel_idx, wires[i])
                tx += 1
                if gap:
                    time.sleep(gap)
            ack = _await_ack(hub, src, msg_id, ack_wait)
            if ack is None:
                log("  no receiver ACK — retrying gap set")
                continue                                 # resend same set next round
            missing = ms.missing_fragments(ack)
            if not missing:
                delivered = True
                log(f"  receiver ACKed COMPLETE after round {rounds_used}")
                break
            log(f"  receiver still missing {len(missing)}: {missing[:8]}"
                + ("…" if len(missing) > 8 else ""))
            to_send = missing
        return {"delivered": delivered, "rounds": rounds_used,
                "total": total, "tx_frames": tx}
    finally:
        hub.close()


class CapsuleReceiver:
    """RX side of reliable capsule ARQ. Feed each hub channel_message to feed(); it
    reassembles and emits ACK bitmaps back through `hub` so the sender can fill gaps.
    feed() returns the assembled blob (byte-exact) once complete, else None. Call tick()
    on idle so an ACK is flushed at end-of-burst (reflecting the whole round)."""
    def __init__(self, hub, me=0x10, default_ch=None, idle_flush=0.8):
        self.hub, self.me, self.default_ch, self.idle_flush = hub, me, default_ch, idle_flush
        self.store = ms.FragmentStore()
        self._pending = {}          # (src,msg_id) -> {total, ch, last_rx}
        self._done = set()

    def _ack(self, ch, msg_id, src, total, bitmap):
        wire = ms.frame_to_wire(ms.build_ack_bitmap(msg_id, src, self.me, total, bitmap))
        self.hub.send_channel(ch if ch is not None else (self.default_ch or 0), wire)

    def _full_bitmap(self, total):
        return bytes([0xFF]) * ((total + 7) // 8)

    def feed(self, hub_msg):
        frame = _read_frame(hub_msg)
        if frame is None or len(frame) < 6:
            return None
        flags = frame[1]
        if flags & ms.F_IS_CONTROL:                      # ignore ACKs — we are the receiver
            return None
        src = frame[4]
        msg_id = ms._u16r(frame[2:4])
        total = frame[7] if (flags & ms.F_FRAGMENTED) else 1
        ch = hub_msg.get("channelIdx", self.default_ch)
        k = (src, msg_id)
        if k in self._done:                              # late/dup frame — re-ACK complete
            self._ack(ch, msg_id, src, total, self._full_bitmap(total))
            return None
        kind, val = ms.decode(frame, self.store)
        if kind == "msg":
            self._done.add(k)
            self._pending.pop(k, None)
            self._ack(ch, msg_id, src, total, self._full_bitmap(total))
            return val
        if kind == "partial":
            self._pending[k] = {"total": total, "ch": ch, "last_rx": time.time()}
        return None

    def tick(self):
        """Flush an ACK bitmap for any transfer quiet for > idle_flush (end-of-burst)."""
        now = time.time()
        for k, st in list(self._pending.items()):
            if now - st["last_rx"] >= self.idle_flush:
                src, msg_id = k
                bm, tot = self.store.bitmap(k)
                self._ack(st["ch"], msg_id, src, tot, bm)
                st["last_rx"] = now                      # rate-limit until more arrives


def receive_capsule(channel_idx, me=0x10, host=HUB_HOST, port=HUB_PORT,
                    timeout=120.0, idle_flush=0.8, on_log=None):
    """Block until one full capsule on `channel_idx` is reassembled (ACKing as it goes), or
    `timeout` elapses. Returns the blob or None. Run this on the receiving node."""
    log = on_log or (lambda *_: None)
    hub = HubClient(host, port, name="caap-capsule-rx")
    rx = CapsuleReceiver(hub, me=me, default_ch=channel_idx, idle_flush=idle_flush)
    end = time.time() + timeout
    try:
        while time.time() < end:
            m = hub.recv_json(timeout=0.5)
            if m is None:
                rx.tick()
                continue
            if m.get("type") != "channel_message":
                continue
            if channel_idx is not None and m.get("channelIdx") not in (None, channel_idx):
                continue
            blob = rx.feed(m)
            if blob is not None:
                log(f"capsule COMPLETE: {len(blob)} B")
                return blob
        return None
    finally:
        hub.close()


class Reassembler:
    """Receiver helper: feed each hub channel_message; get back completed CAAP blobs
    addressed to `me`. Mirrors the responder's decode path (raw wire first, text
    fallback), with the same (src,msg_id) dedup."""
    def __init__(self, me, key=None, session_salt=None):
        self.me, self.key, self.salt = me, key, session_salt
        self.store = ms.FragmentStore()
        self._seen = []

    def feed(self, hub_msg):
        if hub_msg.get("type") != "channel_message":
            return None
        raw = hub_msg.get("raw") or ""
        frame = ms.wire_to_frame(raw)
        if frame is None:
            text = hub_msg.get("text", "") or ""
            for cand in [text] + text.split():
                frame = ms.wire_to_frame(cand)
                if frame is not None:
                    break
        if frame is None or len(frame) < 6:
            return None
        src, dst = frame[4], frame[5]
        mid = int.from_bytes(frame[2:4], "little")
        if dst != self.me:
            return None
        if (src, mid) in self._seen:
            return None
        kind, val = ms.decode(frame, self.store, key=self.key, session_salt=self.salt)
        if kind == "msg":
            self._seen.append((src, mid))
            return val
        return None


# --------------------------------------------------------------------------- CLI
def cmd_send(a):
    blob = open(a.file, "rb").read()
    n = send_blob(blob, a.channel, a.src, a.dst)
    print(f"sent {len(blob)} B as {n} MeshTalk frame(s) on channel {a.channel} "
          f"(src {a.src} -> dst {a.dst})")


def cmd_send_capsule(a):
    blob = open(a.file, "rb").read()
    res = send_capsule(blob, a.channel, a.src, dst=a.dst, gap=a.gap,
                       max_rounds=a.rounds, on_log=lambda s: print("  " + s))
    tag = "DELIVERED" if res["delivered"] else "BEST-EFFORT (no receiver ACK)"
    print(f"{tag}: {len(blob)} B, {res['total']} fragment(s), {res['tx_frames']} frame TX "
          f"over {res['rounds']} round(s) on channel {a.channel}")
    return 0 if res["delivered"] or a.dst == 0xFF else 1


def cmd_recv_capsule(a):
    blob = receive_capsule(a.channel, me=a.me, timeout=a.timeout,
                           on_log=lambda s: print("  " + s))
    if blob is None:
        print("timeout: no complete capsule received")
        return 1
    open(a.out, "wb").write(blob)
    print(f"received {len(blob)} B -> {a.out}")
    return 0


def _should_drop(wire, drop_once, dropped):
    """Simulate LoRa loss: drop each named DATA fragment index exactly once (its first
    transmission). Control frames (ACK bitmaps) and retransmissions always pass through."""
    frame = ms.wire_to_frame(wire)
    if frame is None or (frame[1] & ms.F_IS_CONTROL) or not (frame[1] & ms.F_FRAGMENTED):
        return False
    msg_id, idx = ms._u16r(frame[2:4]), frame[6]
    if idx in drop_once and (msg_id, idx) not in dropped:
        dropped.add((msg_id, idx))
        return True
    return False


def _loopback_hub(drop_once=None):
    """A stand-in mesh hub for the selftest: accepts MULTIPLE clients and BROADCASTS
    every send_channel to ALL of them as a channel_message carrying the wire in `raw`
    — exactly what the real hub does (a frame sent by one client returns to every
    registered client, incl. off the air). With `drop_once`, DATA fragments at those
    indices are dropped on first TX (loss injection for the ARQ test). Returns
    (port, captured-wires)."""
    dropped = set()
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    port = srv.getsockname()[1]
    captured = []
    clients = []
    clients_lock = threading.Lock()

    def broadcast(idx, wire):
        line = (json.dumps({"type": "channel_message", "channelIdx": idx,
                            "raw": wire, "text": ""}) + "\n").encode()
        with clients_lock:
            for c in list(clients):
                try:
                    c.sendall(line)
                except OSError:
                    pass

    def handle(conn):
        buf = b""
        conn.settimeout(5)
        while True:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                m = json.loads(line)
                if m.get("action") == "send_channel":
                    if drop_once and _should_drop(m["text"], drop_once, dropped):
                        continue                          # simulate loss: swallow this frame
                    captured.append(m["text"])
                    broadcast(m["channelIdx"], m["text"])
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
        conn.close()

    def accept_loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            with clients_lock:
                clients.append(conn)
            threading.Thread(target=handle, args=(conn,), daemon=True).start()
    threading.Thread(target=accept_loop, daemon=True).start()
    return port, captured


def cmd_selftest(a):
    fails = [0]

    def chk(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails[0] += 1

    # 1. protocol: register + send_channel + channel_message echo through a mock hub
    port, captured = _loopback_hub()
    time.sleep(0.2)
    global TX_GAP_S
    TX_GAP_S = 0.0                                   # no airtime wait in the test
    payload = b'{"alg":"AUTH","note":"ARES net check, station KJ6"}' * 12
    rx = Reassembler(me=10)
    hub = HubClient("127.0.0.1", port, name="selftest-rx")
    n = send_blob(payload, channel_idx=1, src=20, dst=10, msg_id=55,
                  host="127.0.0.1", port=port)
    chk("bridge fragments + sends over the hub protocol", n >= 1)
    got = None
    for _ in range(n + 2):
        m = hub.recv_json(timeout=3)
        if m is None:
            break
        out = rx.feed(m)
        if out is not None:
            got = out
    hub.close()
    chk("blob round-trips hub->wire->reassemble byte-exact", got == payload)

    # 2. a 109 B CRF-M-sized frame rides in ONE hub message
    port2, cap2 = _loopback_hub()
    time.sleep(0.2)
    crfm_like = os.urandom(109)
    rx2 = Reassembler(me=10)
    hub2 = HubClient("127.0.0.1", port2, name="selftest-crfm")
    n2 = send_blob(crfm_like, channel_idx=4, src=20, dst=10, msg_id=7,
                   host="127.0.0.1", port=port2)
    chk("109 B frame -> ONE hub send (<=2-packet property on the wire)", n2 == 1)
    got2 = None
    for _ in range(3):
        m = hub2.recv_json(timeout=3)
        if m is None:
            break
        got2 = rx2.feed(m) or got2
    hub2.close()
    chk("109 B frame recovered byte-exact via the bridge", got2 == crfm_like)

    # 3. AUTH stays UNENCRYPTED on the wire (no F_ENCRYPTED bit)
    wires = to_wire_fragments(payload, src=20, dst=10, msg_id=1)
    chk("AUTH wire is unencrypted (Part-97 legal)",
        all((ms.wire_to_frame(w)[1] & ms.F_ENCRYPTED) == 0 for w in wires))

    # 4. reliable capsule ARQ: a lossy MULTI-fragment transfer is recovered by selective
    #    repeat (the full CAAP Profile-A path — ~80 fragments in the field — shrunk here).
    drop = {1, 3, 4}
    port3, _ = _loopback_hub(drop_once=drop)
    time.sleep(0.2)
    capsule = os.urandom(1500)                          # ~13 fragments; stands in for a capsule
    result = {}

    def _rx():
        result["blob"] = receive_capsule(4, me=0x10, host="127.0.0.1", port=port3,
                                         timeout=15.0, idle_flush=0.3)
    rxt = threading.Thread(target=_rx, daemon=True)
    rxt.start()
    time.sleep(0.3)
    res = send_capsule(capsule, channel_idx=4, src=20, dst=0xFF, msg_id=99,
                       host="127.0.0.1", port=port3, gap=0.0, max_rounds=5, ack_wait=3.0)
    rxt.join(timeout=15)
    chk("ARQ: lossy multi-fragment capsule reassembles byte-exact", result.get("blob") == capsule)
    chk("ARQ: sender confirmed delivery via receiver ACK", res["delivered"])
    chk("ARQ: multi-fragment capsule (>=8 frames)", res["total"] >= 8)
    chk("ARQ: selective repeat resent only the gaps, not the whole capsule",
        res["tx_frames"] <= res["total"] + len(drop) + 2)

    print(f"\n{'ALL TESTS PASSED' if fails[0] == 0 else f'{fails[0]} FAILURE(S)'}")
    return 1 if fails[0] else 0


def main():
    ap = argparse.ArgumentParser(prog="caap_mesh")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send")
    s.add_argument("--channel", type=int, required=True)
    s.add_argument("--src", type=int, required=True)
    s.add_argument("--dst", type=int, required=True)
    s.add_argument("--file", required=True)
    s.set_defaults(fn=cmd_send)
    sc = sub.add_parser("send-capsule")               # reliable full-capsule TX (ARQ)
    sc.add_argument("--channel", type=int, required=True)
    sc.add_argument("--src", type=int, required=True)
    sc.add_argument("--dst", type=int, default=0xFF)
    sc.add_argument("--file", required=True)
    sc.add_argument("--rounds", type=int, default=ms.ARQ_MAX_ROUNDS)
    sc.add_argument("--gap", type=float, default=TX_GAP_S)
    sc.set_defaults(fn=cmd_send_capsule)
    rc = sub.add_parser("recv-capsule")               # reassemble + ACK a full capsule
    rc.add_argument("--channel", type=int, required=True)
    rc.add_argument("--me", type=int, default=0x10)
    rc.add_argument("--timeout", type=float, default=120.0)
    rc.add_argument("--out", required=True)
    rc.set_defaults(fn=cmd_recv_capsule)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
