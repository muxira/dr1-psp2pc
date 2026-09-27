#!/usr/bin/env python3
"""dr1-save-bridge: Danganronpa: Trigger Happy Havoc PSP (NPJH50515) -> Steam PC save converter.

Builds a complete PC save slot from a PSP `DATKG.BIN`.  Every byte of the PC game
state is either transferred from the PSP save through the offset map below or set
to the value a fresh PC game uses, so no PC save is needed as a template.  Pure
standard library, no dependencies.  docs/FORMAT.md explains how every offset was
established.
"""
import argparse
import datetime
import hashlib
import math
import os
import shutil
import struct
import sys
from collections import Counter

__version__ = "2.0.0"

# --------------------------------------------------------------- container ----
VFS_FOOTER = 0xDEADBEEF
ENTRY_HDR = 0x2E4                 # slot header in front of the core
TITLE = "Danganronpa: Trigger Happy Havoc"

# ------------------------------------------------------------------- PSP ------
PSP_ENC_HEAD = 0x10                # savedata IV prefix in front of the ciphertext
PSP_LEN = 0x19698                  # encrypted DATKG.BIN == decrypted core size
PSP_HDR = 0x20                     # header size; the body follows
PSP_STATE = PSP_LEN - PSP_HDR - 4  # 0x19674 byte body: magic, 4 pad, game state
PSP_BODY = PSP_HDR                 # body offset inside the core
PSP_TAIL_SUM = PSP_LEN - 4
PSP_HDR_SUM = 0x1C
PSP_MAGIC = 0x20                   # second magic, start of the body
PSP_DIFFICULTY = 0x07              # header: 2 difficulty bytes, same encoding as PC
PSP_CLEARED = 0x14                 # header: u32 chapter-clear mask
PSP_FLAGS = 0x18                   # header: u32 completion flags

# body offsets, verified against PARAM.SFO and the PC build
PSP_CHAPTER = 0x08                 # u8  0 = prologue, 7 = epilogue
PSP_SCENE = 0x2A                   # u16 current text id
PSP_SCRIPT = 0x2C                  # u16 script offset of the current text
PSP_COINS = 0x15C                  # u16 Monocoins
PSP_SAVES = 0x192BC                # u32 "number of times saved"

PSP_GS = 0x08                      # game state inside the body
PSP_GS_LEN = 0x1966C
# Message log inside the PSP game state.  The original build keeps 0x200 rows of
# 54 chars; the Russian fan translation writes 66 char rows from the same bases.
PSP_LOG = dict(flag=0x634, head=0x636, write=0x638, meta=0x63A, text=0xC3A,
               attr=0xE43A, end=0x1503A, rows=0x200, chars=(54, 66))

# -------------------------------------------------------------------- PC ------
PC_LEN = 0x3A9B0                   # core size
PC_HDR = 0x3C
PC_STATE = PC_LEN - PC_HDR - 4     # 0x3A970 byte body
PC_MAGIC = 0x3C                    # second magic, start of the body
PC_HDR_SUM = 0x38                  # u32 byte sum of core[0x00 .. 0x37]
PC_TAIL_SUM = PC_LEN - 4           # u32 byte sum of core[0x3C .. 0x3A9AB]
PC_OPTIONS = 0x04                  # 8 option bytes, bytes 3..4 are the difficulty
PC_TIME = 0x10                     # u8 seconds, minutes, hours, pad
PC_DAYS = 0x14                     # u32, extra 24 h blocks
PC_CLEARED = 0x18                  # u32 chapter-clear mask
PC_SAVES = 0x1C                    # u32
PC_FLAGS = 0x20                    # u32 completion flags

# body offsets
PC_CHAPTER = 0x08
PC_SCENE = 0x36
PC_SCRIPT = 0x38
PC_COINS = 0x170

PC_GS = 0x08                       # main game state inside the body
PC_GS_LEN = 0x1D3DC
PC_SCHOOL = PC_GS + PC_GS_LEN      # School Mode game state, PC only
PC_SYSTEM = PC_SCHOOL + PC_GS_LEN  # 0x3A7C0: system data (unlocks), PC only
PC_SYSTEM_LEN = PC_STATE - PC_SYSTEM
PC_LOG = dict(flag=0x648, head=0x64A, write=0x64C, meta=0x64E, text=0xC4E,
              attr=0x10C4E, end=0x18C4E, rows=0x200, chars=64)
GS_IP = 0x18                       # u32 script instruction pointer
GS_TEXT_IP = 0x30                  # u16 script offset of the current text
GS_SAVE_FLAGS = 0x34               # the PC save routine sets bit 0x80 here

MAGIC = bytes.fromhex("6495ae16")

# Complete map of the PC game state (0x1D3DC bytes) onto the PSP one (0x1966C).
# (pc_lo, pc_hi, kind, psp_lo), offsets relative to the game state.  The rows tile
# the whole PC range; docs/FORMAT.md explains how every row was established.
#   copy    bytes copied verbatim from psp_lo
#   widen   u8 fields on PSP, u16 on PC
#   log     message log, rebuilt (language specific)
#   window  text window state, set to the PC idle value
#   zero    PC-only or language specific bytes, zero in a fresh PC game
STATE_MAP = (
    (0x00000, 0x00018, "widen", 0x00000),   # chapter, scene, variant, call stack
    (0x00018, 0x00140, "copy", 0x0000C),    # script state, flags, flag words 0-1
    (0x00140, 0x00148, "zero", None),       # flag words 2-3
    (0x00148, 0x00648, "copy", 0x00134),    # items, Monocoins, progress tables
    (0x00648, 0x18C4E, "log", None),        # message log
    (0x18C4E, 0x18C50, "copy", 0x1503A),
    (0x18C50, 0x19010, "zero", None),       # text window, 5 rows of text + attr
    (0x19010, 0x19018, "window", None),     # text window state
    (0x19018, 0x194E6, "copy", 0x1536E),    # choice menu state
    (0x194E6, 0x194E8, "zero", None),       # alignment padding
    (0x194E8, 0x1954A, "copy", 0x1583C),    # trial state
    (0x1954A, 0x1A63E, "copy", 0x1589E),    # table A, records 0-216
    (0x1A63E, 0x1A6A2, "zero", None),       # table A, records 217-221
    (0x1A6A2, 0x1B796, "copy", 0x16992),    # table B, records 0-216
    (0x1B796, 0x1B7FA, "zero", None),       # table B, records 217-221
    (0x1B7FA, 0x1BF90, "copy", 0x17A86),    # tables C and D, current record
    (0x1BF90, 0x1BF94, "zero", None),       # current record, last field
    (0x1BF94, 0x1D02C, "copy", 0x1821C),    # trial records, per chapter records
    (0x1D02C, 0x1D03C, "copy", 0x192B8),
    (0x1D03C, PC_GS_LEN, "zero", None),     # PC-only counters, reserved
)

