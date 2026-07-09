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

CLI:
  caap_mesh.py send  --channel IDX --src N --dst N --file BLOB   # fragment + TX via hub
  caap_mesh.py selftest                                         # loopback-hub, no radio
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


def _loopback_hub():
    """A stand-in mesh hub for the selftest: accepts MULTIPLE clients and BROADCASTS
    every send_channel to ALL of them as a channel_message carrying the wire in `raw`
    — exactly what the real hub does (a frame sent by one client returns to every
    registered client, incl. off the air). Returns (port, captured-wires)."""
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
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
