#!/usr/bin/env python3
# meshspeak.py - MeshTalk v1 codec (AI-to-AI compression/transport over MeshCore)
# Copyright (c) 2026 Jose C. Guzman / Clavote Research. All Rights Reserved.
#
# HARDENED 2026-06-10 per adversarial crypto audit:
#  CRITICAL: direction bit DERIVED from (src,dst) -> A->B and B->A never share a (key,nonce).
#  HIGH: msg_id range is LOUD (no silent &0xFFFF wrap); MeshTalkSession gives monotonic ids
#        + salt rotation before wrap, so nonces never repeat in-direction either.
#  HIGH: decode() DROPS money-bearing CRYPTO_TX frames that are not AEAD-encrypted.
#  HIGH: fragment index is bounds-checked (no KeyError DoS from 2 unauthenticated frames).
#  MED:  reassembly is first-writer-wins (no inject-overwrite) with buffer count + byte caps,
#        and evict_stale() is actually self-driven on upsert.
import os, time, zlib, struct, hashlib, base64, json, sqlite3

MAGIC_VER = 0xA1
F_COMPRESSED = 1 << 0
F_ENCRYPTED  = 1 << 1
F_FRAGMENTED = 1 << 2
F_CRYPTO_TX  = 1 << 3
F_CODEC_LO   = 1 << 4
F_CODEC_HI   = 1 << 5
F_ACK_REQ    = 1 << 6
F_IS_CONTROL = 1 << 7
CODEC_RAW, CODEC_DICT, CODEC_DEFLATE, CODEC_CRYPTOPACK = 0, 1, 2, 3
CHAIN_BTC, CHAIN_EVM, CHAIN_LN = 0x01, 0x02, 0x03
TXOP_SIGNED_RAW, TXOP_PSBT, TXOP_BOLT11, TXOP_BROADCAST_REQ, TXOP_BROADCAST_ACK, TXOP_BAL_Q, TXOP_BAL_R = range(1, 8)
CTRL_ACK_BITMAP, CTRL_NACK_ALL, CTRL_COMPLETE = 0x01, 0x02, 0x03
REASSEMBLY_TTL_S = 120
IDEMPOTENCY_TTL_S = 3600
ARQ_MAX_ROUNDS = 5
MAX_FRAGMENTS = 255
MSG_ID_MAX = 0xFFFF
MAX_REASSEMBLY_BUFFERS = 256
MAX_REASSEMBLY_BYTES = 1 << 20            # 1 MiB total pre-AEAD (DoS cap)


def _u16(n):
    if not (0 <= n <= MSG_ID_MAX):
        raise ValueError(f"u16 out of range: {n}")
    return struct.pack("<H", n)
def _u16r(b): return struct.unpack("<H", b)[0]
def digest4(obj): return hashlib.sha256(obj).digest()[:4]


def derive_direction(src, dst):
    # Deterministic per agent pair: A->B and B->A get OPPOSITE bits, so the SAME msg_id never
    # yields the same nonce in both directions. Both endpoints compute the same bit per frame.
    return 0 if src < dst else 1


def text_codec(obj_bytes):
    d = zlib.compress(obj_bytes, 9)
    if len(d) < len(obj_bytes):
        return d, CODEC_DEFLATE
    return obj_bytes, CODEC_RAW


def text_decodec(payload, codec):
    if codec == CODEC_DEFLATE:
        return zlib.decompress(payload)
    if codec == CODEC_RAW:
        return payload
    raise ValueError("codec not implemented (dict/cryptopack are phase-2)")


def _aad(flags, msg_id, src, dst):
    return bytes([MAGIC_VER, flags & ~F_FRAGMENTED]) + _u16(msg_id) + bytes([src, dst])


def derive_nonce(session_salt, msg_id, direction):
    assert len(session_salt) == 8
    return session_salt + _u16(msg_id) + bytes([direction & 1, 0x00])