# Values a fresh PC game uses for data the PSP build does not have, taken from a
# new-game PC save: option bytes, text window state, School Mode game state and
# system block.  Run-length encoded as (offset, value, count).
PC_OPTION_DEFAULTS = bytes((0x32, 0x46, 0x64, 0x02, 0x02, 0x01, 0x02, 0x00))
WINDOW_IDLE = bytes((0x02, 0, 0, 0, 0, 0, 0, 0))
SCHOOL_DEFAULT = (
    (0x36, 0xff, 1), (0x3b, 0xff, 1), (0x40, 0xff, 1), (0x45, 0xff, 1),
    (0x4a, 0xff, 1), (0x4f, 0xff, 1), (0x54, 0xff, 1), (0x59, 0xff, 1),
    (0x5e, 0xff, 1), (0x63, 0xff, 1), (0x68, 0xff, 1), (0x6d, 0xff, 1),
    (0x72, 0xff, 1), (0x77, 0xff, 1), (0x7c, 0xff, 1), (0x81, 0xff, 1),
    (0x86, 0xff, 1), (0x8b, 0xff, 1), (0x90, 0xff, 1), (0x95, 0xff, 1),
    (0x9a, 0xff, 1), (0x9b, 0x1, 1), (0x9c, 0xff, 1), (0x9d, 0x1, 1), (0x9e, 0xff, 1),
    (0x9f, 0x1, 1), (0xa0, 0xff, 1), (0xa1, 0x1, 1), (0xa2, 0xff, 1), (0xa3, 0x1, 1),
    (0xa4, 0xff, 1), (0xa5, 0x1, 1), (0xa6, 0xff, 1), (0xa7, 0x1, 1), (0xa8, 0xff, 1),
    (0xa9, 0x1, 1), (0xaa, 0xff, 1), (0xab, 0x1, 1), (0xac, 0xff, 1), (0xad, 0x1, 1),
    (0xae, 0xff, 1), (0xaf, 0x1, 1), (0xb0, 0xff, 1), (0xb1, 0x1, 1), (0xb2, 0xff, 1),
    (0xb3, 0x1, 1), (0xb4, 0xff, 1), (0xb5, 0x1, 1), (0xb6, 0xff, 1), (0xb7, 0x1, 1),
    (0xb8, 0xff, 1), (0xb9, 0x1, 1), (0xba, 0xff, 1), (0xbb, 0x1, 1), (0xbc, 0xff, 1),
    (0xbd, 0x1, 1), (0xbe, 0xff, 1), (0xbf, 0x1, 1), (0xc0, 0xff, 1), (0xc1, 0x1, 1),
    (0xc2, 0xff, 1), (0xc3, 0x1, 1), (0xc4, 0xff, 1), (0xc5, 0x1, 1), (0xc6, 0xff, 1),
    (0xc7, 0x1, 1), (0xc8, 0xff, 1), (0xc9, 0x1, 1), (0xca, 0xff, 1), (0xcb, 0x1, 1),
    (0xcc, 0xff, 1), (0xcd, 0x1, 1), (0xce, 0xff, 1), (0xcf, 0x1, 1), (0xd0, 0xff, 1),
    (0xd1, 0x1, 1), (0xd2, 0xff, 1), (0xd3, 0x1, 1), (0xd4, 0xff, 1), (0xd5, 0x1, 1),
    (0xd6, 0xff, 3), (0xda, 0xff, 3), (0xdf, 0xff, 1), (0xe2, 0xff, 1),
    (0xed, 0xff, 1), (0xf3, 0xff, 2), (0x138, 0x4, 1), (0x13f, 0x30, 1),
    (0x160, 0x9, 1), (0x16c, 0x2, 1), (0x16e, 0x2, 1), (0x230, 0xff, 100),
    (0x297, 0x6, 1), (0x2a3, 0x6, 1), (0x2cb, 0xff, 1), (0x649, 0xff, 1),
    (0x1bf78, 0xff, 8), (0x1cfa8, 0x1, 2),
)
SYSTEM_DEFAULT = (
    (0x4, 0x1, 30), (0x22, 0xff, 160), (0xef, 0x64, 30), (0x110, 0x1, 1),
    (0x112, 0xff, 1), (0x114, 0x64, 1), (0x115, 0xff, 1), (0x18a, 0x64, 1),
    (0x19a, 0x4, 1), (0x19c, 0x2, 1),
)

CHAPTER_LABELS = {
    0: "Prologue", 1: "Chapter 01", 2: "Chapter 02", 3: "Chapter 03",
    4: "Chapter 04", 5: "Chapter 05", 6: "Chapter 06", 7: "Epilogue",
}

# ------------------------------------------------------------------- keys ----
# No decryption key is shipped with this tool.  The user provides them in
# keys.txt (see keys.example.txt, or run `keys --import`); only SHA-256
# fingerprints are kept here, to tell the user which key is missing or wrong.
KEYS_FILE = "keys.txt"
GAME_KEY = "game_key"
CIPHER_KEYS = ("key19CC", "key19DC", "kirk_key_12", "kirk_key_64")
KEY_NAMES = (GAME_KEY,) + CIPHER_KEYS
KEY_FINGERPRINTS = {
    "game_key": "12630d87b98f4229a5282cc3ac203b7ebc09713aa6ac1b821cc5d9e6f0cd8d13",
    "key19CC": "b58242561b16a8925126dbae5558dda53d6c275d712ee69f5bebce37c577af2d",
    "key19DC": "a0bf478c1471e4115011e5e0f70d5e2d43c95e5ef2645d164b76ec3cd118ec10",
    "kirk_key_12": "22e830fa0ac9b99cfd0706c7d907429f88f1de4b3605088d09e76c2ece69133f",
    "kirk_key_64": "5855111a412d0aa6cf441adc1a80ea8db8a3fd8ba876e409b2dc10745775eeee",
}
GAME_KEY_RELEASE = "NPJH50515"         # release whose game key is fingerprinted


