#!/usr/bin/env python3
"""
meshtalk_chatbot.py — plaintext LLM channel bot for the MeshCore mesh.

Copyright (c) 2026 Jose C. Guzman / Clavote Research. All Rights Reserved.

Lets Joe converse with the Clem LLM over the mesh in PLAIN TEXT (no session key, from
any device). Connects to the mesh-hub (TCP 7777), watches ONE channel, and when a
message is ADDRESSED to Clem (starts with the trigger word, default "clem") it asks the
OpenClaw gateway and replies in plaintext on the same channel.

Separate from meshspeak_responder.py (the encrypted Clem-to-Clem frame path) — this does
NOT touch that. Trigger-gated so it stays quiet during normal channel use (essential on
GUZMAN, the family emergency channel): it only speaks when spoken to.

Loop guards: ignores its own / any "clem"-named sender, ignores MeshTalk/HF base64
frames, dedupes recent (sender,text), and rate-limits per sender.

Env:
  MESH_BOT_CHANNEL   channel index to watch (default 4 = GUZMAN)
  MESH_BOT_TRIGGER   address word, case-insensitive (default "clem")
  MESH_BOT_COOLDOWN  seconds between replies to one sender (default 8)
"""
import json
import os
import shlex
import socket
import subprocess
import time

HUB_HOST, HUB_PORT = "127.0.0.1", 7777
OPENCLAW = "/home/joe/.npm-global/bin/openclaw"
CHANNEL = int(os.environ.get("MESH_BOT_CHANNEL", "4"))          # GUZMAN
TRIGGER = os.environ.get("MESH_BOT_TRIGGER", "clem").lower()
COOLDOWN = float(os.environ.get("MESH_BOT_COOLDOWN", "8"))
CHAN_TEXT_MAX = 160                                             # keep under the ~177 char limit
REPLY_MAX_CHARS = 460
MAX_CHUNKS = 3
CONV_LOG = "/home/joe/meshcore-bots/chatbot_conversation.log"


def log(m):
    print(f"[chatbot {time.strftime('%H:%M:%S')}] {m}", flush=True)


def conv(line):
    try:
        with open(CONV_LOG, "a") as f:
            f.write(time.strftime("%H:%M:%S ") + line + "\n")
    except Exception:
        pass


def strip_sender_prefix(text, sender):
    """MeshCore prefixes channel text with the sender name: 'Bob: message'. Strip a
    leading '<sender>:' (or any leading 'Name:') so the trigger match sees the body."""
    t = text.strip()
    if sender and t.lower().startswith(sender.lower()):
        t = t[len(sender):].lstrip()
    if t[:1] == ":":
        t = t[1:].lstrip()
    # generic 'Word...: ' leading label (defensive)
    if ":" in t[:24]:
        head, _, rest = t.partition(":")
        if head and " " not in head.strip() and rest.strip():
            # only strip if it looks like a name label, not part of the message
            if head.strip().lower() in (sender or "").lower() or len(head.strip()) <= 16:
                t = rest.strip()
    return t


def looks_like_frame(text):
    """A base64 MeshTalk/HF wire fragment, not human chat — leave those to the encrypted
    responder / binding layers."""
    t = text.strip()
    if len(t) < 8:
        return False
    import base64
    try:
        raw = base64.b64decode(t, validate=True)
    except Exception:
        return False
    return bool(raw) and raw[0] in (0xA1, 0xF8)          # MeshTalk MAGIC / HF_MAGIC


def is_addressed(text):
    low = text.lower()
    return low.startswith(TRIGGER) or low.startswith("@" + TRIGGER) or low.startswith(TRIGGER + ",")


def strip_trigger(text):
    low = text.lower()
    for pre in ("@" + TRIGGER, TRIGGER):
        if low.startswith(pre):
            return text[len(pre):].lstrip(" ,:").strip()
    return text.strip()


def llm_reply(sender, query):
    prompt = (f'A MeshCore radio operator ("{sender}") asks over the mesh: "{query}". '
              f'Answer helpfully and concisely as Clem. Reply with ONLY plain text '
              f'(no markdown, no code fences, no preamble), under {REPLY_MAX_CHARS} '
              f'characters — it goes out over a slow LoRa channel.')
    try:
        cmd = f"{OPENCLAW} agent --agent main --message {shlex.quote(prompt)}"
        r = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=150)
        body = r.stdout.replace("```", "").strip()
        if body:
            return body[:REPLY_MAX_CHARS]
    except Exception as e:
        log(f"gateway error: {e}")
    return f"Copy {sender}, I couldn't reach my backend just now. -Clem"


def chunk(text):
    out, t = [], text.strip()
    while t and len(out) < MAX_CHUNKS:
        out.append(t[:CHAN_TEXT_MAX])
        t = t[CHAN_TEXT_MAX:]
    return out


def main():
    seen = []            # recent (sender, text) for dedupe
    last_reply = {}      # sender -> ts (cooldown)
    while True:
        try:
            s = socket.create_connection((HUB_HOST, HUB_PORT), timeout=8)
            s.sendall((json.dumps({"action": "register", "name": "clem-chatbot"}) + "\n").encode())
            log(f"connected; watching ch{CHANNEL} for messages addressed '{TRIGGER}'")
            s.settimeout(None)
            buf = b""
            while True:
                chunk_in = s.recv(4096)
                if not chunk_in:
                    raise ConnectionError("hub closed")
                buf += chunk_in
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    if msg.get("type") != "channel_message":
                        continue
                    if msg.get("channelIdx") != CHANNEL:
                        continue
                    sender = (msg.get("senderName") or msg.get("sender") or "").strip()
                    raw_text = msg.get("text", "") or ""
                    if not raw_text or looks_like_frame(raw_text):
                        continue
                    # loop guard: never answer ourselves or any clem-named station
                    if TRIGGER in sender.lower():
                        continue
                    text = strip_sender_prefix(raw_text, sender)
                    if not is_addressed(text):
                        continue
                    key = (sender, text)
                    if key in seen:
                        continue
                    seen.append(key); del seen[:-200]
                    now = time.time()
                    if now - last_reply.get(sender, 0) < COOLDOWN:
                        log(f"cooldown: ignoring rapid message from {sender}")
                        continue
                    last_reply[sender] = now
                    query = strip_trigger(text)
                    if not query:
                        continue
                    log(f"<- {sender}: {query}")
                    conv(f"{sender} -> Clem: {query}")
                    reply = llm_reply(sender, query)
                    log(f"-> reply: {reply}")
                    conv(f"Clem -> {sender}: {reply}")
                    for part in chunk(reply):
                        s.sendall((json.dumps({"action": "send_channel",
                                               "channelIdx": CHANNEL, "text": part}) + "\n").encode())
                        time.sleep(2)                    # LoRa airtime courtesy
        except Exception as e:
            log(f"hub connection error: {e}; retry 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
