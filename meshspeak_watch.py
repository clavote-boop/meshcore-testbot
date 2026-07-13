#!/usr/bin/env python3
# meshspeak_watch.py - keyed live view of the MeshSpeak conversation. Holds the session key,
# connects to the hub, decodes EVERY MeshSpeak frame (both directions), prints the readable exchange.
# Auto-reconnects so it survives hub restarts.
import sys, json, socket, time
sys.path.insert(0, "/home/joe/meshcore-bots")
import meshspeak as ms
from binascii import unhexlify

k, s, _ = open("/home/joe/clem_demo_session.txt").read().split()
KEY, SALT = unhexlify(k), unhexlify(s)
NAMES = {20: "Bob", 10: "Clem"}
store = ms.FragmentStore()
print("== watching MeshSpeak #test conversation (decoded with the session key) — Ctrl-C to stop ==", flush=True)

while True:
    try:
        sock = socket.create_connection(("127.0.0.1", 7777))
        sock.sendall((json.dumps({"action": "register", "name": "ms-watch"}) + "\n").encode())
        buf = ""
        while True:
            data = sock.recv(4096)
            if not data:
                break
            buf += data.decode("utf-8", "replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                if m.get("type") != "channel_message":
                    continue
                fr = ms.wire_to_frame(m.get("raw") or m.get("text", ""))
                if fr is None:
                    continue
                kind, val = ms.decode(fr, store, key=KEY, session_salt=SALT)
                if kind == "msg":
                    who = NAMES.get(fr[4], f"agent{fr[4]}")
                    print(f"  {who}: {val.decode('utf-8', 'replace')}", flush=True)
    except Exception:
        time.sleep(3)   # hub down / restarting -> reconnect
