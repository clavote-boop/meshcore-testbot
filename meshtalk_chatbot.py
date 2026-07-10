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
import socket
import time

HUB_HOST, HUB_PORT = "127.0.0.1", 7777
# SPEED (learned from Janet — she is fast + terse): local LLM on this CPU-only box is
# 50-100s even for a tiny model, unusable. So call a FAST FREE CLOUD model via Venice AI
# (the provider already configured in openclaw — free tier, cost 0). llama-3.2-3b returns
# in ~0.8s. Local ollama stays as an offline FALLBACK.
PROVIDER = os.environ.get("MESH_BOT_PROVIDER", "venice")        # venice (fast cloud) | ollama
VENICE_MODEL = os.environ.get("MESH_BOT_VENICE_MODEL", "llama-3.2-3b")   # small, fast, FREE
OLLAMA_URL = os.environ.get("MESH_BOT_OLLAMA", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.environ.get("MESH_BOT_MODEL", "qwen2.5:0.5b")  # offline fallback only
KEEP_ALIVE = os.environ.get("MESH_BOT_KEEPALIVE", "60m")
NUM_PREDICT = int(os.environ.get("MESH_BOT_NUM_PREDICT", "60"))  # short replies = fast
LLM_TIMEOUT = float(os.environ.get("MESH_BOT_TIMEOUT", "20"))
_ch_raw = os.environ.get("MESH_BOT_CHANNELS", "all").strip().lower()
# "all" or empty => watch EVERY channel (CHANNELS = None means no channel filter)
CHANNELS = None if _ch_raw in ("", "all", "*") else {int(x) for x in _ch_raw.split(",") if x.strip()}
TRIGGERS = [t.strip().lower() for t in os.environ.get("MESH_BOT_TRIGGERS", "clem heavyside,clem").split(",") if t.strip()]
TRIGGERS.sort(key=len, reverse=True)                           # longest phrase first
_allow_raw = os.environ.get("MESH_BOT_ALLOW", "Bob Heavyside,Janet")
ALLOW_SENDERS = {s.strip().lower() for s in _allow_raw.split(",") if s.strip()}  # empty => anyone
COOLDOWN = float(os.environ.get("MESH_BOT_COOLDOWN", "8"))
# Conversation continuity: after a sender addresses Clem ("Clem ..."), keep following
# THAT sender's messages without needing "Clem" again, until the conversation ends —
# ends on inactivity (SESSION_TTL) OR a turn cap (SESSION_MAX_TURNS). The turn cap is a
# hard loop-stop: Janet is also a bot, so an open-ended Clem<->Janet exchange would loop
# forever; after the cap Clem goes quiet until re-addressed by name.
SESSION_TTL = float(os.environ.get("MESH_BOT_SESSION_TTL", "300"))       # 5 min inactivity
SESSION_MAX_TURNS = int(os.environ.get("MESH_BOT_SESSION_TURNS", "4"))   # loop-stop
# MeshCore channel text limit is ~140 chars over the air (a 160-char line to Janet was
# truncated at ~137). Keep a safe margin and prefer ONE short message (fast, like Janet).
CHAN_TEXT_MAX = 130
REPLY_MAX_CHARS = 250
MAX_CHUNKS = 2
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


def decide(msg, sessions=None, now=None):
    """PURE decision: given a hub channel_message (+ current sessions), return
    (channel, sender, query, addressed) if the bot should reply, else None. Replies when
    the message is ADDRESSED to Clem, OR when the sender has an OPEN conversation (a
    prior 'Clem ...' within SESSION_TTL and under the turn cap) — that is the "follow the
    conversation until it ends" behaviour. Read-only (the caller updates sessions)."""
    sessions = sessions or {}
    now = now if now is not None else time.time()
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
    addressed = is_addressed(text)
    sess = sessions.get(sender)
    in_session = (sess is not None and (now - sess["last"]) < SESSION_TTL
                  and sess["turns"] < SESSION_MAX_TURNS)
    if addressed:
        query = extract_query(text)                   # strip the trigger
    elif in_session:
        query = text                                  # follow the conversation, no trigger
    else:
        return None
    if not query:
        return None
    return ch, sender, query, addressed


import glob
import urllib.request

SYSTEM = ("You are Clem Heavyside, a regular on a LoRa mesh channel — a real person, dry "
          "and easygoing. Reply in ONE short line, under 22 words, plain text only (no "
          "markdown, no preamble). Talk like a normal person, NOT a radio nerd: go easy "
          "on jargon and callsign-speak. Do NOT end with '73' as a habit — most replies "
          "need no sign-off at all; when you do sign off, ROTATE it (catch ya, later, "
          "take care, cheers, be good, stay warm, over 'n out) and only rarely '73'. Now "
          "and then, when it fits a friendly line, drop a little old-timer warmth like "
          "calling someone 'Old Man' or saying 'fine business' — sparingly, as flavor, "
          "never every message. NEVER say you are an AI, a model, Llama, or Venice, and "
          "never mention any provider or technology behind you. If asked what you are, who "
          "runs you, or how you work, deflect lightly and stay in character (e.g. 'just "
          "Clem, out here on the mesh'). Do not volunteer details about yourself.")

_venice = {}


def _venice_creds():
    """Read Venice base URL + API key from the openclaw config at runtime, so the secret
    lives in ONE place (never duplicated into this file, the service unit, or git)."""
    if _venice:
        return _venice.get("base"), _venice.get("key")
    for f in glob.glob(os.path.expanduser("~/.openclaw/openclaw.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        provs = d.get("models", {}).get("providers", {})
        for name in ("venice-ai", "venice"):
            p = provs.get(name, {})
            key, base = p.get("apiKey"), (p.get("baseUrl") or p.get("baseURL"))
            if key and base:
                _venice.update(base=base, key=key)
                return base, key
    _venice.update(base=None, key=None)
    return None, None


def _venice_reply(sender, query):
    base, key = _venice_creds()
    if not key:
        raise RuntimeError("no venice key in openclaw config")
    body = json.dumps({"model": VENICE_MODEL, "max_tokens": NUM_PREDICT,
                       "temperature": 0.7, "messages": [
                           {"role": "system", "content": SYSTEM},
                           {"role": "user", "content": f'{sender} says: "{query}"'}]}).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
        d = json.loads(r.read())
    return d["choices"][0]["message"]["content"].strip()


def _ollama_reply(sender, query):
    prompt = f'{sender} says over the mesh: "{query}". Reply as Clem in one short line.'
    body = json.dumps({"model": OLLAMA_MODEL, "prompt": prompt, "system": SYSTEM,
                       "stream": False, "keep_alive": KEEP_ALIVE,
                       "options": {"num_predict": NUM_PREDICT, "temperature": 0.7}}).encode()
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=max(LLM_TIMEOUT, 120)) as r:
        return json.loads(r.read()).get("response", "").strip()


def prime_model():
    """Fast cloud (venice) needs no priming. Only warm the local model when it is the
    configured provider / fallback path, so the offline path isn't a multi-minute stall."""
    if PROVIDER != "ollama":
        base, key = _venice_creds()
        log(f"provider=venice model={VENICE_MODEL} key={'ok' if key else 'MISSING'} "
            f"(local ollama fallback: {OLLAMA_MODEL})")
        return


def llm_reply(sender, query):
    order = ([_venice_reply, _ollama_reply] if PROVIDER == "venice"
             else [_ollama_reply, _venice_reply])
    for fn in order:
        try:
            resp = fn(sender, query)
            resp = " ".join(resp.replace("```", "").split()).strip()
            if len(resp) > 1 and resp[0] in "\"'" and resp[-1] == resp[0]:
                resp = resp[1:-1].strip()          # drop a model's self-wrapping quotes
            if resp:
                return resp[:REPLY_MAX_CHARS]
        except Exception as e:
            log(f"{fn.__name__} error: {e}")
    return f"Copy {sender}. -Clem"


def chunk(text):
    out, t = [], text.strip()
    while t and len(out) < MAX_CHUNKS:
        out.append(t[:CHAN_TEXT_MAX])
        t = t[CHAN_TEXT_MAX:]
    return out


def main():
    log(f"provider={PROVIDER} channels={'ALL' if CHANNELS is None else sorted(CHANNELS)} "
        f"triggers={TRIGGERS} allow={sorted(ALLOW_SENDERS) or 'ANYONE'}")
    prime_model()                       # log provider/model status (no warmup for cloud)
    log(f"session: follow for {int(SESSION_TTL)}s / max {SESSION_MAX_TURNS} turns after a 'Clem'")
    seen, last_reply, sessions = [], {}, {}
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
                    now = time.time()
                    d = decide(msg, sessions, now)
                    if d is None:
                        continue
                    ch, sender, query, addressed = d
                    key = (sender, query)
                    if key in seen:
                        continue
                    seen.append(key); del seen[:-200]
                    if now - last_reply.get(sender, 0) < COOLDOWN:
                        log(f"cooldown: skip rapid msg from {sender}")
                        continue
                    last_reply[sender] = now
                    # conversation session: addressing 'Clem' starts fresh (turns=0); an
                    # in-session follow-up increments toward the loop-stop cap.
                    prev = 0 if addressed else sessions.get(sender, {}).get("turns", 0)
                    sessions[sender] = {"last": now, "turns": prev + 1}
                    tag = "addressed" if addressed else f"follow {prev+1}/{SESSION_MAX_TURNS}"
                    log(f"<- [{sender} ch{ch} {tag}] {query}")
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
