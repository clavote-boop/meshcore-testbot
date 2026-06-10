#!/usr/bin/env python3
# meshhub.py - Python MeshCore hub (firmware-current; drop-in for mesh-hub.js)
# Connects to the radio via the meshcore PYTHON lib (companion protocol v10), speaks the SAME
# TCP 7777 JSON protocol the bots + control-dashboard use, and integrates MeshTalk (MT):
# detects/decrypts MeshTalk frames for the control view, drains radio memory on every connect.
#
#   client -> hub : {"action":"register","name":...}
#                   {"action":"send_channel","channelIdx":N,"text":...}
#   hub -> client : {"type":"hub_state","connected":bool,"channels":[{channelIdx,name}]}
#                   {"type":"channel_message","channelIdx":N,"senderName":...,"text":...,"ms":bool,"raw":...}
#                   {"type":"channels_update","channels":[...]}
#                   {"type":"hub_connected"} / {"type":"hub_disconnected"}
import asyncio, json, os, time, signal
from meshcore import MeshCore, EventType
import meshtalk as ms

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

# Control view holds the session key so it can DECRYPT MeshTalk for display (Joe's monitor).
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


async def broadcast(obj):
    for w in list(clients.keys()):
        await send_to(w, obj)


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
    if frame is not None:                       # MeshTalk frame (AI-agent room)
        ms_store.evict_stale()
        if frame[1] & ms.F_ENCRYPTED:
            dk, dv = ms.decode(frame, ms_store, key=MS_KEY, session_salt=MS_SALT) if MS_KEY else ("drop", None)
            if dk == "partial":
                log(f"CH{idx} MeshTalk fragment <{sender}>")
                return
            if dk == "msg":
                shown = (dv.decode("utf-8", "replace") if isinstance(dv, (bytes, bytearray)) else str(dv)) + "  [decrypted MS]"
            elif dk == "crypto":
                shown = f"[decrypted MS crypto-tx chain=0x{dv['chain']:02x} txop=0x{dv['txop']:02x}]"
            else:
                shown = ms.frame_to_wire(frame) + "  (encrypted MeshTalk frame -- no/invalid key)"
        else:
            kind, val = ms.decode(frame, ms_store)
            if kind == "partial":
                log(f"CH{idx} MeshTalk fragment <{sender}>")
                return
            if kind == "crypto":
                shown = f"[MeshTalk crypto-tx chain=0x{val['chain']:02x} txop=0x{val['txop']:02x}]"
            elif kind == "msg":
                v = val
                shown = v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else str(v)
            else:
                log(f"CH{idx} MeshTalk drop: {val}")
                return
        log(f"CH{idx} [MeshTalk] <{sender}>: {shown[:60]}")
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
                    if mc is not None and idx is not None and text:
                        try:
                            await mc.commands.send_chan_msg(int(idx), str(text))
                            log(f"sent CH{idx}: {str(text)[:50]}")
                            _fr = ms.wire_to_frame(str(text))
                            if _fr is not None and MS_KEY and (_fr[1] & ms.F_ENCRYPTED):
                                _k2, _v2 = ms.decode(_fr, tx_store, key=MS_KEY, session_salt=MS_SALT)
                                if _k2 == "msg":
                                    _t = _v2.decode("utf-8", "replace") if isinstance(_v2, (bytes, bytearray)) else str(_v2)
                                    await broadcast({"type": "channel_message", "channelIdx": idx,
                                                     "senderName": f"agent{_fr[4]}",
                                                     "text": _t + "  [decrypted MS -- sent]", "ms": True, "raw": str(text)})
                        except Exception as e:
                            log(f"send error: {e}")
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
