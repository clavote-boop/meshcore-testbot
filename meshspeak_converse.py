#!/usr/bin/env python3
# meshspeak_converse.py - tiny helper for the Bob<->Clem MeshSpeak demo (encrypted).
# Session (demo key, salt, Bob's wire) is in ~/clem_demo_session.txt.
#   decode             -> print Bob's message
#   reply "your text"  -> print CLEM_WIRE (Clem->Bob, encrypted, direction flips -> nonce-safe)
import sys
sys.path.insert(0, "/home/joe/meshcore-bots")
import meshspeak as ms
from binascii import unhexlify

SESS = "/home/joe/clem_demo_session.txt"
k_hex, s_hex, bob_wire = open(SESS).read().split()
KEY, SALT = unhexlify(k_hex), unhexlify(s_hex)
BOB, CLEM = 20, 10

cmd = sys.argv[1] if len(sys.argv) > 1 else "decode"
if cmd == "decode":
    fr = ms.wire_to_frame(bob_wire)
    st = ms.FragmentStore()
    kind, val = ms.decode(fr, st, key=KEY, session_salt=SALT)
    print("Bob said:", val.decode() if isinstance(val, (bytes, bytearray)) else val)
elif cmd == "reply":
    reply = sys.argv[2].encode()
    fr = ms.encode(reply, src=CLEM, dst=BOB, msg_id=1,
                   frag_payload_max=ms.WIRE_FRAG_PAYLOAD_MAX, key=KEY, session_salt=SALT)
    print("CLEM_WIRE=" + ms.frame_to_wire(fr[0]))
