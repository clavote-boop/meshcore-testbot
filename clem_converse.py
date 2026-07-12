#!/usr/bin/env python3
"""clem_converse.py — N100/Clem side of the authenticated Hermes<->Clem STS conversation.

Mirrors JOERYZEN's bridge/hermes_sts_responder.py `test-initiate`: Clem is the INITIATOR.
Connect to the relay, sts_initiate to Hermes (addr 11) on ch5, then send line(s) and READ
Hermes' reply on the same forward-secret session. Multi-turn: sends each arg line in order,
printing Hermes' reply after each. Run with:

  MS_HUB_HOST=100.123.182.10 MS_HUB_PORT=7778 python3 clem_converse.py "line one" "line two"
"""
import os, sys, time, socket, json

os.environ.setdefault("MS_HUB_HOST", "100.123.182.10")
os.environ.setdefault("MS_HUB_PORT", "7778")
sys.path.insert(0, os.path.expanduser("~/meshcore-bots"))
import meshspeak as ms
import meshspeak_agent as A

CHANNEL  = int(os.environ.get("STS_CHANNEL", "5"))
SELF_ID  = os.environ.get("STS_ID",   os.path.expanduser("~/.meshspeak/n100.id"))
PEER_PUB = os.environ.get("STS_PEER", os.path.expanduser("~/.meshspeak/joeryzen.id.pub"))
REPLY_WINDOW = float(os.environ.get("STS_REPLY_WINDOW", "45"))


def send_msg(sock, sess, my_addr, peer_addr, text):
    frames = ms.encode(text.encode("utf-8"), src=my_addr, dst=peer_addr,
                       msg_id=int.from_bytes(os.urandom(2), "big"),
                       frag_payload_max=ms.WIRE_FRAG_PAYLOAD_MAX,
                       key=sess["key"], session_salt=sess["salt"])
    for i, f in enumerate(frames):
        A._send_wire(sock, CHANNEL, f)
        if i + 1 < len(frames):
            time.sleep(1)


def read_reply(sock, sess, my_addr, store, idle):
    buf = b""
    sock.settimeout(1.0)
    end = time.time() + idle
    while time.time() < end:
        try:
            data = sock.recv(4096)
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
            if m.get("channelIdx") not in (None, CHANNEL):
                continue
            frame = A._read_frame(m)
            if frame is None or len(frame) < 6 or (frame[1] & ms.F_IS_CONTROL):
                continue
            if frame[5] != my_addr:
                continue
            kind, val = ms.decode(frame, store, key=sess["key"], session_salt=sess["salt"])
            if kind == "msg":
                return val.decode("utf-8", "replace")
    return None


def main():
    lines = sys.argv[1:] or ["Hermes, Clem here on the N100 — first real turn over STS. How do you read?"]
    ident, peer = A.load_identity(SELF_ID), A.load_peer(PEER_PUB)
    my_addr, peer_addr = ident[2], peer[1]
    print(f"relay {os.environ['MS_HUB_HOST']}:{os.environ['MS_HUB_PORT']} ch{CHANNEL} "
          f"self={my_addr} peer(Hermes)={peer_addr}")
    s = A._connect("clem-sts-converse")
    sess = A.sts_initiate(s, CHANNEL, ident, peer, timeout=25)
    print(f"STS up: session {sess['session_id'].hex()[:8]}… forward-secret, mutual-auth")
    store = ms.FragmentStore()
    for n, text in enumerate(lines, 1):
        print(f"\nClem  -> {text}")
        send_msg(s, sess, my_addr, peer_addr, text)
        reply = read_reply(s, sess, my_addr, store, REPLY_WINDOW)
        if reply is None:
            print(f"Hermes-> (no reply within {REPLY_WINDOW:.0f}s)")
            break
        print(f"Hermes-> {reply}")
    s.close()
    print("\nsession closed")


if __name__ == "__main__":
    main()
