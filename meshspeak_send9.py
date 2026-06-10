#!/usr/bin/env python3
# meshspeak_send9.py - send NINE encrypted MeshSpeak texts on #test (for security monitoring).
# Distinct msg_ids (correct nonce hygiene), fresh ephemeral session key, AEAD on every frame.
import sys, os, json, time, urllib.request
sys.path.insert(0, "/home/joe/meshcore-bots")
import meshspeak as ms

CHAN = int(os.environ.get("MSTEST_CHAN", "1"))   # 1 = #test
DASH = "http://127.0.0.1:3005/send"
KEY = os.urandom(32)
SALT = os.urandom(8)

def send(text):
    body = json.dumps({"channelIdx": CHAN, "text": text}).encode()
    req = urllib.request.Request(DASH, data=body, headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=8).status
    except Exception as e:
        return f"ERR:{e}"

MSGS = [
    b"agent room 1: status nominal",
    b"agent room 2: vault head ok",
    b"agent room 3: heartbeat seq up",
    b"agent room 4: radio link green",
    b"agent room 5: bots all healthy",
    b"agent room 6: no anomalies",
    b"agent room 7: mesh quiet",
    b"agent room 8: monitor active",
    b"agent room 9: end of burst",
]

print(f"sending 9 ENCRYPTED MeshSpeak texts on channel {CHAN} (#test)")
for i, m in enumerate(MSGS, 1):
    mid = 400 + i                                  # distinct msg_id per message
    frames = ms.encode(m, src=10, dst=0xFF, msg_id=mid,
                       frag_payload_max=ms.WIRE_FRAG_PAYLOAD_MAX,
                       key=KEY, session_salt=SALT)
    codes = []
    for f in frames:
        codes.append(str(send(ms.frame_to_wire(f))))
        time.sleep(2)
    enc = all((f[1] & ms.F_ENCRYPTED) for f in frames)
    print(f"  [{i}] msg_id={mid} frags={len(frames)} encrypted={enc} TX={codes}")
print("done: 9 encrypted texts transmitted.")