class BridgeError(Exception):
    pass


def sum8(b):
    return sum(b) & 0xFFFFFFFF


# ------------------------------------------------------- PSP savedata crypt ----
# The console encrypts every savedata block with a Kirk AES stream cipher keyed
# by a per-game key.  Reimplemented here in pure Python as a port of PPSSPP's
# Core/HLE/sceChnnlsv.cpp; byte identical to the reference implementation for
# every mode 5 block.
def _make_sbox():
    """The standard AES S-box: GF(2^8) inverse followed by the affine map."""
    box = bytearray(256)
    p = q = 1
    while True:
        p ^= ((p << 1) ^ (0x1B if p & 0x80 else 0)) & 0xFF      # p *= 3
        q ^= q << 1                                               # q /= 3
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ (q << 1 | q >> 7) ^ (q << 2 | q >> 6) ^ (q << 3 | q >> 5) ^ (q << 4 | q >> 4)
        box[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    box[0] = 0x63
    return bytes(box)


_SBOX = _make_sbox()
_INV_SBOX = bytes(_SBOX.index(bytes([i])) for i in range(256))
_MUL = {}
for _c in (9, 11, 13, 14):
    _t = []
    for _i in range(256):
        _r, _a, _b = 0, _i, _c
        while _b:
            if _b & 1:
                _r ^= _a
            _a = ((_a << 1) ^ 0x1B) & 0xFF if _a & 0x80 else _a << 1
            _b >>= 1
        _t.append(_r)
    _MUL[_c] = _t


def _xt(a):
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else a << 1


def _key_expand(key):
    w = [int.from_bytes(key[4 * i:4 * i + 4], "big") for i in range(4)]
    rcon = 1
    for i in range(4, 44):
        t = w[i - 1]
        if i % 4 == 0:
            t = ((t << 8) | (t >> 24)) & 0xFFFFFFFF
            t = (_SBOX[(t >> 24) & 0xFF] << 24) | (_SBOX[(t >> 16) & 0xFF] << 16) \
                | (_SBOX[(t >> 8) & 0xFF] << 8) | _SBOX[t & 0xFF]
            t ^= rcon << 24
            rcon = _xt(rcon)
        w.append(w[i - 4] ^ t)
    return w


def _add_round_key(s, w, rnd):
    for c in range(4):
        v = w[4 * rnd + c]
        for r in range(4):
            s[4 * c + r] ^= (v >> (24 - 8 * r)) & 0xFF


def _aes_decrypt_block(w, blk):
    s = bytearray(blk)
    _add_round_key(s, w, 10)
    for rnd in range(9, 0, -1):
        t = bytearray(s[4 * ((c - r) % 4) + r] for c in range(4) for r in range(4))
        for i in range(16):
            t[i] = _INV_SBOX[t[i]]
        _add_round_key(t, w, rnd)
        m9, m11, m13, m14 = _MUL[9], _MUL[11], _MUL[13], _MUL[14]
        for c in range(4):
            a0, a1, a2, a3 = t[4 * c:4 * c + 4]
            t[4 * c] = m14[a0] ^ m11[a1] ^ m13[a2] ^ m9[a3]
            t[4 * c + 1] = m9[a0] ^ m14[a1] ^ m11[a2] ^ m13[a3]
            t[4 * c + 2] = m13[a0] ^ m9[a1] ^ m14[a2] ^ m11[a3]
            t[4 * c + 3] = m11[a0] ^ m13[a1] ^ m9[a2] ^ m14[a3]
        s = t
    t = bytearray(s[4 * ((c - r) % 4) + r] for c in range(4) for r in range(4))
    for i in range(16):
        t[i] = _INV_SBOX[t[i]]
    _add_round_key(t, w, 0)
    return bytes(t)


def _kirk_cbc_decrypt(key, data):
    """Kirk AES-CBC: the first block is decrypted unchained, then CBC."""
    w = _key_expand(key)
    out = bytearray()
    prev = None
    for off in range(0, len(data) - 15, 16):
        blk = data[off:off + 16]
        d = _aes_decrypt_block(w, blk)
        out += d if prev is None else bytes(a ^ b for a, b in zip(d, prev))
        prev = blk
    return bytes(out)


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


def _kirk_member(data, off, length, iv, unkn, keys):
    """Decrypt one 0x800 byte (or shorter) member of a savedata block."""
    buf = bytearray(20 + length)
    buf[20:36] = _xor(iv, keys["key19DC"])
    buf[0:16] = _kirk_cbc_decrypt(keys["kirk_key_12"], bytes(buf[20:36]))
    buf[0:16] = _xor(buf[0:16], keys["key19CC"])
    seed = bytes(buf[0:16])
    pad = bytes(16) if unkn == 1 else seed[:12] + (unkn - 1).to_bytes(4, "little")
    for i in range(20, length + 20, 16):
        buf[i:i + 12] = seed[:12]
        buf[i + 12:i + 16] = unkn.to_bytes(4, "little")
        unkn += 1
    buf[0:length] = _kirk_cbc_decrypt(keys["kirk_key_64"], bytes(buf[20:20 + length]))
    buf[0:16] = _xor(buf[0:16], pad)
    for i in range(length):
        data[off + i] ^= buf[i]
    return unkn


def psp_decrypt(raw, keys):
    """Decrypt a mode 5 `DATKG.BIN` with the KEY_NAMES dict and return the plain core."""
    d = bytearray(raw)
    d += bytes(-len(d) % 16)
    iv = _xor(bytes(d[:16]), keys[GAME_KEY])
    unkn, i, n = 1, 0, len(d) - PSP_ENC_HEAD
    while n - i >= 2048:
        unkn = _kirk_member(d, PSP_ENC_HEAD + i, 2048, iv, unkn, keys)
        i += 2048
    if n - i:
        _kirk_member(d, PSP_ENC_HEAD + i, n - i, iv, unkn, keys)
    return bytes(d[PSP_ENC_HEAD:PSP_ENC_HEAD + len(raw) - PSP_ENC_HEAD])


# ------------------------------------------------------------- validation ----
def psp_ok(core):
    return (len(core) == PSP_LEN and core[:4] == MAGIC
            and core[PSP_MAGIC:PSP_MAGIC + 4] == MAGIC
            and struct.unpack_from("<I", core, PSP_HDR_SUM)[0] == sum8(core[:PSP_HDR_SUM])
            and struct.unpack_from("<I", core, PSP_TAIL_SUM)[0]
            == sum8(core[PSP_BODY:PSP_TAIL_SUM]))


def pc_ok(core):
    """Both checksums the PC build verifies, exactly as DR1_us.exe does."""
    return (len(core) == PC_LEN and core[:4] == MAGIC
            and core[PC_MAGIC:PC_MAGIC + 4] == MAGIC
            and struct.unpack_from("<I", core, PC_HDR_SUM)[0] == sum8(core[:PC_HDR_SUM])
            and struct.unpack_from("<I", core, PC_TAIL_SUM)[0]
            == sum8(core[PC_MAGIC:PC_TAIL_SUM]))


def pc_fix_sums(core):
    struct.pack_into("<I", core, PC_HDR_SUM, sum8(core[:PC_HDR_SUM]))
    struct.pack_into("<I", core, PC_TAIL_SUM, sum8(core[PC_MAGIC:PC_TAIL_SUM]))


def entry_ok(data):
    return (len(data) == ENTRY_HDR + PC_LEN
            and struct.unpack_from("<I", data, ENTRY_HDR - 4)[0] == PC_LEN
            and pc_ok(data[ENTRY_HDR:]))


# ---------------------------------------------------------------- loading ----
def entropy(b):
    c, n = Counter(b), len(b)
    return -sum(v / n * math.log2(v / n) for v in c.values())


def _hex16(text, what):
    try:
        k = bytes.fromhex(text.strip())
    except ValueError:
        raise BridgeError(f"{what}: not a hex string") from None
    if len(k) != 16:
        raise BridgeError(f"{what}: must be 16 bytes (32 hex digits), got {len(k)}")
    return k


def fingerprint(k):
    return hashlib.sha256(k).hexdigest()


def keys_path(a=None):
    return getattr(a, "keys", None) or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), KEYS_FILE)


