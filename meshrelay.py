#!/usr/bin/env python3
"""meshrelay.py — a tiny tailnet MeshSpeak relay (no radio).

A TCP broadcast hub so MeshSpeak agents on DIFFERENT nodes (over Tailscale) share a channel
without RF: any client's send_channel is relayed to every OTHER client as a channel_message
carrying the raw wire. Lets Clem (N100) and Clavote (Ryzen) run the MeshSpeak STS handshake
over IP while the LoRa link is down. Speaks the same line-delimited-JSON protocol as meshhub,
so meshspeak_agent needs no changes — just point MS_HUB_HOST/MS_HUB_PORT at this relay.

Trust note: the relay is unauthenticated (bind it to the tailnet, a trusted network). It
cannot forge anything — MeshSpeak STS frames are Ed25519-authenticated end to end, so the
relay only shuttles opaque wires; confidentiality/authenticity stay with the agents.

  meshrelay.py [--host 0.0.0.0] [--port 7778]
"""
import argparse
import json
import socket
import threading
import time


def main():
    ap = argparse.ArgumentParser(prog="meshrelay")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=7778)
    a = ap.parse_args()

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((a.host, a.port))
    srv.listen(16)
    clients = {}
    lock = threading.Lock()

    def log(m):
        print(f"[meshrelay {time.strftime('%H:%M:%S')}] {m}", flush=True)

    log(f"listening on {a.host}:{a.port} (tailnet MeshSpeak relay, no radio)")

    def broadcast(obj, exclude):
        line = (json.dumps(obj) + "\n").encode()
        with lock:
            for c in list(clients):
                if c is exclude:
                    continue
                try:
                    c.sendall(line)
                except OSError:
                    pass

    def handle(conn, addr):
        with lock:
            clients[conn] = "?"
        log(f"client connected from {addr[0]} ({len(clients)} total)")
        buf = b""
        try:
            while True:
                data = conn.recv(4096)
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
                    act = m.get("action")
                    if act == "register":
                        with lock:
                            clients[conn] = m.get("name", "?")
                        log(f"registered '{clients[conn]}' from {addr[0]}")
                    elif act == "send_channel":
                        text = m.get("text", "")
                        broadcast({"type": "channel_message", "channelIdx": m.get("channelIdx"),
                                   "senderName": clients.get(conn, "?"), "text": text,
                                   "raw": text}, exclude=conn)
        except OSError:
            pass
        finally:
            with lock:
                clients.pop(conn, None)
            try:
                conn.close()
            except OSError:
                pass
            log(f"client disconnected ({len(clients)} total)")

    try:
        while True:
            conn, addr = srv.accept()
            threading.Thread(target=handle, args=(conn, addr), daemon=True).start()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