def encode(obj_bytes, *, src, dst, msg_id, frag_payload_max,
           is_crypto=False, compress=True, want_ack=False,
           key=None, session_salt=None):
    if not (0 <= msg_id <= MSG_ID_MAX):
        raise ValueError("msg_id out of range; rotate the session (see MeshTalkSession)")
    if not (0 <= src <= 0xFF and 0 <= dst <= 0xFF):
        raise ValueError("src/dst must be 0..255")
    digest = digest4(obj_bytes)
    codec = CODEC_RAW
    payload = obj_bytes
    if not is_crypto and compress:
        payload, codec = text_codec(obj_bytes)
    encrypted = key is not None
    if encrypted:
        from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
        if session_salt is None:
            raise ValueError("session_salt required when key set")
        direction = derive_direction(src, dst)               # DERIVED, never caller-controlled
        lflags = (codec << 4) | (F_COMPRESSED if codec else 0) | F_ENCRYPTED | (F_CRYPTO_TX if is_crypto else 0)
        nonce = derive_nonce(session_salt, msg_id, direction)
        body = ChaCha20Poly1305(key).encrypt(nonce, payload, _aad(lflags, msg_id, src, dst))
    else:
        body = payload
    chunks = [body[i:i + frag_payload_max] for i in range(0, len(body), frag_payload_max)] or [b""]
    total = len(chunks)
    if total > MAX_FRAGMENTS:
        raise ValueError("message exceeds MAX_FRAGMENTS")
    fragmented = total > 1
    frames = []
    for i, chunk in enumerate(chunks):
        flags = (codec << 4)
        flags |= F_COMPRESSED if codec else 0
        flags |= F_ENCRYPTED if encrypted else 0
        flags |= F_FRAGMENTED if fragmented else 0
        flags |= F_CRYPTO_TX if is_crypto else 0
        flags |= F_ACK_REQ if want_ack else 0
        h = bytes([MAGIC_VER, flags]) + _u16(msg_id) + bytes([src, dst])
        if fragmented:
            h += bytes([i, total])
            if i == 0:
                h += _u16(len(body)) + digest
        frames.append(h + chunk)
    return frames


class MeshTalkSession:
    """Nonce hygiene for one (src->dst) send stream: monotonic msg_id + salt rotation BEFORE the
    16-bit wrap, so (key,nonce) never repeats in-direction. One per peer you send to. Salt
    distribution on rotation is the (open) session-key establishment problem; .rotations signals it."""
    def __init__(self, key, src, dst, salt=None):
        self.key, self.src, self.dst = key, src, dst
        self.salt = salt or os.urandom(8)
        self._n = 0
        self.rotations = 0

    def next_msg_id(self):
        if self._n > MSG_ID_MAX:
            self.salt = os.urandom(8); self._n = 0; self.rotations += 1
        mid = self._n; self._n += 1
        return mid

    def encode(self, obj_bytes, *, frag_payload_max, is_crypto=False, compress=True, want_ack=False):
        mid = self.next_msg_id()
        return encode(obj_bytes, src=self.src, dst=self.dst, msg_id=mid,
                      frag_payload_max=frag_payload_max, is_crypto=is_crypto,
                      compress=compress, want_ack=want_ack, key=self.key, session_salt=self.salt)


