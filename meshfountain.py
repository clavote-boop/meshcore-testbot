#!/usr/bin/env python3
"""meshfountain.py — holographic (spread) erasure overlay for MeshCore/LoRa.

The DCT overlay (meshcanvas) gives IMAGES graceful blur from any prefix. This gives EXACT
data — text, memory, capsules — holographic recovery: split the payload into K blocks and
emit rateless XOR "droplets" (an LT fountain code). Every droplet mixes a pseudo-random set
of blocks, so no droplet is special and ANY ~K(1+ε) droplets rebuild the payload byte-exact,
in any order, with arbitrary ones missing. Lose fragments on a fire-and-forget channel and
the message still decodes — you just need enough droplets, not specific ones.

Each droplet is sized to one MeshSpeak fragment; ride it over the same MeshCore channel.

  selftest        # exact text recovery from a random droplet subset + with drops
"""
import hashlib
import math
import os
import random
import struct
import sys

MAGIC = 0xF0
HDR = struct.Struct("<BHIH")            # magic, K, total_len, seed  (9 bytes)


def _blocks(data, B):
    pad = data + b"\x00" * ((-len(data)) % B)
    K = len(pad) // B
    return [pad[i * B:(i + 1) * B] for i in range(K)], len(data), K


def _robust_soliton(K, c=0.08, delta=0.5):
    rho = [0.0] * (K + 1)
    rho[1] = 1.0 / K
    for i in range(2, K + 1):
        rho[i] = 1.0 / (i * (i - 1))
    R = max(1.0, c * math.log(max(2, K) / delta) * math.sqrt(K))
    tau = [0.0] * (K + 1)
    kr = max(1, int(K / R))
    for i in range(1, kr):
        if i <= K:
            tau[i] = R / (i * K)
    if 1 <= kr <= K:
        tau[kr] += R * math.log(R / delta) / K
    mu = [rho[i] + tau[i] for i in range(K + 1)]
    Z = sum(mu)
    return [m / Z for m in mu]


def _degree(rng, dist, K):
    x, acc = rng.random(), 0.0
    for d in range(1, K + 1):
        acc += dist[d]
        if x <= acc:
            return d
    return K


def _indices(seed, K, dist):
    """Deterministic block set for a droplet — encoder and decoder derive the SAME set."""
    rng = random.Random(seed)
    d = _degree(rng, dist, K)
    return sorted(rng.sample(range(K), d))


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def encode(data, block_size=48, overhead=1.7, extra=4):
    blocks, total, K = _blocks(data, block_size)
    dist = _robust_soliton(K)
    n = int(K * overhead) + extra
    droplets = []
    for seed in range(n):
        idx = _indices(seed, K, dist)
        pl = bytes(block_size)
        for ix in idx:
            pl = _xor(pl, blocks[ix])
        droplets.append(HDR.pack(MAGIC, K, total, seed) + pl)
    return droplets


def decode(droplets):
    """Peeling decoder. Returns the exact payload, or None if not enough droplets."""
    parsed, K, total, B = [], None, None, None
    for d in droplets:
        if len(d) < HDR.size or d[0] != MAGIC:
            continue
        m, k, tot, seed = HDR.unpack(d[:HDR.size])
        K, total, B = k, tot, len(d) - HDR.size
        parsed.append((seed, bytearray(d[HDR.size:])))
    if K is None:
        return None
    dist = _robust_soliton(K)
    entries = [[set(_indices(seed, K, dist)), pl] for seed, pl in parsed]
    known = {}
    progress = True
    while len(known) < K and progress:
        progress = False
        for e in entries:
            idx, pl = e
            for ix in list(idx & known.keys()):
                pl = bytearray(_xor(pl, known[ix]))
                idx.discard(ix)
            e[0], e[1] = idx, pl
            if len(idx) == 1:
                ix = next(iter(idx))
                if ix not in known:
                    known[ix] = bytes(pl)
                    progress = True
    if len(known) < K:
        return None
    return b"".join(known[i] for i in range(K))[:total]


def cmd_selftest():
    fails = [0]

    def chk(name, cond):
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        if not cond:
            fails[0] += 1

    msg = ("Blob Heavyside, memory-transfer test: this exact line rides a holographic "
           "fountain over LoRa. Any ~K droplets rebuild it byte-for-byte — no fragment is "
           "special, order-free, drops-tolerant. 73 -Clem").encode()
    B = 48
    droplets = encode(msg, block_size=B)
    K = math.ceil(len(msg) / B)
    print(f"  payload {len(msg)} B -> {K} blocks -> {len(droplets)} droplets "
          f"({len(droplets[0])} B each = 1 MeshSpeak fragment)")

    chk("full set decodes byte-exact", decode(droplets) == msg)

    rng = random.Random(1)
    sub = rng.sample(droplets, math.ceil(K * 1.45))          # a RANDOM subset, others lost
    chk(f"random subset ({len(sub)}/{len(droplets)}) decodes byte-exact", decode(sub) == msg)

    dropped = [d for i, d in enumerate(droplets) if i % 4 != 0]   # drop every 4th fragment
    chk(f"every-4th-fragment dropped ({len(dropped)}/{len(droplets)}) still exact",
        decode(dropped) == msg)

    too_few = droplets[:max(1, K - 2)]
    chk("too few droplets -> clean None (not a wrong answer)", decode(too_few) in (None, msg) and decode(too_few) != msg)

    print("\nALL TESTS PASSED" if fails[0] == 0 else f"\n{fails[0]} FAILURE(S)")
    return 1 if fails[0] else 0


if __name__ == "__main__":
    sys.exit(cmd_selftest())
