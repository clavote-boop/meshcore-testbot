#!/usr/bin/env python3
# meshhub.py - Python MeshCore hub (firmware-current; drop-in for mesh-hub.js)
# Connects to the radio via the meshcore PYTHON lib (companion protocol v10), speaks the SAME
# TCP 7777 JSON protocol the bots + control-dashboard use, and integrates MeshSpeak (MT):
# detects/decrypts MeshSpeak frames for the control view, drains radio memory on every connect.
#
#   client -> hub : {"action":"register","name":...}
#                   {"action":"send_channel","channelIdx":N,"text":...}
#   hub -> client : {"type":"hub_state","connected":bool,"channels":[{channelIdx,name}]}
#                   {"type":"channel_message","channelIdx":N,"senderName":...,"text":...,"ms":bool,"raw":...}
#                   {"type":"channels_update","channels":[...]}
#                   {"type":"hub_connected"} / {"type":"hub_disconnected"}
import asyncio, json, os, time, signal
from meshcore import MeshCore, EventType
import meshspeak as ms

PORT = int(os.environ.get("HUB_PORT", "7777"))
SERIAL = os.environ.get("SERIAL_PORT", "/dev/ttyUSB0")
BAUD = int(os.environ.get("BAUD_RATE", "115200"))
HEARTBEAT = "/tmp/mesh-hub-heartbeat"
NUM_CHANNELS = 12

clients = {}      # writer -> name
channels = []     # [{channelIdx, name}]
mc = None
connected = False
ms_store = ms.FragmentStore()
tx_store = ms.FragmentStore()

# Control view holds the session key so it can DECRYPT MeshSpeak for display (Joe's monitor).
try:
    import binascii as _ba
    _k, _s, _w = open("/home/joe/clem_demo_session.txt").read().split()
    MS_KEY, MS_SALT = _ba.unhexlify(_k), _ba.unhexlify(_s)
except Exception:
    MS_KEY = MS_SALT = None


def log(m):
    print(f"[meshhub {time.strftime('%H:%M:%S')}] {m}", flush=True)


async def send_to(writer, obj):
    try:
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()
    except Exception:
        pass


async def broadcast(obj, exclude=None):
    for w in list(clients.keys()):
        if exclude is not None and w is exclude:
            continue
        await send_to(w, obj)


async def relay_local(sender_writer, idx, text):
    """Relay a locally-sent channel frame to the OTHER local clients.

    The radio is half-duplex: it never hears its own transmission. So a frame one local
    client sends reaches sibling clients on this node ONLY if the hub relays it. Relay the
    RAW wire so each client can decode with ITS OWN session key -- the hub does not need to
    hold that key. Decryption is attempted solely to enrich the monitor view, and only when
    the hub happens to hold a matching key.
    """
    frame = ms.wire_to_frame(text)
    if frame is None:                                  # plain channel text
        await broadcast({"type": "channel_message", "channelIdx": idx,
                         "senderName": "local", "text": text}, exclude=sender_writer)
        return
    src = frame[4] if len(frame) > 4 else "?"
    shown = None
    if MS_KEY and (frame[1] & ms.F_ENCRYPTED):
        try:
            k2, v2 = ms.decode(frame, tx_store, key=MS_KEY, session_salt=MS_SALT)
            if k2 == "msg":
                dv = v2.decode("utf-8", "replace") if isinstance(v2, (bytes, bytearray)) else str(v2)
                shown = dv + "  [decrypted MS -- sent]"
        except Exception:
            pass
    await broadcast({"type": "channel_message", "channelIdx": idx,
                     "senderName": f"agent{src}",
                     "text": shown if shown is not None else text,
                     "ms": True, "raw": text}, exclude=sender_writer)


def parse_sender(text):
    if text and ": " in text:
        return text[:text.index(": ")], text
    return "", text


# ---- radio side ----
async def on_channel(event):
    p = event.payload or {}
    idx = p.get("channel_idx", p.get("channelIdx", -1))
    text = p.get("text", "") or ""
    sender = (p.get("sender_name") or p.get("from") or p.get("pubkey_prefix")
              or parse_sender(text)[0] or "?")
    frame = None
    for _cand in [text] + text.split():     # whole text, then each whitespace token (strips "Name:  ")
        frame = ms.wire_to_frame(_cand)
        if frame is not None:
            break
    if frame is not None:                       # MeshSpeak frame (AI-agent room)
        ms_store.evict_stale()
        if frame[1] & ms.F_ENCRYPTED:
            dk, dv = ms.decode(frame, ms_store, key=MS_KEY, session_salt=MS_SALT) if MS_KEY else ("drop", None)
            if dk == "partial":
                log(f"CH{idx} MeshSpeak fragment <{sender}>")
                return
            if dk == "msg":
                shown = (dv.decode("utf-8", "replace") if isinstance(dv, (bytes, bytearray)) else str(dv)) + "  [decrypted MS]"
            elif dk == "crypto":
                shown = f"[decrypted MS crypto-tx chain=0x{dv['chain']:02x} txop=0x{dv['txop']:02x}]"
            else:
                shown = ms.frame_to_wire(frame) + "  (encrypted MeshSpeak frame -- no/invalid key)"
        else:
            kind, val = ms.decode(frame, ms_store)
            if kind == "partial":
                log(f"CH{idx} MeshSpeak fragment <{sender}>")
                return
            if kind == "crypto":
                shown = f"[MeshSpeak crypto-tx chain=0x{val['chain']:02x} txop=0x{val['txop']:02x}]"
            elif kind == "msg":
                v = val
                shown = v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)
            elif kind == "control":
                log(f"CH{idx} MeshSpeak control frame <{sender}>")
                await broadcast({"type": "channel_message", "channelIdx": idx,
                                 "senderName": str(sender), "text": "[MeshSpeak control]",
                                 "ms": True, "raw": ms.frame_to_wire(frame)})
                return
            else:
                log(f"CH{idx} MeshSpeak drop: {val}")
                return
        log(f"CH{idx} [MeshSpeak] <{sender}>: {shown[:60]}")
        await broadcast({"type": "channel_message", "channelIdx": idx,
                         "senderName": str(sender), "text": shown, "ms": True, "raw": ms.frame_to_wire(frame)})
        return
    log(f"CH{idx} <{sender}>: {text[:60]}")
    await broadcast({"type": "channel_message", "channelIdx": idx,
                     "senderName": str(sender), "text": text})