def read_keys_file(path):
    """`name = hex` lines; `#` starts a comment.  Empty values are skipped."""
    keys = {}
    if not os.path.exists(path):
        return keys
    with open(path, encoding="utf-8") as f:
        for no, line in enumerate(f, 1):
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            name, sep, value = (x.strip() for x in line.partition("="))
            if not sep or name not in KEY_NAMES:
                raise BridgeError(f"{path}:{no}: expected one of {', '.join(KEY_NAMES)} = <hex>")
            if value:
                keys[name] = _hex16(value, f"{path}:{no} {name}")
    return keys


def write_keys_file(path, keys):
    lines = ["# PSP savedata keys for dr1_save_bridge.py.  Private: never commit or share.\n"]
    lines += [f"{n} = {keys[n].hex() if n in keys else ''}\n" for n in KEY_NAMES]
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def load_keys(a):
    """All keys needed to decrypt a DATKG.BIN, from keys.txt and --key."""
    path = keys_path(a)
    keys = read_keys_file(path)
    if getattr(a, "key", None):
        keys[GAME_KEY] = _hex16(a.key, "--key")
    missing = [n for n in KEY_NAMES if n not in keys]
    if missing:
        raise BridgeError(
            f"missing key(s) {', '.join(missing)} in {path}. An encrypted DATKG.BIN needs "
            f"them; run `python dr1_save_bridge.py keys --import <files>` or fill in "
            f"keys.example.txt and save it as {KEYS_FILE} (see README, Keys)")
    for n in CIPHER_KEYS:
        if fingerprint(keys[n]) != KEY_FINGERPRINTS[n]:
            raise BridgeError(f"{n} in {path} is wrong (fingerprint mismatch)")
    return keys


def looks_like_vfs(raw):
    """A savedata.vfs starts with [u32 len][name]; slot names begin with 'data'."""
    return len(raw) > 8 and raw[4:8] == b"data"


