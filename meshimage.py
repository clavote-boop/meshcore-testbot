#!/usr/bin/env python3
"""meshimage.py — send/receive a small image over MeshCore using the fountain overlay.

Ties the holographic erasure codec (meshfountain) to the LoRa/MeshCore transport so a low-res
2D image survives a lossy fire-and-forget channel: the image is fountain-coded into rateless
droplets, each droplet aired as one channel frame; the receiver collects droplets until the
fountain peels back the EXACT image (any ~K of them, order-free, drops-tolerant).

Wire (plaintext, so any hub-client can catch it, no session key needed):
    a channel text line  "MIMG:<base64(droplet)>"  — one droplet per line.

  selftest                             pure-python encode -> random loss -> decode, byte-exact
  send  --channel N [--file grid.json] [--pattern H|smiley] [--overhead 1.8]
  recv  --channel N [--timeout 120] [--out img.json]

Run send on the radio-owning node (MS_HUB_HOST=127.0.0.1), recv on the peer.
"""
import base64, json, os, struct, sys, time, socket, random

sys.path.insert(0, os.path.expanduser("~/meshcore-bots"))
import meshfountain as fountain
import meshspeak_agent as A

TAG = "MIMG:"
IMG = struct.Struct("<BB")          # width, height  (grayscale 0-255 pixels follow)


# ---- tiny built-in 16x16 patterns (grayscale) -------------------------------------------
def pattern(name="H"):
    W = H = 16
    g = [[0] * W for _ in range(H)]
    if name == "smiley":
        for (r, c) in [(4, 5), (4, 10), (5, 5), (5, 10)]:
            g[r][c] = 255
        for c in range(5, 11):
            g[11][c] = 255
        g[10][4] = g[10][11] = 255
    else:  # "H" for Hermes
        for r in range(3, 13):
            g[r][4] = g[r][11] = 255
        for c in range(4, 12):
            g[8][c] = 255
    return g


def img_to_bytes(g):
    h = len(g); w = len(g[0])
    return IMG.pack(w, h) + bytes(px & 0xFF for row in g for px in row)


def bytes_to_img(b):
    w, h = IMG.unpack(b[:2])
    px = b[2:2 + w * h]
    return [[px[r * w + c] for c in range(w)] for r in range(h)]


def render(g):
    ramp = " .:-=+*#%@"
    return "\n".join("".join(ramp[min(9, px * 10 // 256)] for px in row) for row in g)


# ---- transport --------------------------------------------------------------------------
def _send_text(sock, ch, text):
    sock.sendall((json.dumps({"action": "send_channel", "channelIdx": ch, "text": text}) + "\n").encode())


def cmd_send(a):
    grid = json.load(open(os.path.expanduser(a.file))) if a.file else pattern(a.pattern)
    if isinstance(grid, dict):
        grid = grid["px"] if "px" in grid else grid
    data = img_to_bytes(grid)
    droplets = fountain.encode(data, block_size=48, overhead=a.overhead)
    print(f"image {len(grid[0])}x{len(grid)} -> {len(data)} B -> {len(droplets)} droplets "
          f"(~{len(droplets[0])} B each = 1 fragment)")
    print(render(grid))
    s = A._connect("meshimage-tx")
    for i, d in enumerate(droplets):
        _send_text(s, a.channel, TAG + base64.b64encode(d).decode())
        print(f"  aired droplet {i+1}/{len(droplets)}", flush=True)
        if i + 1 < len(droplets):
            time.sleep(a.gap)
    s.close()
    print("done — all droplets aired")


def cmd_recv(a):
    s = A._connect("meshimage-rx")
    s.settimeout(1.0)
    print(f"listening for image droplets on ch{a.channel} (timeout {a.timeout:.0f}s) …")
    seen, droplets, buf = set(), [], b""
    end = time.time() + a.timeout
    while time.time() < end:
        try:
            data = s.recv(4096)
        except socket.timeout:
            continue
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
            if m.get("type") != "channel_message":
                continue
            if m.get("channelIdx") not in (None, a.channel):
                continue
            text = (m.get("text") or "").strip()
            i = text.find(TAG)
            if i < 0:
                continue
            b64 = text[i + len(TAG):].split()[0]
            try:
                d = base64.b64decode(b64)
            except Exception:
                continue
            if d in seen:
                continue
            seen.add(d)
            droplets.append(d)
            got = fountain.decode(droplets)
            print(f"  droplet {len(droplets)} collected; decode -> "
                  f"{'IMAGE READY' if got else 'need more'}", flush=True)
            if got:
                grid = bytes_to_img(got)
                print("\n=== DECODED IMAGE ===")
                print(render(grid))
                if a.out:
                    json.dump(grid, open(os.path.expanduser(a.out), "w"))
                    print(f"(saved to {a.out})")
                s.close()
                return 0
    s.close()
    print("timed out — not enough droplets arrived")
    return 1


def cmd_selftest(a=None):
    import math
    grid = pattern("H")
    data = img_to_bytes(grid)
    droplets = fountain.encode(data, block_size=48, overhead=2.2)
    K = math.ceil(len(data) / 48)
    print(f"H 16x16 -> {len(data)} B -> {len(droplets)} droplets (K={K} blocks)")
    rng = random.Random(7)
    keep_n = min(len(droplets), math.ceil(K * 1.6))          # realistic margin above K
    kept = rng.sample(droplets, keep_n)                       # the rest 'lost' in flight
    out = fountain.decode(kept)
    ok = out == data
    print(f"  kept {len(kept)}/{len(droplets)} droplets (rest 'lost')")
    print(f"  [{'PASS' if ok else 'FAIL'}] byte-exact image recovered from the lossy subset")
    if ok:
        print(render(bytes_to_img(out)))
    return 0 if ok else 1


def main():
    import argparse
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(required=True)
    s = sub.add_parser("send"); s.add_argument("--channel", type=int, required=True)
    s.add_argument("--file"); s.add_argument("--pattern", default="H")
    s.add_argument("--overhead", type=float, default=1.8); s.add_argument("--gap", type=float, default=1.2)
    s.set_defaults(fn=cmd_send)
    r = sub.add_parser("recv"); r.add_argument("--channel", type=int, required=True)
    r.add_argument("--timeout", type=float, default=120.0); r.add_argument("--out")
    r.set_defaults(fn=cmd_recv)
    sub.add_parser("selftest").set_defaults(fn=cmd_selftest)
    a = p.parse_args()
    sys.exit(a.fn(a) or 0)


if __name__ == "__main__":
    main()
