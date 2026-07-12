#!/usr/bin/env python3
"""meshcanvas.py — a 2D-surface / holographic image overlay for MeshCore + LoRa.

SAME LoRa CSS modulation, SAME MeshCore channel transport, SAME MeshSpeak fragmentation —
only a different PAYLOAD codec. A low-res 2D grid is transformed to the frequency domain
(2D DCT) and its coefficients are sent LOW-FREQUENCY-FIRST, so every fragment carries
information about the WHOLE surface. Reconstruction from ANY subset of fragments yields the
entire image at a fidelity proportional to how much arrived — graceful degradation
("holographic" recovery) instead of the naive row-per-fragment scheme where a lost fragment
punches a missing stripe. Ideal for a lossy fire-and-forget channel and for memory transfer.

  demo                          # build a test surface, show holographic degradation, dump grids
  fragments N                   # report MeshSpeak fragment count for an N x N surface
"""
import base64
import json
import math
import struct
import sys

import numpy as np


# ---- frequency-domain transform (orthonormal 2D DCT-II) ------------------------
def dct_matrix(N):
    n = np.arange(N)
    k = n.reshape(-1, 1)
    D = np.sqrt(2.0 / N) * np.cos(np.pi * (2 * n + 1) * k / (2 * N))
    D[0, :] *= 1 / np.sqrt(2.0)
    return D


def dct2(x, D):
    return D @ x @ D.T


def idct2(c, D):
    return D.T @ c @ D


def freq_order(N):
    """Coefficient indices ordered low-frequency first (by i+j) — the holographic order."""
    return sorted([(i, j) for i in range(N) for j in range(N)], key=lambda p: (p[0] + p[1], p[0]))


# ---- encode / decode -----------------------------------------------------------
def encode_surface(grid):
    g = np.asarray(grid, dtype=float)
    N = g.shape[0]
    D = dct_matrix(N)
    C = dct2(g, D)
    order = freq_order(N)
    coeffs = np.array([C[i, j] for (i, j) in order])
    scale = max(1e-6, float(np.abs(coeffs).max()) / 127.0)
    q = np.clip(np.round(coeffs / scale), -127, 127).astype(np.int8)
    return {"N": N, "scale": scale, "order": order, "q": q}


def to_payload(enc):
    """Compact wire payload: [N u8][scale f32][coeffs int8...]. This is what MeshSpeak fragments."""
    return bytes([enc["N"]]) + struct.pack("<f", enc["scale"]) + enc["q"].tobytes()


def reconstruct(enc, keep_indices):
    """Rebuild the surface from ONLY the coefficient positions in keep_indices (a set of ranks
    in freq order). Everything else is treated as zero — that is the holographic recovery."""
    N, scale, order, q = enc["N"], enc["scale"], enc["order"], enc["q"]
    C = np.zeros((N, N))
    for rank, (i, j) in enumerate(order):
        if rank in keep_indices:
            C[i, j] = q[rank] * scale
    D = dct_matrix(N)
    return np.clip(idct2(C, D), 0, 255)


RAMP = " .:-=+*#%@"


def ascii_render(grid):
    g = np.asarray(grid)
    out = []
    for row in g:
        out.append("".join(RAMP[min(len(RAMP) - 1, int(max(0, v) / 256 * len(RAMP)))] for v in row))
    return "\n".join(out)


def rms(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


# ---- a recognizable low-res test surface ---------------------------------------
def demo_image(N=24):
    g = np.full((N, N), 18.0)
    cy = cx = (N - 1) / 2.0
    r = N * 0.44
    for i in range(N):
        for j in range(N):
            if (i - cy) ** 2 + (j - cx) ** 2 < r * r:
                g[i, j] = 205
    for (ey, ex) in [(N * 0.40, N * 0.36), (N * 0.40, N * 0.64)]:
        for i in range(N):
            for j in range(N):
                if (i - ey) ** 2 + (j - ex) ** 2 < (N * 0.055) ** 2:
                    g[i, j] = 18
    for j in range(N):                                   # a smile (parabola opening up)
        dx = (j - cx) / (0.5 * r)
        if abs(dx) <= 1:
            mi = int(cy + 0.28 * r + 0.22 * r * dx * dx)
            for di in range(2):
                if 0 <= mi + di < N:
                    g[mi + di, j] = 18
    return g


def cmd_demo():
    try:
        import meshspeak as ms
        frag_max = ms.WIRE_FRAG_PAYLOAD_MAX
    except Exception:
        frag_max = 118
    N = 24
    img = demo_image(N)
    enc = encode_surface(img)
    payload = to_payload(enc)
    total = len(enc["q"])
    nfrag = max(1, math.ceil(len(payload) / frag_max))
    per_frag = math.ceil(total / nfrag)

    print(f"surface {N}x{N} = {N*N} px  ->  DCT payload {len(payload)} B  ->  "
          f"{nfrag} MeshSpeak fragment(s) (~{per_frag} coeffs each)")
    print(f"(same LoRa modulation + MeshCore channel; only the payload codec differs)\n")

    # holographic PREFIX reconstructions = receiving the first k fragments, low-freq first
    grids = {"original": img.tolist()}
    levels = [("all frags", nfrag), (f"{max(1,nfrag//2)}/{nfrag} frags", max(1, nfrag // 2)),
              (f"2/{nfrag} frags", 2), ("1st frag only", 1)]
    for label, kf in levels:
        keep = set(range(min(total, kf * per_frag)))
        rec = reconstruct(enc, keep)
        print(f"--- {label}  ({len(keep)}/{total} coeffs, RMS {rms(img, rec):.1f}) ---")
        print(ascii_render(rec))
        print()
        grids[label] = rec.tolist()

    # holographic RANDOM subset (any ~1/3 of fragments) still gives the whole image
    import hashlib
    seed = int.from_bytes(hashlib.sha256(payload).digest()[:4], "big")
    rng = np.random.default_rng(seed)
    pick = sorted(rng.choice(nfrag, size=max(1, nfrag // 3), replace=False).tolist())
    keep = set()
    for f in pick:
        keep |= set(range(f * per_frag, min(total, (f + 1) * per_frag)))
    rec = reconstruct(enc, keep)
    print(f"--- RANDOM {len(pick)}/{nfrag} frags {pick} (holographic: any part -> whole)  "
          f"RMS {rms(img, rec):.1f} ---")
    print(ascii_render(rec))
    grids["random subset"] = rec.tolist()

    json.dump({"N": N, "grids": grids}, open("/home/joe/holo_demo.json", "w"))
    print("\n(grids dumped to /home/joe/holo_demo.json)")


def cmd_fragments(N):
    try:
        import meshspeak as ms
        fm = ms.WIRE_FRAG_PAYLOAD_MAX
    except Exception:
        fm = 118
    payload = 1 + 4 + N * N
    print(f"{N}x{N}: DCT payload {payload} B -> {math.ceil(payload/fm)} MeshSpeak fragment(s)")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "fragments":
        cmd_fragments(int(sys.argv[2]))
    else:
        cmd_demo()