def load_psp(path, get_keys):
    """Read a decrypted core, or decrypt a DATKG.BIN; `get_keys` is called only then."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) == PSP_LEN:
        if not psp_ok(data):
            raise BridgeError(f"{path}: not a valid decrypted DR1 save (checksum)")
        return bytearray(data)
    if len(data) != PSP_LEN + PSP_ENC_HEAD:
        raise BridgeError(f"{path}: unexpected size {len(data)}")
    keys = get_keys()
    core = psp_decrypt(data, keys)
    if not psp_ok(core):
        known = fingerprint(keys[GAME_KEY]) == KEY_FINGERPRINTS[GAME_KEY]
        hint = (f"the game key is the one of {GAME_KEY_RELEASE}; is this save from "
                f"another release?" if known else "wrong game_key for this save?")
        raise BridgeError(f"{path}: decryption failed, {hint} "
                          f"(entropy {entropy(core[:4096]):.2f})")
    return bytearray(core)


# ---------------------------------------------------------- PC storage ------
def vfs_parse(blob):
    o, ents = 0, []
    while o < len(blob) - 4:
        n, = struct.unpack_from("<I", blob, o)
        if not 0 < n <= 64:
            raise BridgeError(f"savedata.vfs: bad entry name length at {o:#x}")
        name = blob[o + 4:o + 4 + n].decode("ascii")
        o += 4 + n
        size, = struct.unpack_from("<I", blob, o)
        o += 4
        ents.append([name, bytearray(blob[o:o + size])])
        o += size
    if o != len(blob) - 4 or struct.unpack_from("<I", blob, o)[0] != VFS_FOOTER:
        raise BridgeError("savedata.vfs: bad footer")
    return ents


def vfs_pack(ents):
    out = bytearray()
    for name, data in ents:
        nb = name.encode("ascii")
        out += struct.pack("<I", len(nb)) + nb + struct.pack("<I", len(data)) + data
    return bytes(out + struct.pack("<I", VFS_FOOTER))


def slot_name(n):
    return f"data{n - 1:04d}.bin"


# ----------------------------------------------------------- message log ----
def _rle(runs, size):
    out = bytearray(size)
    for off, val, n in runs:
        out[off:off + n] = bytes([val]) * n
    return out


def psp_log_width(gs):
    """Row width of the PSP message log in chars (54, or 66 in the fan translation)."""
    L = PSP_LOG
    write = struct.unpack_from("<H", gs, L["write"])[0]
    best, score = L["chars"][0], None
    for w in L["chars"]:
        s = 0
        for r in range(1, min(write, 32) + 1):
            o = L["text"] + 2 * r * w
            if o + 2 > L["attr"]:
                break
            if gs[o - 2:o] != b"\0\0":
                s -= 1000              # text runs across a row boundary: wrong width
            elif gs[o:o + 2] != b"\0\0":
                s += 1
        if score is None or s > score:
            best, score = w, s
    return best


def read_log(gs, L, width):
    """Message log lines, oldest first, as (meta, chars, attrs).

    `meta` is 3 bytes per line: speaker, kind (1 single line, 2 first line of a
    multi-line message, 0 continuation), style.
    """
    head, write = struct.unpack_from("<HH", gs, L["head"])
    rows = L["rows"]
    if head >= rows or write >= rows:
        return []
    lines, i = [], head
    while i != write and len(lines) < rows:
        t, a = L["text"] + 2 * i * width, L["attr"] + i * width
        if t + 2 * width > L["attr"] or a + width > L["end"]:
            break                          # row outside the array (fan translation)
        chars = list(struct.unpack_from(f"<{width}H", gs, t))
        n = chars.index(0) if 0 in chars else width
        m = L["meta"] + 3 * i
        lines.append((bytes(gs[m:m + 3]), chars[:n], list(gs[a:a + n])))
        i = (i + 1) % rows
    while lines and lines[0][0][1] == 0:   # oldest message was partly overwritten
        lines.pop(0)
    return lines


def read_psp_log(gs):
    return read_log(gs, PSP_LOG, psp_log_width(gs))


def read_pc_log(gs):
    return read_log(gs, PC_LOG, PC_LOG["chars"])


def _split(chars, limit):
    if len(chars) <= limit:
        return [chars]
    cut = max((k for k in range(1, limit + 1) if chars[k - 1] == 0x20), default=limit)
    return [chars[:cut]] + _split(chars[cut:], limit)


def fit_lines(lines, limit):
    """Split lines longer than `limit` chars at a space, keeping the meta kinds valid."""
    out = []
    for meta, chars, attrs in lines:
        parts, pos = _split(chars, limit), 0
        for k, part in enumerate(parts):
            kind = meta[1]
            if len(parts) > 1:
                kind = (2 if meta[1] in (1, 2) else 0) if k == 0 else 0
            out.append((bytes([meta[0], kind, meta[2]]), part, attrs[pos:pos + len(part)]))
            pos += len(part)
    return out


def write_pc_log(gs, lines):
    """Replace the PC message log with `lines` (an empty list clears it)."""
    L = PC_LOG
    w = L["chars"]
    gs[L["flag"]:L["end"]] = bytes(L["end"] - L["flag"])
    lines = fit_lines(lines, w - 1)[-(L["rows"] - 1):]
    for i, (meta, chars, attrs) in enumerate(lines):
        gs[L["meta"] + 3 * i:L["meta"] + 3 * i + 3] = meta
        struct.pack_into(f"<{len(chars)}H", gs, L["text"] + 2 * i * w, *chars)
        gs[L["attr"] + i * w:L["attr"] + i * w + len(attrs)] = bytes(attrs)
    struct.pack_into("<HH", gs, L["head"], 0, len(lines))
    gs[L["flag"]] = 1 if lines else 0
    gs[L["flag"] + 1] = 0xFF
    return len(lines)


# --------------------------------------------------------------- convert ----
def psp_body(core):
    return core[PSP_BODY:PSP_BODY + PSP_STATE]


def psp_stats(core):
    b = psp_body(core)
    return dict(
        chapter=b[PSP_CHAPTER],
        scene=struct.unpack_from("<H", b, PSP_SCENE)[0],
        script=struct.unpack_from("<H", b, PSP_SCRIPT)[0],
        coins=struct.unpack_from("<H", b, PSP_COINS)[0],
        saves=struct.unpack_from("<I", b, PSP_SAVES)[0],
    )


def pc_body(core):
    return core[PC_MAGIC:PC_MAGIC + PC_STATE]


def pc_stats(core):
    b = pc_body(core)
    h, m, s = core[PC_TIME + 2], core[PC_TIME + 1], core[PC_TIME]
    days, = struct.unpack_from("<I", core, PC_DAYS)
    return dict(
        chapter=struct.unpack_from("<H", b, PC_CHAPTER)[0],
        scene=struct.unpack_from("<H", b, PC_SCENE)[0],
        script=struct.unpack_from("<H", b, PC_SCRIPT)[0],
        coins=struct.unpack_from("<H", b, PC_COINS)[0],
        saves=struct.unpack_from("<I", core, PC_SAVES)[0],
        hours=h + 24 * days, minutes=m, seconds=s,
    )


def convert_state(src, backlog="clear", script="zero"):
    """Map a PSP game state onto a fresh PC game state, row by row of STATE_MAP."""
    out = bytearray(PC_GS_LEN)
    for lo, hi, kind, p in STATE_MAP:
        if kind == "copy":
            out[lo:hi] = src[p:p + hi - lo]
        elif kind == "widen":
            for i in range((hi - lo) // 2):
                out[lo + 2 * i] = src[p + i]
        elif kind == "window":
            out[lo:hi] = WINDOW_IDLE
    write_pc_log(out, read_psp_log(src) if backlog == "keep" else [])
    if script == "zero":
        # PSP and PC script files differ, a PSP offset would land mid-instruction
        struct.pack_into("<I", out, GS_IP, 0)
        struct.pack_into("<H", out, GS_TEXT_IP, 0)
    out[GS_SAVE_FLAGS] |= 0x80
    return out


def convert_core(psp, system=None, backlog="clear", script="zero"):
    """Build a complete PC core from a decrypted PSP core.

    `system` is an optional healthy PC core that supplies the option bytes and the
    system block (the player's settings and unlocks); built-in defaults otherwise.
    """
    if not psp_ok(psp):
        raise BridgeError("PSP data is not a valid decrypted DR1 save (magic/checksum)")
    if system is not None and not pc_ok(system):
        raise BridgeError("PC slot used for the system data is damaged (magic/checksum)")
    body = psp_body(psp)
    core = bytearray(PC_LEN)
    core[0:4] = MAGIC
    opts = bytearray(system[PC_OPTIONS:PC_OPTIONS + 8] if system else PC_OPTION_DEFAULTS)
    opts[3:5] = psp[PSP_DIFFICULTY:PSP_DIFFICULTY + 2]
    core[PC_OPTIONS:PC_OPTIONS + 8] = opts
    core[PC_CLEARED:PC_CLEARED + 4] = psp[PSP_CLEARED:PSP_CLEARED + 4]
    core[PC_SAVES:PC_SAVES + 4] = body[PSP_SAVES:PSP_SAVES + 4]
    core[PC_FLAGS:PC_FLAGS + 4] = psp[PSP_FLAGS:PSP_FLAGS + 4]
    b = PC_MAGIC
    core[b:b + 4] = MAGIC
    core[b + PC_GS:b + PC_GS + PC_GS_LEN] = convert_state(
        body[PSP_GS:PSP_GS + PSP_GS_LEN], backlog, script)
    core[b + PC_SCHOOL:b + PC_SCHOOL + PC_GS_LEN] = _rle(SCHOOL_DEFAULT, PC_GS_LEN)
    core[b + PC_SYSTEM:b + PC_STATE] = (system[b + PC_SYSTEM:b + PC_STATE] if system
                                        else _rle(SYSTEM_DEFAULT, PC_SYSTEM_LEN))
    pc_fix_sums(core)
    if not pc_ok(core):
        raise BridgeError("internal error: converted core failed its own checksums")
    return core


def _put_str(entry, off, size, s):
    b = s.encode("utf-8")[:size - 1]
    entry[off:off + size] = b + bytes(size - len(b))


def chapter_label(core):
    st = pc_stats(core)
    return CHAPTER_LABELS.get(st["chapter"], f"Chapter {st['chapter']:02d}")


def build_entry(psp, system=None, backlog="clear", script="zero", now=None):
    core = convert_core(psp, system, backlog, script)
    e = bytearray(ENTRY_HDR) + core
    e[0:0x40] = TITLE.encode("ascii").ljust(0x40, b"\x00")
    st = pc_stats(core)
    _put_str(e, 0x40, 0x80, chapter_label(core))
    _put_str(e, 0xC0, 0x200,
             f"Monocoins acquired: {st['coins']}coins\n"
             f"Time Played {st['hours']}:{st['minutes']:02d}:{st['seconds']:02d}\n"
             f"Number of times saved: {st['saves']}")
    _put_str(e, 0x2C0, 0x20, (now or datetime.datetime.now()).strftime("%d.%m.%Y/%H.%M.%S"))
    struct.pack_into("<I", e, ENTRY_HDR - 4, PC_LEN)
    return e, st


def default_game_dir():
    return os.path.join(os.path.expanduser("~"), "Documents", "My Games", "Danganronpa")


# -------------------------------------------------------------------- CLI ----
def _stamp(entry):
    """Sort key from the slot timestamp `DD.MM.YYYY/HH.MM.SS`."""
    s = bytes(entry[0x2C0:0x2E0]).split(b"\0")[0].decode("ascii", "replace")
    try:
        return datetime.datetime.strptime(s, "%d.%m.%Y/%H.%M.%S")
    except ValueError:
        return datetime.datetime.min


def pick_system(ents, want):
    """PC slot whose settings and system block are reused: `want`, else the newest."""
    if want:
        name = slot_name(want)
        for n, d in ents:
            if n == name and entry_ok(d):
                return name, d
        raise BridgeError(f"system slot {want} missing or damaged")
    cands = [(n, d) for n, d in ents if n.startswith("data") and entry_ok(d)]
    if not cands:
        return None, None
    return max(cands, key=lambda c: _stamp(c[1]))


def cmd_convert(a):
    if os.path.exists(a.pc_vfs):
        with open(a.pc_vfs, "rb") as f:
            ents = vfs_parse(f.read())
    else:
        print(f"{a.pc_vfs} does not exist, a new savedata.vfs is created")
        ents = []
    targets = {}
    for spec in a.map:
        slot, _, src = spec.partition("=")
        if not slot.isdigit() or not 1 <= int(slot) <= 99 or not src:
            raise BridgeError(f"bad --map {spec!r}, expected SLOT=FILE")
        targets[slot_name(int(slot))] = src
    sname, system = (None, None) if a.default_system else pick_system(ents, a.system_slot)
    print(f"settings and system data: "
          f"{'slot %d' % (int(sname[4:8]) + 1) if sname else 'built-in defaults'}"
          f"   backlog: {a.backlog}   script: {a.script}")
    loaded = [(name, src, load_psp(src, lambda: load_keys(a))) for name, src in targets.items()]
    for name, src, psp in loaded:
        entry, est = build_entry(psp, system[ENTRY_HDR:] if system else None,
                                 a.backlog, a.script)
        for x in ents:
            if x[0] == name:
                x[1] = entry
                break
        else:
            ents.append([name, entry])
        print(f"  slot {int(name[4:8]) + 1} <- {os.path.basename(src)}: "
              f"{chapter_label(entry[ENTRY_HDR:])}, scene {est['scene']:#06x}, "
              f"{est['coins']} Monocoins, {est['saves']} saves")
    ents.sort(key=lambda x: (not x[0].startswith("data"), x[0]))
    blob = vfs_pack(ents)
    for name, data in vfs_parse(blob):
        if name in targets and not entry_ok(data):
            raise BridgeError(f"self-check failed for {name}")
    if a.install:
        if os.path.exists(a.out):
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            bak = os.path.join(os.path.dirname(os.path.abspath(a.out)) or ".",
                               f"savedata.vfs.{ts}.bak")
            shutil.copy2(a.out, bak)
            print(f"backup: {bak}")
        else:
            os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    elif os.path.exists(a.out) and not a.force:
        raise BridgeError(f"{a.out} exists (use --force or --install)")
    with open(a.out, "wb") as f:
        f.write(blob)
    print(f"wrote {a.out} ({len(blob)} bytes)")


def verify_state(psp, pc_core):
    """Compare a template-free conversion with a real PC slot, per STATE_MAP row.

    Returns [(pc_lo, pc_hi, kind, differing byte offsets)].  Only `copy` and
    `widen` rows carry PSP data; the rest are expected to differ.
    """
    got = convert_state(psp_body(psp)[PSP_GS:PSP_GS + PSP_GS_LEN], "clear", "copy")
    real = pc_body(pc_core)[PC_GS:PC_GS + PC_GS_LEN]
    return [(lo, hi, kind, [i for i in range(lo, hi) if got[i] != real[i]])
            for lo, hi, kind, _ in STATE_MAP]


def cmd_verify(a):
    psp = load_psp(a.psp, lambda: load_keys(a))
    with open(a.pc_vfs, "rb") as f:
        ents = dict((n, d) for n, d in vfs_parse(f.read()))
    data = ents.get(slot_name(a.slot))
    if data is None or not entry_ok(data):
        raise BridgeError(f"slot {a.slot} missing or damaged")
    mapped = same = 0
    print("row                  kind    differing bytes")
    for lo, hi, kind, diff in verify_state(psp, data[ENTRY_HDR:]):
        if kind in ("copy", "widen"):
            mapped += hi - lo
            same += hi - lo - len(diff)
        shown = " ".join(f"{i:#x}" for i in diff[:8]) + (" ..." if len(diff) > 8 else "")
        print(f"{lo:#07x}-{hi:#07x}  {kind:7} {len(diff):6}  {shown}")
    print(f"transferred bytes identical to the PC slot: {same}/{mapped} "
          f"({100 * same / mapped:.2f} %)")


def _key_candidates(path):
    """(label, 16 bytes) candidates in a file: C byte arrays, hex strings, PPSSPP
    log lines and 16-byte binary files."""
    import re
    with open(path, "rb") as f:
        data = f.read(8 << 20)
    if len(data) == 16:
        yield "binary", data
        return
    text = data.decode("latin-1")
    for m in re.finditer(r"Game key \(hex\):\s*([0-9A-Fa-f]{32})", text):
        yield GAME_KEY, bytes.fromhex(m.group(1))
    for m in re.finditer(r"(?<![0-9A-Fa-f])([0-9A-Fa-f]{32})(?![0-9A-Fa-f])", text):
        yield "hex", bytes.fromhex(m.group(1))
    tokens = [int(t, 16) for t in re.findall(r"0[xX]([0-9A-Fa-f]{2})\b", text)]
    for i in range(len(tokens) - 15):
        yield "array", bytes(tokens[i:i + 16])


def import_keys(paths):
    """Find the keys in the user's files by fingerprint.  Returns (keys, other
    game keys found in PPSSPP logs)."""
    by_fp = {v: n for n, v in KEY_FINGERPRINTS.items()}
    found, others = {}, set()
    files = []
    for p in paths:
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in names
                          if n.lower().endswith((".c", ".cpp", ".h", ".txt", ".log", ".bin",
                                                 ".ini", ".py"))]
        else:
            files.append(p)
    for path in files:
        try:
            for label, k in _key_candidates(path):
                name = by_fp.get(fingerprint(k))
                if name:
                    found[name] = k
                elif label == GAME_KEY:
                    others.add(k)
        except OSError:
            continue
    return found, others


def cmd_keys(a):
    path = keys_path(a)
    keys = read_keys_file(path)
    if a.game_key:
        keys[GAME_KEY] = _hex16(a.game_key, "--game-key")
    if a.import_paths:
        found, others = import_keys(a.import_paths)
        keys.update(found)
        print(f"found: {', '.join(sorted(found)) or 'nothing'}")
        if others and GAME_KEY not in found:
            print(f"{len(others)} game key(s) of other releases found in PPSSPP logs; "
                  f"pass the right one with --game-key")
    if a.game_key or a.import_paths:
        write_keys_file(path, keys)
        print(f"wrote {path}")
    print(f"keys file: {path}")
    for n in KEY_NAMES:
        if n not in keys:
            state = "missing"
        elif fingerprint(keys[n]) == KEY_FINGERPRINTS[n]:
            state = "ok"
        elif n == GAME_KEY:
            state = f"set (not the {GAME_KEY_RELEASE} key)"
        else:
            state = "WRONG"
        print(f"  {n:12} {state}")


def cmd_info(a):
    with open(a.file, "rb") as f:
        raw = f.read()
    if looks_like_vfs(raw):
        for name, data in vfs_parse(raw):
            if not name.startswith("data"):
                print(f"{name}: {len(data)} bytes")
                continue
            if not entry_ok(data):
                print(f"slot {int(name[4:8]) + 1} ({name}): DAMAGED / foreign layout")
                continue
            st = pc_stats(data[ENTRY_HDR:])
            print(f"slot {int(name[4:8]) + 1} ({name}): "
                  f"{CHAPTER_LABELS.get(st['chapter'], st['chapter'])}, "
                  f"scene {st['scene']:#06x}, {st['coins']} Monocoins, "
                  f"{st['hours']}:{st['minutes']:02d}:{st['seconds']:02d}, "
                  f"{st['saves']} saves")
        return
    if len(raw) in (PSP_LEN, PSP_LEN + PSP_ENC_HEAD):
        core = load_psp(a.file, lambda: load_keys(a))
        st = psp_stats(core)
        print(f"PSP save ok, {CHAPTER_LABELS.get(st['chapter'], st['chapter'])}, "
              f"scene {st['scene']:#06x}, script {st['script']:#06x}, "
              f"{st['coins']} Monocoins, {st['saves']} saves")
        gs = psp_body(core)[PSP_GS:PSP_GS + PSP_GS_LEN]
        for _, chars, _ in read_psp_log(gs)[-8:]:
            print(f"  | {''.join(map(chr, chars))}")
        return
    print(f"unknown file ({len(raw)} bytes)")


def cmd_probe(a):
    with open(a.file, "rb") as f:
        raw = f.read()
    if looks_like_vfs(raw):
        for name, data in vfs_parse(raw):
            if name.startswith("data") and entry_ok(data):
                _dump_pc(name, data[ENTRY_HDR:])
        return
    _dump_psp(load_psp(a.file, lambda: load_keys(a)))


def _dump_psp(core):
    st = psp_stats(core)
    gs = psp_body(core)[PSP_GS:PSP_GS + PSP_GS_LEN]
    print(f"PSP core {len(core):#x}  magic {core[:8].hex(' ')} / "
          f"{core[PSP_MAGIC:PSP_MAGIC + 8].hex(' ')}")
    print(f"  header sum @{PSP_HDR_SUM:#x}   tail sum @{PSP_TAIL_SUM:#x}   "
          f"body @{PSP_BODY:#x} size {PSP_STATE:#x}, game state @body+{PSP_GS:#x}")
    print(f"  {CHAPTER_LABELS.get(st['chapter'], st['chapter'])}  scene "
          f"{st['scene']:#06x}  script {st['script']:#06x}  "
          f"Monocoins {st['coins']}  saves {st['saves']}")
    print(f"  message log: {psp_log_width(gs)} chars per row, "
          f"{len(read_psp_log(gs))} lines")
    print("  state map (pc game state <- psp game state):")
    for lo, hi, kind, p in STATE_MAP:
        src = f"<- {p:#07x}" if p is not None else ""
        print(f"    {lo:#07x}-{hi:#07x} {kind:7} {src}")


def _dump_pc(name, core):
    st = pc_stats(core)
    gs = pc_body(core)[PC_GS:PC_GS + PC_GS_LEN]
    print(f"PC core {len(core):#x} {name}  magic {core[:4].hex()} / "
          f"{core[PC_MAGIC:PC_MAGIC + 4].hex()}  options "
          f"{core[PC_OPTIONS:PC_OPTIONS + 8].hex(' ')}")
    print(f"  header sum @{PC_HDR_SUM:#x}   tail sum @{PC_TAIL_SUM:#x}   "
          f"body @{PC_MAGIC:#x} size {PC_STATE:#x}")
    print(f"  {CHAPTER_LABELS.get(st['chapter'], st['chapter'])}  scene "
          f"{st['scene']:#06x}  script {st['script']:#06x}  "
          f"Monocoins {st['coins']}  saves {st['saves']}  "
          f"time {st['hours']}:{st['minutes']:02d}:{st['seconds']:02d}")
    lines = read_pc_log(gs)
    print(f"  message log: {len(lines)} lines")
    for _, chars, _ in lines[-4:]:
        print(f"  | {''.join(map(chr, chars))}")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(
        prog="dr1_save_bridge", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version=__version__)
    ap.add_argument("--keys", metavar="FILE",
                    help=f"keys file (default: {KEYS_FILE} next to this script)")
    ap.add_argument("--key", help="16-byte PSP game key in hex, overrides the keys file")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert", help="put PSP saves into PC savedata.vfs slots")
    c.add_argument("--pc-vfs", help="existing PC savedata.vfs; created if missing")
    c.add_argument("--map", action="append", required=True, metavar="SLOT=FILE",
                   help="PC slot number (1-based) = PSP DATKG.BIN; repeatable")
    c.add_argument("--backlog", choices=["clear", "keep"], default="clear",
                   help="clear the dialogue log (default) or copy the PSP lines over")
    c.add_argument("--script", choices=["zero", "copy"], default="zero",
                   help="restart the current script (default) or copy the PSP offset")
    c.add_argument("--system-slot", type=int,
                   help="PC slot whose settings and unlocks are reused (default: newest)")
    c.add_argument("--default-system", action="store_true",
                   help="ignore existing slots, use fresh-game settings and unlocks")
    c.add_argument("--out", help="output file, defaults to --pc-vfs with --install")
    c.add_argument("--install", action="store_true",
                   help="overwrite --pc-vfs, keeping a timestamped .bak next to it")
    c.add_argument("--force", action="store_true")
    c.set_defaults(func=cmd_convert)
    i = sub.add_parser("info", help="show the slots of a savedata.vfs or a PSP save")
    i.add_argument("file")
    i.set_defaults(func=cmd_info)
    p = sub.add_parser("probe", help="dump the save structure")
    p.add_argument("file")
    p.set_defaults(func=cmd_probe)
    v = sub.add_parser("verify", help="compare a conversion with a real PC slot "
                                      "made at the same story point")
    v.add_argument("psp", help="PSP DATKG.BIN")
    v.add_argument("pc_vfs", help="PC savedata.vfs")
    v.add_argument("--slot", type=int, required=True, help="PC slot number (1-based)")
    v.set_defaults(func=cmd_verify)
    k = sub.add_parser("keys", help="show, import or set the decryption keys")
    k.add_argument("--import", dest="import_paths", nargs="+", metavar="PATH",
                   help="files or folders to search: PPSSPP sources (sceChnnlsv.cpp, "
                        "kirk_engine.c), PPSSPP logs, gamekey.bin")
    k.add_argument("--game-key", metavar="HEX", help="store this game key")
    k.set_defaults(func=cmd_keys)
    a = ap.parse_args(argv)
    if a.cmd == "convert" and not a.pc_vfs:
        a.pc_vfs = os.path.join(default_game_dir(), "savedata.vfs")
    if a.cmd == "convert" and a.install and not a.out:
        a.out = a.pc_vfs
    if a.cmd == "convert" and not a.out:
        ap.error("convert needs --out, or --install to write over the input file")
    try:
        a.func(a)
    except (BridgeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