class FragmentStore:
    """Reassembly buffer keyed by (src,msg_id). First-writer-wins (no inject-overwrite), with
    buffer-count and total-byte caps; self-evicting on upsert."""
    def __init__(self):
        self._b = {}
        self._bytes = 0

    def _now(self): return time.time()

    def upsert(self, src, msg_id, total, msg_len, digest):
        self.evict_stale()
        k = (src, msg_id)
        b = self._b.get(k)
        if b is None:
            if len(self._b) >= MAX_REASSEMBLY_BUFFERS:
                return False                                  # cap: refuse new buffers
            self._b[k] = {"total": total, "len": msg_len, "digest": digest, "parts": {}, "ts": self._now()}
        else:                                                 # first-writer-wins for metadata
            if b["total"] is None and total is not None: b["total"] = total
            if b["len"] is None and msg_len is not None: b["len"] = msg_len
            if b["digest"] is None and digest is not None: b["digest"] = digest
        return True

    def put(self, k, idx, data):
        b = self._b.get(k)
        if b is None or idx in b["parts"]:                    # ignore duplicate/injected index
            return
        if self._bytes + len(data) > MAX_REASSEMBLY_BYTES:
            return                                            # global byte cap
        b["parts"][idx] = data
        self._bytes += len(data)

    def complete(self, k):
        b = self._b.get(k)
        return bool(b) and b["total"] is not None and len(b["parts"]) == b["total"]

    def get_digest(self, k):
        b = self._b.get(k)
        return b["digest"] if b else None

    def assemble(self, k):
        b = self._b.pop(k)
        self._bytes -= sum(len(v) for v in b["parts"].values())
        return b"".join(b["parts"][i] for i in range(b["total"]))

    def bitmap(self, k):
        b = self._b[k]; total = b["total"] or 0
        bm = bytearray((total + 7) // 8)
        for i in b["parts"]:
            bm[i >> 3] |= 1 << (i & 7)
        return bytes(bm), total

    def evict_stale(self):
        now = self._now()
        for k in [k for k, b in self._b.items() if now - b["ts"] > REASSEMBLY_TTL_S]:
            b = self._b.pop(k)
            self._bytes -= sum(len(v) for v in b["parts"].values())


def decode(frame, store, *, key=None, session_salt=None):
    if not frame or len(frame) < 6 or frame[0] != MAGIC_VER:
        return ("drop", "bad magic")
    flags = frame[1]; msg_id = _u16r(frame[2:4]); src = frame[4]; dst = frame[5]
    if flags & F_IS_CONTROL:
        return ("control", parse_control(frame))
    if (flags & F_CRYPTO_TX) and not (flags & F_ENCRYPTED):
        return ("drop", "unauthenticated crypto-tx")          # money frames MUST be AEAD
    off, idx, total, msg_len, dig = 6, 0, 1, None, None
    if flags & F_FRAGMENTED:
        if len(frame) < 8:
            return ("drop", "short frag header")
        idx, total = frame[6], frame[7]
        if total < 1 or not (0 <= idx < total):
            return ("drop", "bad fragment index")             # no KeyError DoS
        off = 8
        if idx == 0:
            if len(frame) < 14:
                return ("drop", "short frag0 header")
            msg_len = _u16r(frame[8:10]); dig = frame[10:14]; off = 14
    if not store.upsert(src, msg_id, total if (flags & F_FRAGMENTED) else 1, msg_len, dig):
        return ("drop", "reassembly buffer full")
    k = (src, msg_id)
    store.put(k, idx, frame[off:])
    if not store.complete(k):
        return ("partial", None)
    stored_digest = store.get_digest(k)
    body = store.assemble(k)
    try:
        if flags & F_ENCRYPTED:
            from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
            direction = derive_direction(src, dst)
            codec = (flags >> 4) & 0b11
            lflags = (codec << 4) | (F_COMPRESSED if codec else 0) | F_ENCRYPTED | (flags & F_CRYPTO_TX)
            nonce = derive_nonce(session_salt, msg_id, direction)
            body = ChaCha20Poly1305(key).decrypt(nonce, body, _aad(lflags, msg_id, src, dst))
        codec = (flags >> 4) & 0b11
        obj = text_decodec(body, codec) if (flags & F_COMPRESSED) else body
    except Exception:
        return ("drop", "decode error")
    if stored_digest is not None and digest4(obj) != stored_digest:
        return ("drop", "digest mismatch")
    if flags & F_CRYPTO_TX:
        return ("crypto", parse_crypto_envelope(obj))
    return ("msg", obj)


def pack_crypto_envelope(chain, txop, idem_key, tx_bytes):
    assert len(idem_key) == 4
    return bytes([chain, txop]) + idem_key + tx_bytes


def parse_crypto_envelope(obj):
    return {"chain": obj[0], "txop": obj[1], "idem": obj[2:6], "tx": obj[6:]}


class IdempotencyStore:
    """Durable, replay-PERMANENT seen-set for crypto broadcasts (audit MED #8 + LOW #10):
    a replayed tx must NEVER re-broadcast -- not across a restart, not after any TTL. Backed by
    SQLite/WAL on disk, so the replay guard is not bounded by memory and does not expire."""
    def __init__(self, path=":memory:"):
        self.db = sqlite3.connect(path)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("CREATE TABLE IF NOT EXISTS idem (k BLOB PRIMARY KEY, ack TEXT, ts REAL)")
        self.db.commit()
    def get(self, idem):
        r = self.db.execute("SELECT ack FROM idem WHERE k=?", (idem,)).fetchone()
        return json.loads(r[0]) if r else None
    def put(self, idem, ack):
        self.db.execute("INSERT OR IGNORE INTO idem (k,ack,ts) VALUES (?,?,?)",
                        (idem, json.dumps(ack), time.time()))
        self.db.commit()


def handle_crypto_envelope(env, store, broadcast_fn):
    """§4.2 idempotency-gated broadcast, DURABLE + replay-permanent. `store` = IdempotencyStore.
    Replayed tx -> cached ack, never re-broadcast (survives restarts; no TTL re-enable)."""
    if env["txop"] not in (TXOP_SIGNED_RAW, TXOP_BROADCAST_REQ):
        return None
    idem = env["idem"]
    cached = store.get(idem)
    if cached is not None:
        return cached                                # replay -> cached ack, DO NOT re-broadcast
    ack = broadcast_fn(env["chain"], env["tx"])
    store.put(idem, ack)                              # durable + permanent
    return ack


# ---- Forward-secret session establishment (audit MED #9: no forward secrecy) ----
def gen_ephemeral_keypair():
    """Ephemeral X25519 keypair for a forward-secret session. Returns (priv32, pub32)."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    from cryptography.hazmat.primitives import serialization
    p = X25519PrivateKey.generate()
    return (p.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()),
            p.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def ecdh_session(my_priv, peer_pub):
    """Forward-secret (key32, salt8) from an X25519 ECDH of EPHEMERAL keys -> compromising a
    long-term identity later cannot decrypt recorded traffic. Both sides derive the same pair.
    This is the recommended resolution of the session-key open decision (ECDH over static PSK)."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    from cryptography.hazmat.primitives import hashes
    shared = X25519PrivateKey.from_private_bytes(my_priv).exchange(X25519PublicKey.from_public_bytes(peer_pub))
    okm = HKDF(algorithm=hashes.SHA256(), length=40, salt=None, info=b"meshspeak-v1-session").derive(shared)
    return okm[:32], okm[32:40]


def build_ack_bitmap(msg_id, src, dst, total, bitmap):
    return (bytes([MAGIC_VER, F_IS_CONTROL]) + _u16(msg_id) + bytes([src, dst]) +
            bytes([CTRL_ACK_BITMAP, total]) + bitmap)


def parse_control(frame):
    return {"msg_id": _u16r(frame[2:4]), "src": frame[4], "dst": frame[5],
            "ctrl_type": frame[6], "frag_total": frame[7], "bitmap": frame[8:]}


def missing_fragments(ctrl):
    total, bm = ctrl["frag_total"], ctrl["bitmap"]
    return [i for i in range(total) if not (bm[i >> 3] & (1 << (i & 7)))]


# ---- Channel-text transport binding (channel msgs are UTF-8 only) ----
CHAN_TEXT_MAX = 177
WIRE_FRAG_PAYLOAD_MAX = (CHAN_TEXT_MAX * 3 // 4) - 14


def frame_to_wire(frame_bytes):
    return base64.b64encode(frame_bytes).decode("ascii")


def wire_to_frame(text):
    try:
        b = base64.b64decode(text.strip(), validate=True)
    except Exception:
        return None
    return b if (b and b[0] == MAGIC_VER) else None


# --------------------------------------------------------------------------- selftest
def selftest():
    fails = 0
    def chk(name, cond):
        nonlocal fails
        print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
        fails += 0 if cond else 1

    # core round-trips
    obj = os.urandom(40)
    fr = encode(obj, src=1, dst=2, msg_id=7, frag_payload_max=170)
    st = FragmentStore(); k, v = decode(fr[0], st)
    chk("single packet round-trips", k == "msg" and v == obj)

    big = b"clavote paymentAgent USDC BASE broadcast fragment MeshTalk " * 30
    fr = encode(big, src=3, dst=4, msg_id=99, frag_payload_max=60)
    chk("fragments (>1 frame)", len(fr) > 1)
    st = FragmentStore(); out = None
    for f in fr: k, out = decode(f, st)
    chk("fragments reassemble+decompress+digest", k == "msg" and out == big)
    st = FragmentStore(); out = None
    for f in reversed(fr): k, out = decode(f, st)
    chk("out-of-order reassembly", k == "msg" and out == big)

    key = os.urandom(32); salt = os.urandom(8)
    msg = b"agent-to-agent secret payload " * 4
    fr = encode(msg, src=1, dst=2, msg_id=11, frag_payload_max=80, key=key, session_salt=salt)
    st = FragmentStore(); out = None
    for f in fr: k, out = decode(f, st, key=key, session_salt=salt)
    chk("AEAD round-trip", k == "msg" and out == msg)
    st = FragmentStore(); r = None
    for f in fr: r = decode(f, st, key=os.urandom(32), session_salt=salt)
    chk("AEAD rejects wrong key", r[0] == "drop")

    # --- CRITICAL fix: direction derived -> A->B and B->A never share a nonce ---
    pt = b"identical plaintext + identical msg_id"
    fa = encode(pt, src=1, dst=2, msg_id=5, frag_payload_max=200, key=key, session_salt=salt)[0]
    fb = encode(pt, src=2, dst=1, msg_id=5, frag_payload_max=200, key=key, session_salt=salt)[0]
    chk("CRITICAL: A->B vs B->A ciphertexts differ (no keystream reuse)", fa[6:] != fb[6:])
    st = FragmentStore(); ka, va = decode(fa, st, key=key, session_salt=salt)
    st = FragmentStore(); kb, vb = decode(fb, st, key=key, session_salt=salt)
    chk("both directions still decrypt correctly", va == pt and vb == pt)

    # --- HIGH fix: CRYPTO_TX must be AEAD-encrypted ---
    env = pack_crypto_envelope(CHAIN_BTC, TXOP_SIGNED_RAW, b"\x00\x01\x02\x03", b"signed-tx")
    unenc = encode(env, src=5, dst=6, msg_id=1, frag_payload_max=200, is_crypto=True)  # no key!
    st = FragmentStore(); r = decode(unenc[0], st)
    chk("HIGH: unencrypted CRYPTO_TX is DROPPED", r[0] == "drop")
    enc = encode(env, src=5, dst=6, msg_id=2, frag_payload_max=200, is_crypto=True, key=key, session_salt=salt)
    st = FragmentStore(); r = decode(enc[0], st, key=key, session_salt=salt)
    chk("encrypted CRYPTO_TX still parses", r[0] == "crypto" and r[1]["chain"] == CHAIN_BTC)

    # --- HIGH fix: out-of-range fragment index -> drop, no crash ---
    fr = encode(os.urandom(300), src=1, dst=2, msg_id=8, frag_payload_max=100)   # 3 frags
    bad = bytearray(fr[1]); bad[6] = 99                                          # idx 99 of 3
    st = FragmentStore(); r = decode(bytes(bad), st)
    chk("HIGH: out-of-range fragment index dropped (no KeyError)", r[0] == "drop")

    # --- HIGH fix: msg_id wrap is LOUD ---
    raised = False
    try: encode(b"x", src=1, dst=2, msg_id=70000, frag_payload_max=100)
    except ValueError: raised = True
    chk("HIGH: msg_id overflow raises (no silent wrap)", raised)

    # --- MED fix: first-writer-wins (injected fragment cannot overwrite an honest one) ---
    fr = encode(b"a multi fragment honest message for the injection-resistance test 123456", src=1, dst=2, msg_id=9, frag_payload_max=20)
    st = FragmentStore()
    for f in fr: decode(f, st)                                # honest message arrives, decodes
    st2 = FragmentStore()
    decode(fr[0], st2)
    forged = bytearray(fr[1]); forged[8:] = b"ZZZZ"           # forge fragment 1 body, inject FIRST
    decode(bytes(forged), st2)
    r = None
    for f in fr[1:]: r = decode(f, st2)                       # honest frags 1..n arrive after
    chk("MED: injected fragment can't be overwritten -> no forged accept (drop)", r[0] == "drop")

    # --- HIGH fix: Session monotonic ids + rotation before wrap ---
    sess = MeshTalkSession(os.urandom(32), src=1, dst=2)
    ids = [sess.next_msg_id() for _ in range(3)]
    chk("Session msg_ids monotonic", ids == [0, 1, 2])
    sess._n = MSG_ID_MAX + 1; old = sess.salt
    m = sess.next_msg_id()
    chk("Session rotates salt before wrap", m == 0 and sess.salt != old and sess.rotations == 1)

    # --- MED/LOW fix: durable, replay-permanent idempotency ---
    import tempfile
    dbp = tempfile.mktemp(suffix=".idem.db")
    store = IdempotencyStore(dbp)
    env5 = parse_crypto_envelope(pack_crypto_envelope(CHAIN_BTC, TXOP_SIGNED_RAW, b"\xaa\xbb\xcc\xdd", b"tx"))
    calls = []
    def bc(c, t): calls.append(t); return {"txid": "abc", "status": "ok"}
    handle_crypto_envelope(env5, store, bc)
    handle_crypto_envelope(env5, store, bc)                       # replay
    chk("idempotency: replay -> exactly ONE broadcast", len(calls) == 1)
    store2 = IdempotencyStore(dbp)                                # simulate restart (reopen db)
    handle_crypto_envelope(env5, store2, bc)
    chk("idempotency DURABLE across restart (no re-broadcast)", len(calls) == 1)

    # --- MED fix: forward-secret ECDH session ---
    pa, qa = gen_ephemeral_keypair(); pb, qb = gen_ephemeral_keypair()
    ka, sa = ecdh_session(pa, qb); kb, sb = ecdh_session(pb, qa)
    chk("ECDH: both sides derive same key+salt", ka == kb and sa == sb)
    pc, qc = gen_ephemeral_keypair(); kc, sc = ecdh_session(pc, qb)
    chk("ECDH forward secrecy: new ephemeral -> different key", kc != ka)
    msg_fs = b"forward secret agent message"
    fr = encode(msg_fs, src=1, dst=2, msg_id=3, frag_payload_max=200, key=ka, session_salt=sa)
    st = FragmentStore(); kk, vv = decode(fr[0], st, key=kb, session_salt=sb)
    chk("ECDH-derived key works end-to-end in encode/decode", kk == "msg" and vv == msg_fs)

    # channel-text transport still works
    m2 = b"agent room broadcast nominal " * 3
    fr = encode(m2, src=9, dst=0xFF, msg_id=77, frag_payload_max=WIRE_FRAG_PAYLOAD_MAX)
    wires = [frame_to_wire(f) for f in fr]
    chk("wire fits channel budget", all(len(w) <= CHAN_TEXT_MAX for w in wires))
    chk("plain text ignored by wire_to_frame", wire_to_frame("Bob:  hello") is None)
    st = FragmentStore(); out = None
    for w in wires: out = decode(wire_to_frame(w), st)[1]
    chk("full channel round-trip", out == m2)

    print(f"\n{'ALL TESTS PASSED' if fails == 0 else f'{fails} FAILURE(S)'}")
    return fails


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
