#!/usr/bin/env python3
# meshspeak_responder.py - Clem's live MeshSpeak responder.
# Listens on the hub (TCP 7777). For each MeshSpeak frame addressed to Clem (dst=10) it can
# decrypt with the session key, it asks Clem (OpenClaw gateway) to fulfill the request (it may run
# searches/commands), encrypts the reply (Clem->sender, direction flips -> nonce-safe), auto-
# fragments long replies into multiple channel chunks, and writes a readable conversation log.
import sys, os, json, socket, time, subprocess, threading, shlex
sys.path.insert(0, "/home/joe/meshcore-bots")
import meshspeak as ms
from binascii import unhexlify

HUB_HOST, HUB_PORT = "127.0.0.1", 7777
SESS = "/home/joe/clem_demo_session.txt"
CLEM, NAME = 10, "clem-ms"
OPENCLAW = "/home/joe/.npm-global/bin/openclaw"

k_hex, s_hex, _ = open(SESS).read().split()
KEY, SALT = unhexlify(k_hex), unhexlify(s_hex)

seen = []
send_lock = threading.Lock()
out_mid = [100]


def log(m): print(f"[ms-responder {time.strftime('%H:%M:%S')}] {m}", flush=True)


CONV_LOG = "/home/joe/meshcore-bots/ms_conversation.log"
def conv(line):
    try:
        with open(CONV_LOG, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + line + "\n")
    except Exception:
        pass


def clem_reply(src_agent, text):
    prompt = (f'Incoming MeshSpeak request from agent {src_agent}: "{text}". '
              f'Fulfill it now -- you may run shell commands or search the system to answer. '
              f'Reply with ONLY the result as plain text (no markdown, no code fences, no preamble), '
              f'under 480 characters.')
    try:
        cmd = f"{OPENCLAW} agent --agent main --message {shlex.quote(prompt)}"
        r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=150)
        body = r.stdout.replace("```", "").strip()
        if body:
            return body[:480]
    except Exception as e:
        log(f"gateway error: {e}")
    return f"Copy agent {src_agent}, search failed. -Clem"


def send_channel(sock, idx, wire):
    with send_lock:
        sock.sendall((json.dumps({"action": "send_channel", "channelIdx": idx, "text": wire}) + "\n").encode())


def handle(sock, msg):
    if msg.get("type") != "channel_message":
        return
    idx = msg.get("channelIdx", -1)
    text = msg.get("text", "") or ""
    raw = msg.get("raw") or ""
    frame = ms.wire_to_frame(raw)                 # hub provides the decoded wire for MeshSpeak frames
    if frame is None:
        for cand in [text] + text.split():        # fallback for unencrypted frames carried in text
            frame = ms.wire_to_frame(cand)
            if frame is not None:
                break
    if frame is None or len(frame) < 6:
        return
    src, dst = frame[4], frame[5]
    mid = int.from_bytes(frame[2:4], "little")
    if dst != CLEM or src == CLEM:                 # only frames TO Clem, never our own
        return
    if (src, mid) in seen:
        return
    seen.append((src, mid))
    del seen[:-500]
    st = ms.FragmentStore()
    kind, val = ms.decode(frame, st, key=KEY, session_salt=SALT)
    if kind != "msg":
        log(f"frame from agent {src} not a decodable msg ({kind})")
        return
    incoming = val.decode("utf-8", "replace")
    log(f"<- agent {src}: {incoming}")
    conv(f"agent{src} -> Clem: {incoming}")
    reply = clem_reply(src, incoming)
    log(f"-> reply: {reply}")
    conv(f"Clem -> agent{src}: {reply}")
    out_mid[0] += 1
    for f in ms.encode(reply.encode(), src=CLEM, dst=src, msg_id=out_mid[0],
                       frag_payload_max=ms.WIRE_FRAG_PAYLOAD_MAX, key=KEY, session_salt=SALT):
        send_channel(sock, idx, ms.frame_to_wire(f))
        time.sleep(2)
    log(f"reply sent on ch{idx}")


def main():
    while True:
        try:
            sock = socket.create_connection((HUB_HOST, HUB_PORT))
            sock.sendall((json.dumps({"action": "register", "name": NAME}) + "\n").encode())
            log("connected to hub; listening for MeshSpeak addressed to Clem (agent 10)")
            buf = ""
            while True:
                data = sock.recv(4096)
                if not data:
                    break
                buf += data.decode("utf-8", "replace")
                while "\n" in buf:
                    line, buf = buf.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        m = json.loads(line)
                    except Exception:
                        continue
                    threading.Thread(target=handle, args=(sock, m), daemon=True).start()
        except Exception as e:
            log(f"hub connection error: {e}; retry 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
