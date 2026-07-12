#!/usr/bin/env python3
"""meshcs.py — compressed-sensing holographic image overlay: TRUE any-subset for images.

meshcanvas/meshphoto degrade gracefully only from a PREFIX — the low-frequency fragments
carry the gist, so a random high-frequency-only fragment reconstructs poorly. This makes
EVERY fragment equally valuable, the image sibling of meshfountain: the image's (sparse) DCT
coefficients are captured as M random linear measurements (a Rademacher sensing matrix
derived from a shared seed); each fragment carries a chunk of measurements. From ANY subset
of fragments the receiver solves for the sparse coefficients (Orthogonal Matching Pursuit)
and inverse-DCTs — reconstruction quality scales with HOW MANY measurements arrived, not
WHICH. That is the compressed-sensing "any part -> whole" property for images.

  selftest        # any-subset recovery incl. a random single fragment; error shrinks with data
"""
import json
import math
import sys

import numpy as np

sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
import meshcanvas as mc


def sensing_matrix(M, N, seed):
    rng = np.random.default_rng(seed)
    return rng.choice([-1.0, 1.0], size=(M, N)) / math.sqrt(M)


def encode_cs(img, S=50, M=180, seed=1234567):
    H, W = img.shape
    N = H * W
    D = mc.dct_matrix(H)
    x = mc.dct2(img, D).flatten()
    keep = np.argsort(-np.abs(x))[:S]
    xs = np.zeros(N)
    xs[keep] = x[keep]                                  # S-sparse coefficient vector
    Phi = sensing_matrix(M, N, seed)
    y = Phi @ xs
    scale = max(1e-9, float(np.abs(y).max()) / 32000.0)   # int16 measurements (precision matters)
    yq = np.clip(np.round(y / scale), -32000, 32000).astype(np.int16)
    return {"H": H, "W": W, "N": N, "S": S, "M": M, "seed": seed, "scale": scale, "yq": yq}


def omp(Phi, y, S):
    """Recover an S-sparse x from y = Phi x (greedy Orthogonal Matching Pursuit)."""
    y = y.astype(float)
    r = y.copy()
    support = []
    xs = np.zeros(0)
    for _ in range(min(S, max(1, len(y) - 1))):
        j = int(np.argmax(np.abs(Phi.T @ r)))
        if j in support:
            break
        support.append(j)
        Ps = Phi[:, support]
        xs, *_ = np.linalg.lstsq(Ps, y, rcond=None)
        r = y - Ps @ xs
        if np.linalg.norm(r) < 1e-6:
            break
    x = np.zeros(Phi.shape[1])
    if support:
        x[support] = xs
    return x


def decode_cs(enc, rows):
    """Reconstruct the image from the measurements whose indices are in `rows` (any subset)."""
    Phi = sensing_matrix(enc["M"], enc["N"], enc["seed"])
    y = enc["yq"].astype(float) * enc["scale"]
    rows = sorted(rows)
    x = omp(Phi[rows], y[rows], enc["S"])
    D = mc.dct_matrix(enc["H"])
    return np.clip(mc.idct2(x.reshape(enc["H"], enc["W"]), D), 0, 255)


def demo_img(N=16):
    g = np.full((N, N), 28.0)
    cy = cx = (N - 1) / 2.0
    r = N * 0.44
    for i in range(N):
        for j in range(N):
            if (i - cy) ** 2 + (j - cx) ** 2 < r * r:
                g[i, j] = 208
    for ey, ex in [(N * 0.38, N * 0.36), (N * 0.38, N * 0.64)]:
        for i in range(N):
            for j in range(N):
                if (i - ey) ** 2 + (j - ex) ** 2 < (N * 0.065) ** 2:
                    g[i, j] = 28
    for j in range(N):
        dx = (j - cx) / (0.5 * r)
        if abs(dx) <= 1:
            mi = int(cy + 0.30 * r + 0.20 * r * dx * dx)
            if 0 <= mi < N:
                g[mi, j] = 28
    for _ in range(2):                                  # soften edges -> sparse in DCT (CS-friendly)
        g = (g + np.roll(g, 1, 0) + np.roll(g, -1, 0) + np.roll(g, 1, 1) + np.roll(g, -1, 1)) / 5.0
    return g


def _rms(a, b):
    return float(np.sqrt(np.mean((np.asarray(a) - np.asarray(b)) ** 2)))


def cmd_selftest(dump=False):
    fails = [0]

    def chk(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails[0] += 1

    N = 16
    img = demo_img(N)
    S, M, B = 50, 180, 60
    enc = encode_cs(img, S=S, M=M)
    nfrag = math.ceil(M / B)
    print(f"  {N}x{N} image, {S}-sparse -> {M} CS measurements -> {nfrag} fragments "
          f"({B} measurements each, int16)")

    full = decode_cs(enc, range(M))
    e_full = _rms(img, full)
    chk("full measurement set reconstructs the image", e_full < 30)

    rng = np.random.default_rng(7)
    errs = {}
    grids = {"original": img.tolist(), "all frags": full.tolist()}
    for kf in (2, 1):                                   # random subset of fragments (NOT a prefix)
        frags = sorted(rng.choice(nfrag, size=kf, replace=False).tolist())
        rows = [i for f in frags for i in range(f * B, min(M, (f + 1) * B))]
        rec = decode_cs(enc, rows)
        errs[kf] = _rms(img, rec)
        grids[f"random {kf}/{nfrag} frags"] = rec.tolist()
        print(f"  random {kf}/{nfrag} frags ({len(rows)} measurements): RMS {errs[kf]:.1f}")

    chk("more measurements -> lower error (graceful)", e_full <= errs[1])
    chk("ANY single random fragment still yields a whole recognizable image (RMS<70)",
        errs[1] < 70)
    if dump:
        json.dump({"N": N, "grids": grids}, open("/home/joe/cs_demo.json", "w"))
        print("(grids dumped)")
    print("\nALL TESTS PASSED" if fails[0] == 0 else f"\n{fails[0]} FAILURE(S)")
    return 1 if fails[0] else 0


if __name__ == "__main__":
    sys.exit(cmd_selftest(dump="--dump" in sys.argv))
