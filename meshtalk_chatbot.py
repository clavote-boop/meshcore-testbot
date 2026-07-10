#!/usr/bin/env python3
"""
meshtalk_chatbot.py — plaintext LLM channel bot for the MeshCore mesh.

Copyright (c) 2026 Jose C. Guzman / Clavote Research. All Rights Reserved.

Lets Joe converse with the Clem LLM over the mesh in PLAIN TEXT (no session key, from
any device). Connects to the mesh-hub (TCP 7777), watches one or more channels, and when
a message is ADDRESSED to Clem ("Clem ..." / "Clem Heavyside ..." / "@clem ...") FROM an
allowed sender, it asks the OpenClaw gateway and replies in plaintext on the same
channel.

Separate from meshspeak_responder.py (the encrypted Clem-to-Clem frame path) — this does
NOT touch that; both run side by side.

Guardrails (essential on the OPEN Public channel): a SENDER ALLOWLIST (default: only
"Bob Heavyside") so Clem never engages strangers or other bots; trigger-gated so it stays
silent otherwise; ignores its own / any "clem"-named sender; ignores MeshTalk/HF base64
frames; dedupes recent (sender,text); per-sender cooldown.

Env:
  MESH_BOT_CHANNELS  comma-separated channel indices (default "0,4" = Public, GUZMAN)
  MESH_BOT_TRIGGERS  comma-separated address phrases, case-insensitive
                     (default "clem heavyside,clem")
  MESH_BOT_ALLOW     comma-separated sender names allowed to converse
                     (default "Bob Heavyside"; empty string = allow anyone)
  MESH_BOT_COOLDOWN  seconds between replies to one sender (default 8)
"""
import base64
import json
import os
import shlex
import socket
import subprocess
import time

HUB_HOST, HUB_PORT = "127.0.0.1", 7777
OPENCLAW = "/home/joe/.npm-global/bin/openclaw"
_ch_raw = os.environ.get("MESH_BOT_CHANNELS", "all").strip().lower()
# "all" or empty => watch EVERY channel (CHANNELS = None means no channel filter)
CHANNELS = None if _ch_raw in ("", "all", "*") else {int(x) for x in _ch_raw.split(",") if x.strip()}
TRIGGERS = [t.strip().lower() for t in os.environ.get("MESH_BOT_TRIGGERS", "clem heavyside,clem").split(",") if t.strip()]
TRIGGERS.sort(key=len, reverse=True)                           # longest phrase first
_allow_raw = os.environ.get("MESH_BOT_ALLOW", "Bob Heavyside")
ALLOW_SENDERS = {s.strip().lower() for s in _allow_raw.split(",") if s.strip()}  # empty => anyone
COOLDOWN = float(os.environ.get("MESH_BOT_COOLDOWN", "8"))
CHAN_TEXT_MAX = 160
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
    """MeshCore prefixes channel text with the sender name: 'Bob Heavyside: message'."""
    t = text.strip()
    if sender and t.lower().startswith(sender.lower()):
        t = t[len(sender):].lstrip()
    if t[:1] == ":":
        t = t[1:].lstrip()
    return t


def looks_like_frame(text):
    """A base64 MeshTalk/HF wire fragment, not human chat — leave to other layers."""
    t = text.strip()
    if len(t) < 8:
        return False
    try:
        raw = base64.b64decode(t, validate=True)
    except Exception:
        return False
    return bool(raw) and raw[0] in (0xA1, 0xF8)


def is_addressed(text):
    """Bob addresses Clem naturally — 'Clem ...', 'Hey Clem', 'Clem Heavyside, ...' — so
    match the trigger ANYWHERE in the message, not just at the start."""
    low = text.lower()
    return any(trig in low for trig in TRIGGERS)


def extract_query(text):
    """The query passed to the LLM. If the message starts with a trigger phrase, strip
    it ('Clem, what time?' -> 'what time?'); otherwise pass the whole message (the LLM IS
    Clem and understands 'Hey Clem ...'). Bare 'Clem' -> the full text so Clem greets."""
    low = text.lower()
    for trig in TRIGGERS:                       # longest phrase first
        if low.startswith(trig) or low.startswith("@" + trig):
            q = text[len(trig) + (1 if low.startswith("@" + trig) else 0):]
            q = q.lstrip(" ,:?!.").strip()
            return q if q else text.strip()
    return text.strip()


def decide(msg):
    """PURE decision: given a hub channel_message, return (channel, sender, query) if the
    bot should reply, else None. Applies channel filter, frame filter, sender allowlist,
    the 'clem'-named self/loop guard, and the address trigger. No side effects."""
    if msg.get("type") != "channel_message":
        return None
    ch = msg.get("channelIdx")
    if CHANNELS is not None and ch not in CHANNELS:   # CHANNELS None => watch all
        return None
    sender = (msg.get("senderName") or msg.get("sender") or "").strip()
    raw_text = msg.get("text", "") or ""
    if not raw_text or looks_like_frame(raw_text):
        return None
    # loop guard: never answer ourselves or any clem-named station
    if "clem" in sender.lower():
        return None
    # sender allowlist (Public safety: only converse with allowed operators)
    if ALLOW_SENDERS and sender.lower() not in ALLOW_SENDERS:
        return None
    text = strip_sender_prefix(raw_text, sender)
    if not is_addressed(text):
        return None
    query = extract_query(text)
    if not query:
        return None
    return ch, sender, query


def llm_reply(sender, query):
    prompt = (f'A MeshCore radio operator ("{sender}") asks Clem over the mesh: "{query}". '
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
    log(f"channels={'ALL' if CHANNELS is None else sorted(CHANNELS)} triggers={TRIGGERS} "
        f"allow={sorted(ALLOW_SENDERS) or 'ANYONE'}")
    seen, last_reply = [], {}
    while True:
        try:
            s = socket.create_connection((HUB_HOST, HUB_PORT), timeout=8)
            s.sendall((json.dumps({"action": "register", "name": "clem-chatbot"}) + "\n").encode())
            log("connected to hub")
            s.settimeout(None)
            buf = b""
            while True:
                got = s.recv(4096)
                if not got:
                    raise ConnectionError("hub closed")
                buf += got
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if not line.strip():
                        continue
                    try:
                        msg = json.loads(line)
                    except Exception:
                        continue
                    d = decide(msg)
                    if d is None:
                        continue
                    ch, sender, query = d
                    key = (sender, query)
                    if key in seen:
                        continue
                    seen.append(key); del seen[:-200]
                    now = time.time()
                    if now - last_reply.get(sender, 0) < COOLDOWN:
                        log(f"cooldown: skip rapid msg from {sender}")
                        continue
                    last_reply[sender] = now
                    log(f"<- [{sender} ch{ch}] {query}")
                    conv(f"{sender} (ch{ch}) -> Clem: {query}")
                    reply = llm_reply(sender, query)
                    log(f"-> {reply}")
                    conv(f"Clem -> {sender} (ch{ch}): {reply}")
                    for part in chunk(reply):
                        s.sendall((json.dumps({"action": "send_channel",
                                               "channelIdx": ch, "text": part}) + "\n").encode())
                        time.sleep(2)
        except Exception as e:
            log(f"hub connection error: {e}; retry 5s")
            time.sleep(5)


if __name__ == "__main__":
    main()
