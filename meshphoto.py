#!/usr/bin/env python3
"""meshphoto.py — photo-optimized holographic image overlay for MeshCore/LoRa.

meshcanvas is grayscale with one global scale — fine for line art, weak for photos. This
adds what real photographs need, while keeping the holographic prefix property (any prefix
of fragments -> the whole picture at lower fidelity):

  * COLOR via YCbCr, with the chroma planes subsampled 2x (eyes carry less chroma detail).
  * PERCEPTUAL, frequency-weighted quantization — low frequencies (where photo energy lives)
    are kept fine, high frequencies quantized hard; chroma coarser than luma.
  * Coefficients from all three planes merged into ONE importance order (low-freq luma first,
    then low-freq chroma, then detail), so an early prefix already gives a coarse FULL-COLOR
    image that sharpens as more fragments arrive.

  photo        # synth a photo-like scene, show color graceful degradation, dump grids
"""
import json
import math
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import meshcanvas as mc


def rgb2ycc(img):
    R, G, B = img[..., 0], img[..., 1], img[..., 2]
    Y = 0.299 * R + 0.587 * G + 0.114 * B
    Cb = 128 - 0.168736 * R - 0.331264 * G + 0.5 * B
    Cr = 128 + 0.5 * R - 0.418688 * G - 0.081312 * B
    return Y, Cb, Cr


def ycc2rgb(Y, Cb, Cr):
    R = Y + 1.402 * (Cr - 128)
    G = Y - 0.344136 * (Cb - 128) - 0.714136 * (Cr - 128)
    B = Y + 1.772 * (Cb - 128)
    return np.clip(np.stack([R, G, B], -1), 0, 255)


def subsample2(P):
    N = P.shape[0]
    return P.reshape(N // 2, 2, N // 2, 2).mean(axis=(1, 3))


def upsample2(P):
    return np.repeat(np.repeat(P, 2, 0), 2, 1)


def qstep(N, base, alpha):
    u = np.arange(N).reshape(-1, 1)
    v = np.arange(N).reshape(1, -1)
    return base * (1.0 + alpha * (u + v))


# per-plane: (name, base-quant, alpha, chroma?) — chroma coarser + subsampled
def encode_photo(rgb):
    N = rgb.shape[0]
    Y, Cb, Cr = rgb2ycc(rgb)
    planes = [("Y", Y, N, 6.0, 0.30, 1.0),
              ("Cb", subsample2(Cb), N // 2, 16.0, 0.40, 1.6),
              ("Cr", subsample2(Cr), N // 2, 16.0, 0.40, 1.6)]
    enc = {"N": N, "planes": []}
    slots = []                                          # (importance, plane_i, u, v)
    for pi, (nm, P, n, base, alpha, w) in enumerate(planes):
        D = mc.dct_matrix(n)
        C = mc.dct2(P, D)
        Q = qstep(n, base, alpha)
        Cq = np.round(C / Q).astype(np.int16)
        enc["planes"].append({"name": nm, "n": n, "base": base, "alpha": alpha, "cq": Cq})
        for u in range(n):
            for v in range(n):
                if Cq[u, v] != 0:
                    slots.append((w * (u + v) / n, pi, u, v))       # importance = norm freq * weight
    slots.sort(key=lambda s: s[0])
    enc["order"] = [(pi, u, v) for _, pi, u, v in slots]
    return enc


def reconstruct_photo(enc, keep_n):
    N = enc["N"]
    keep = set(map(tuple, enc["order"][:keep_n]))
    outs = []
    for pi, pl in enumerate(enc["planes"]):
        n, base, alpha, Cq = pl["n"], pl["base"], pl["alpha"], pl["cq"]
        Q = qstep(n, base, alpha)
        C = np.zeros((n, n))
        for u in range(n):
            for v in range(n):
                if (pi, u, v) in keep:
                    C[u, v] = Cq[u, v] * Q[u, v]
        D = mc.dct_matrix(n)
        outs.append(mc.idct2(C, D))
    Y = outs[0]
    Cb = upsample2(outs[1])
    Cr = upsample2(outs[2])
    return ycc2rgb(Y, Cb, Cr)


def demo_photo(N=48):
    img = np.zeros((N, N, 3))
    for i in range(N):
        t = i / N
        if t < 0.60:                                    # sky: blue, lightening to horizon
            k = t / 0.60
            img[i, :] = [70 + 130 * k, 120 + 100 * k, 200 + 45 * k]
        else:                                           # ground: green -> earthy
            g = (t - 0.60) / 0.40
            img[i, :] = [60 + 70 * g, 120 - 40 * g, 55 + 15 * g]
    cy, cx, r = N * 0.26, N * 0.72, N * 0.11            # sun
    for i in range(N):
        for j in range(N):
            if (i - cy) ** 2 + (j - cx) ** 2 < r * r:
                img[i, j] = [255, 232, 120]
    for j in range(N):                                  # a soft hill
        h = int(N * 0.60 + N * 0.10 * math.sin(3.1 * j / N))
        for i in range(h, min(N, h + int(N * 0.06))):
            img[i, j] = [55, 95, 60]
    return np.clip(img, 0, 255)


def cmd_photo():
    try:
        import meshspeak as ms
        fm = ms.WIRE_FRAG_PAYLOAD_MAX
    except Exception:
        fm = 118
    N = 48
    img = demo_photo(N)
    enc = encode_photo(img)
    coeffs = len(enc["order"])
    payload = coeffs * 1.15 + 8                          # ~1.15 B/coeff after light packing
    nfrag = max(1, math.ceil(payload / fm))
    print(f"photo {N}x{N} color -> {coeffs} significant coeffs (Y + subsampled Cb/Cr) "
          f"-> ~{int(payload)} B -> {nfrag} MeshSpeak fragment(s)")
    grids = {"original": img.astype(np.uint8).tolist()}
    for label, frac in [("all frags", 1.0), ("~half", 0.5), ("~quarter", 0.25), ("~1/8", 0.12)]:
        kn = max(3, int(coeffs * frac))
        rec = reconstruct_photo(enc, kn)
        err = float(np.sqrt(np.mean((img - rec) ** 2)))
        print(f"  {label:10s} {kn:4d}/{coeffs} coeffs  RMS {err:5.1f}")
        grids[label] = rec.astype(np.uint8).tolist()
    json.dump({"N": N, "grids": grids}, open("/home/joe/photo_demo.json", "w"))
    print("(color grids dumped to /home/joe/photo_demo.json)")


if __name__ == "__main__":
    cmd_photo()