async def on_any(event):
    et = getattr(event, "type", None)
    if et is not None and et != EventType.CHANNEL_MSG_RECV:
        n = getattr(et, "name", str(et))
        log(f"[evt] {n}")


async def refresh_channels():
    global channels
    out = []
    for i in range(NUM_CHANNELS):
        try:
            ev = await mc.commands.get_channel(i)
            cp = getattr(ev, "payload", ev) or {}
            name = cp.get("channel_name") or cp.get("name") or ""
            if name:
                out.append({"channelIdx": i, "name": name})
        except Exception:
            pass
    channels = out
    await broadcast({"type": "channels_update", "channels": channels})


async def drain_radio_buffer(mc):
    """ALWAYS check radio memory on (re)connect. Messages received while the companion was
    disconnected stay buffered in the radio; start_auto_message_fetching() only pulls ONE on start
    and otherwise waits for a fresh MESSAGES_WAITING push (lost during a disconnect). So explicitly
    drain get_msg() until NO_MORE_MSGS -- each fetched message dispatches to on_channel."""
    recovered = 0
    while recovered < 256:
        try:
            ev = await mc.commands.get_msg(timeout=4)
        except Exception:
            break
        if ev is None or getattr(ev, "type", None) in (EventType.NO_MORE_MSGS, EventType.ERROR):
            break
        recovered += 1
    if recovered:
        log(f"radio-memory recovery: drained {recovered} buffered message(s) on connect")
    else:
        log("radio-memory check: no buffered messages")
    return recovered


async def connect_radio():
    global mc, connected
    while True:
        try:
            log(f"connecting radio {SERIAL}@{BAUD}")
            mc = await MeshCore.create_serial(SERIAL, baudrate=BAUD)
            if mc is None:
                raise RuntimeError("create_serial returned None (radio not responding)")
            mc.subscribe(EventType.CHANNEL_MSG_RECV, on_channel)
            mc.subscribe(None, on_any)
            await mc.start_auto_message_fetching()
            await drain_radio_buffer(mc)            # recover anything missed while disconnected
            connected = True
            log("radio connected")
            await refresh_channels()
            await broadcast({"type": "hub_connected"})
            await broadcast({"type": "hub_state", "connected": True, "channels": channels})
            while connected and mc is not None:
                if hasattr(mc, "is_connected") and not mc.is_connected:
                    raise RuntimeError("radio link dropped")
                try:
                    open(HEARTBEAT, "w").write(str(time.time()))
                except Exception:
                    pass
                await asyncio.sleep(20)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log(f"radio error: {e}")
            connected = False
            try:
                if mc:
                    await mc.disconnect()
            except Exception:
                pass
            mc = None
            await broadcast({"type": "hub_disconnected"})
            await broadcast({"type": "hub_state", "connected": False, "channels": channels})
            await asyncio.sleep(5)


# ---- client side ----
async def handle_client(reader, writer):
    clients[writer] = "?"
    log(f"client connected ({len(clients)} total)")
    await send_to(writer, {"type": "hub_state", "connected": connected, "channels": channels})
    buf = ""
    try:
        while True:
            data = await reader.read(4096)
            if not data:
                break
            buf += data.decode(errors="replace")
            while "\n" in buf:
                line, buf = buf.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                act = msg.get("action")
                if act == "register":
                    clients[writer] = msg.get("name", "?")
                    log(f"registered: {clients[writer]}")
                elif act == "send_channel":
                    idx = msg.get("channelIdx")
                    text = msg.get("text", "")
                    if mc is None:
                        log(f"send REFUSED CH{idx}: radio not connected")
                        await send_to(writer, {"type": "send_error", "channelIdx": idx,
                                               "error": "radio not connected"})
                    elif idx is not None and text:
                        try:
                            await mc.commands.send_chan_msg(int(idx), str(text))
                            log(f"sent CH{idx}: {str(text)[:50]}")
                        except Exception as e:
                            log(f"send error: {e}")
                        await relay_local(writer, idx, str(text))
    except Exception:
        pass
    finally:
        clients.pop(writer, None)
        try:
            writer.close()
        except Exception:
            pass
        log(f"client disconnected ({len(clients)} total)")


async def main():
    server = await asyncio.start_server(handle_client, "127.0.0.1", PORT)
    log(f"TCP hub listening on 127.0.0.1:{PORT}")
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for _sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(_sig, stop.set)
        except NotImplementedError:
            pass
    radio_task = asyncio.create_task(connect_radio())
    async with server:
        await stop.wait()
    log("shutdown: releasing radio companion session")
    radio_task.cancel()
    try:
        await asyncio.wait_for(radio_task, timeout=2)
    except BaseException:
        pass
    if mc is not None:
        try:
            await asyncio.wait_for(mc.stop_auto_message_fetching(), timeout=2)
        except BaseException:
            pass
        try:
            await asyncio.wait_for(mc.disconnect(), timeout=2)
            log("radio disconnected cleanly")
        except BaseException as e:
            log(f"shutdown disconnect timeout: {e}")


asyncio.run(main())
