#!/usr/bin/env python3
"""meshspeak_agent.py — agent-to-agent MeshSpeak messaging.

A human types text into one agent; that agent COMPRESSES + ENCRYPTS it over MeshSpeak
(ChaCha20-Poly1305) and puts it on the mesh; another agent holding the SAME session key
receives the opaque frame and DECODES it back to the human text. The session key is the
shared secret — a PSK file here, but any key works (e.g. drop in an STS-derived key from
MeshSpeakSTS for forward secrecy). On the wire the frame is opaque; only a keyholder reads it.

  keygen  --out FILE                         mint a 32B key + 8B salt session file (0600)
  send    "<text>" --key FILE --channel N [--src S --dst D]
  recv    --key FILE --channel N [--me D --timeout T]
  selftest
"""
import argparse
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import meshspeak as ms

HUB_HOST = os.environ.get("MS_HUB_HOST", "127.0.0.1")
HUB_PORT = int(os.environ.get("MS_HUB_PORT", "7777"))


def load_key(path):
    parts = open(os.path.expanduser(path)).read().split()
    if len(parts) < 2:
        sys.exit("key file must be: <64-hex key> <16-hex salt> [label]")
    key, salt = bytes.fromhex(parts[0]), bytes.fromhex(parts[1])
    if len(key) != 32 or len(salt) != 8:
        sys.exit("bad key file: need a 32-byte key (64 hex) and 8-byte salt (16 hex)")
    return key, salt


def _connect(name):
    s = socket.create_connection((HUB_HOST, HUB_PORT), timeout=8)
    s.sendall((json.dumps({"action": "register", "name": name}) + "\n").encode())
    time.sleep(0.3)
    return s


def _read_frame(m):
    frame = ms.wire_to_frame(m.get("raw") or "")
    if frame is None:
        for cand in (m.get("text", "") or "").split():
            frame = ms.wire_to_frame(cand)
            if frame is not None:
                break
    return frame


def cmd_keygen(a):
    p = os.path.expanduser(a.out)
    if os.path.exists(p):
        sys.exit(f"refuse: {p} already exists (zeroize/rotate deliberately)")
    key, salt = os.urandom(32), os.urandom(8)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, f"{key.hex()} {salt.hex()} meshspeak-agent\n".encode())
    os.close(fd)
    print(f"session key -> {p} (0600). Give it to the peer agent OUT OF BAND — never over the mesh.")


def cmd_send(a):
    key, salt = load_key(a.key)
    blob = a.text.encode("utf-8")
    frames = ms.encode(blob, src=a.src, dst=a.dst, msg_id=int.from_bytes(os.urandom(2), "big"),
                       frag_payload_max=ms.WIRE_FRAG_PAYLOAD_MAX, key=key, session_salt=salt)
    s = _connect("ms-agent-tx")
    try:
        for i, f in enumerate(frames):
            s.sendall((json.dumps({"action": "send_channel", "channelIdx": a.channel,
                                   "text": ms.frame_to_wire(f)}) + "\n").encode())
            if i + 1 < len(frames):
                time.sleep(2)
    finally:
        s.close()
    print(f"sent {len(blob)} B of human text as {len(frames)} encrypted MeshSpeak frame(s) "
          f"on ch{a.channel} (agent {a.src} -> {a.dst}); opaque on air")


def cmd_recv(a):
    key, salt = load_key(a.key)
    s = _connect("ms-agent-rx")
    s.settimeout(1.0)
    store = ms.FragmentStore()
    buf = b""
    end = time.time() + a.timeout
    print(f"listening on ch{a.channel} for MeshSpeak to agent {a.me} …", flush=True)
    try:
        while time.time() < end:
            try:
                data = s.recv(4096)
            except socket.timeout:
                continue
            if not data:
                break
            buf += data
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("type") != "channel_message":
                    continue
                if a.channel is not None and m.get("channelIdx") not in (None, a.channel):
                    continue
                frame = _read_frame(m)
                if frame is None or len(frame) < 6:
                    continue
                if a.me is not None and frame[5] != a.me:      # dst filter
                    continue
                kind, val = ms.decode(frame, store, key=key, session_salt=salt)
                if kind == "msg":
                    print(f"DECODED from agent {frame[4]}: {val.decode('utf-8', 'replace')}",
                          flush=True)
                    return 0
    finally:
        s.close()
    print("timeout: no decodable message")
    return 1


def cmd_selftest(a):
    fails = 0

    def chk(name, cond):
        nonlocal fails
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails += 1

    key, salt = os.urandom(32), os.urandom(8)
    human = "Net control, this is mobile 7 — eyes on the trailhead, all clear.".encode()
    frames = ms.encode(human, src=20, dst=21, msg_id=7,
                       frag_payload_max=ms.WIRE_FRAG_PAYLOAD_MAX, key=key, session_salt=salt)

    store, got = ms.FragmentStore(), None
    for f in frames:
        kind, val = ms.decode(f, store, key=key, session_salt=salt)
        if kind == "msg":
            got = val
    chk("human text decodes byte-exact with the shared key", got == human)

    wire = b"".join(frames)
    chk("plaintext NOT visible on the wire (compressed+encrypted)",
        b"trailhead" not in wire and b"control" not in wire)

    st2, wrong = ms.FragmentStore(), None
    for f in frames:
        kind, val = ms.decode(f, st2, key=os.urandom(32), session_salt=salt)
        if kind == "msg":
            wrong = val
    chk("a wrong key cannot decode (AEAD auth fails)", wrong != human)

    print("\nALL TESTS PASSED" if fails == 0 else f"\n{fails} FAILURE(S)")
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(prog="meshspeak_agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("keygen"); g.add_argument("--out", required=True); g.set_defaults(fn=cmd_keygen)
    s = sub.add_parser("send"); s.add_argument("text"); s.add_argument("--key", required=True)
    s.add_argument("--channel", type=int, required=True)
    s.add_argument("--src", type=int, default=20); s.add_argument("--dst", type=int, default=21)
    s.set_defaults(fn=cmd_send)
    r = sub.add_parser("recv"); r.add_argument("--key", required=True)
    r.add_argument("--channel", type=int, required=True)
    r.add_argument("--me", type=int, default=21); r.add_argument("--timeout", type=float, default=60.0)
    r.set_defaults(fn=cmd_recv)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    a = ap.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
