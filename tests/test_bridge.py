"""Unit tests for dr1_save_bridge.  Synthetic data only, no real saves."""
import datetime
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import dr1_save_bridge as B

GS = B.PSP_BODY + B.PSP_GS          # PSP game state inside the core


def psp_fix(core):
    struct.pack_into("<I", core, B.PSP_HDR_SUM, B.sum8(core[:B.PSP_HDR_SUM]))
    struct.pack_into("<I", core, B.PSP_TAIL_SUM, B.sum8(core[B.PSP_BODY:B.PSP_TAIL_SUM]))
    return core


def make_psp(chapter=0, coins=0, saves=1, scene=0x5E, script=0xA92, difficulty=(2, 2),
             cleared=0, flags=0, lines=(), width=54, fill=None):
    core = bytearray(B.PSP_LEN)
    if fill is not None:
        for i in range(B.PSP_GS_LEN):
            core[GS + i] = fill(i)
    core[0:4] = B.MAGIC
    core[4:7] = b"\x50\x50\x50"
    core[B.PSP_DIFFICULTY:B.PSP_DIFFICULTY + 2] = bytes(difficulty)
    struct.pack_into("<II", core, B.PSP_CLEARED, cleared, flags)
    core[B.PSP_MAGIC:B.PSP_MAGIC + 4] = B.MAGIC
    core[B.PSP_BODY + B.PSP_CHAPTER] = chapter
    struct.pack_into("<H", core, B.PSP_BODY + B.PSP_SCENE, scene)
    struct.pack_into("<H", core, B.PSP_BODY + B.PSP_SCRIPT, script)
    struct.pack_into("<H", core, B.PSP_BODY + B.PSP_COINS, coins)
    struct.pack_into("<I", core, B.PSP_BODY + B.PSP_SAVES, saves)
    if lines:
        L = B.PSP_LOG
        for k, (meta, text) in enumerate(lines):
            core[GS + L["meta"] + 3 * k:GS + L["meta"] + 3 * k + 3] = meta
            chars = [ord(c) for c in text]
            struct.pack_into(f"<{len(chars)}H", core, GS + L["text"] + 2 * k * width, *chars)
            a = GS + L["attr"] + k * width
            core[a:a + len(chars)] = bytes([4]) * len(chars)
        struct.pack_into("<HH", core, GS + L["head"], 0, len(lines))
    return psp_fix(core)


def make_entry(core):
    e = bytearray(B.ENTRY_HDR) + core
    e[0:0x40] = B.TITLE.encode("ascii").ljust(0x40, b"\x00")
    struct.pack_into("<I", e, B.ENTRY_HDR - 4, B.PC_LEN)
    return e


def gs_of(core):
    return B.pc_body(core)[B.PC_GS:B.PC_GS + B.PC_GS_LEN]


class TestChecksums(unittest.TestCase):
    def test_psp_roundtrip(self):
        core = make_psp()
        self.assertTrue(B.psp_ok(core))
        core[100] ^= 1
        self.assertFalse(B.psp_ok(core))

    def test_converted_core_passes_both_pc_checksums(self):
        core = B.convert_core(make_psp())
        self.assertTrue(B.pc_ok(core))
        core[B.PC_TAIL_SUM - 1] ^= 1
        self.assertFalse(B.pc_ok(core))

    def test_pc_header_sum_is_independent_of_tail(self):
        core = B.convert_core(make_psp())
        core[B.PC_LEN // 2] ^= 0xFF
        self.assertEqual(struct.unpack_from("<I", core, B.PC_HDR_SUM)[0],
                         B.sum8(core[:B.PC_HDR_SUM]))

    def test_entry_ok(self):
        e, _ = B.build_entry(make_psp())
        self.assertTrue(B.entry_ok(e))
        self.assertFalse(B.entry_ok(e[:-1]))


class TestVfs(unittest.TestCase):
    def test_roundtrip(self):
        ents = [["data0000.bin", bytearray(b"abc")], ["icon0000.png", bytearray(b"x")]]
        self.assertEqual(B.vfs_parse(B.vfs_pack(ents)), ents)

    def test_bad_footer(self):
        blob = bytearray(B.vfs_pack([["data0000.bin", bytearray(b"abc")]]))
        blob[-1] ^= 1
        with self.assertRaises(B.BridgeError):
            B.vfs_parse(bytes(blob))

    def test_slot_names(self):
        self.assertEqual(B.slot_name(1), "data0000.bin")
        self.assertEqual(B.slot_name(10), "data0009.bin")


class TestStateMap(unittest.TestCase):
    def test_rows_tile_the_whole_pc_game_state(self):
        pos = 0
        for lo, hi, kind, p in B.STATE_MAP:
            self.assertEqual(lo, pos)
            self.assertGreater(hi, lo)
            self.assertIn(kind, ("copy", "widen", "zero", "log", "window"))
            pos = hi
        self.assertEqual(pos, B.PC_GS_LEN)

    def test_psp_sources_are_in_range_and_in_order(self):
        prev = 0
        for lo, hi, kind, p in B.STATE_MAP:
            if kind in ("copy", "widen"):
                n = hi - lo if kind == "copy" else (hi - lo) // 2
                self.assertGreaterEqual(p, prev)
                self.assertLessEqual(p + n, B.PSP_GS_LEN)
                prev = p + n
            else:
                self.assertIsNone(p)

    def test_copy_rows_are_verbatim(self):
        psp = make_psp(fill=lambda i: (i * 37 + 11) & 0xFF)
        src = psp[GS:GS + B.PSP_GS_LEN]
        gs = gs_of(B.convert_core(psp, script="copy"))
        for lo, hi, kind, p in B.STATE_MAP:
            if kind == "copy":
                want = bytearray(src[p:p + hi - lo])
                if lo <= B.GS_SAVE_FLAGS < hi:
                    want[B.GS_SAVE_FLAGS - lo] |= 0x80
                self.assertEqual(gs[lo:hi], want, hex(lo))

    def test_widen_rows_zero_extend(self):
        psp = make_psp(fill=lambda i: 0xF0 | (i & 0xF))
        gs = gs_of(B.convert_core(psp))
        lo, hi, _, p = B.STATE_MAP[0]
        for i in range((hi - lo) // 2):
            self.assertEqual(struct.unpack_from("<H", gs, lo + 2 * i)[0], psp[GS + p + i])

    def test_pc_only_rows_ignore_psp_bytes(self):
        gs = gs_of(B.convert_core(make_psp(fill=lambda i: 0xAB)))
        for lo, hi, kind, _ in B.STATE_MAP:
            if kind == "zero":
                self.assertEqual(gs[lo:hi], bytes(hi - lo), hex(lo))
            elif kind == "window":
                self.assertEqual(gs[lo:hi], B.WINDOW_IDLE)

    def test_known_fields(self):
        psp = make_psp(chapter=7, coins=635, saves=25, scene=0x77)
        e, st = B.build_entry(psp)
        self.assertEqual((st["chapter"], st["scene"], st["coins"], st["saves"]),
                         (7, 0x77, 635, 25))
        self.assertEqual(B.chapter_label(e[B.ENTRY_HDR:]), "Epilogue")

    def test_script_is_restarted_by_default(self):
        gs = gs_of(B.convert_core(make_psp(script=0xCB7)))
        self.assertEqual(struct.unpack_from("<I", gs, B.GS_IP)[0], 0)
        self.assertEqual(struct.unpack_from("<H", gs, B.GS_TEXT_IP)[0], 0)

    def test_script_can_be_copied(self):
        _, st = B.build_entry(make_psp(script=0xCB7), script="copy")
        self.assertEqual(st["script"], 0xCB7)

    def test_save_flag_bit_is_set_like_the_pc_save_routine(self):
        gs = gs_of(B.convert_core(make_psp()))
        self.assertTrue(gs[B.GS_SAVE_FLAGS] & 0x80)


class TestNoTemplate(unittest.TestCase):
    def test_header_comes_from_the_psp_save_and_defaults(self):
        psp = make_psp(saves=9, difficulty=(1, 0), cleared=0xFF, flags=0x100)
        core = B.convert_core(psp)
        opts = bytearray(B.PC_OPTION_DEFAULTS)
        opts[3:5] = b"\x01\x00"
        self.assertEqual(core[B.PC_OPTIONS:B.PC_OPTIONS + 8], opts)
        self.assertEqual(struct.unpack_from("<III", core, B.PC_CLEARED), (0xFF, 9, 0x100))
        self.assertEqual(core[B.PC_TIME:B.PC_TIME + 8], bytes(8))

    def test_pc_only_blocks_get_fresh_game_values(self):
        body = B.pc_body(B.convert_core(make_psp()))
        self.assertEqual(body[B.PC_SCHOOL:B.PC_SCHOOL + B.PC_GS_LEN],
                         B._rle(B.SCHOOL_DEFAULT, B.PC_GS_LEN))
        self.assertEqual(body[B.PC_SYSTEM:], B._rle(B.SYSTEM_DEFAULT, B.PC_SYSTEM_LEN))

    def test_system_slot_supplies_settings_and_unlocks_only(self):
        system = bytearray(B.convert_core(make_psp(chapter=3, coins=77)))
        system[B.PC_OPTIONS:B.PC_OPTIONS + 8] = bytes(range(10, 18))
        system[B.PC_MAGIC + B.PC_SYSTEM:B.PC_MAGIC + B.PC_STATE] = bytes([7]) * B.PC_SYSTEM_LEN
        B.pc_fix_sums(system)
        core = B.convert_core(make_psp(difficulty=(2, 1)), system)
        self.assertEqual(core[B.PC_OPTIONS:B.PC_OPTIONS + 8], bytes((10, 11, 12, 2, 1, 15, 16, 17)))
        self.assertEqual(B.pc_body(core)[B.PC_SYSTEM:], bytes([7]) * B.PC_SYSTEM_LEN)
        self.assertEqual(B.pc_stats(core)["coins"], 0)

    def test_damaged_system_slot_is_rejected(self):
        system = bytearray(B.convert_core(make_psp()))
        system[500] ^= 1
        with self.assertRaises(B.BridgeError):
            B.convert_core(make_psp(), system)


class TestLog(unittest.TestCase):
    LINES = [(b"\x1e\x02\x00", "first line of a message"), (b"\x1e\x00\x00", "and its end"),
             (b"\x05\x01\x00", "a single line")]

    def test_clear_empties_the_log(self):
        gs = gs_of(B.convert_core(make_psp(lines=self.LINES)))
        L = B.PC_LOG
        self.assertEqual(gs[L["flag"]], 0)
        self.assertEqual(gs[L["flag"] + 1], 0xFF)
        self.assertEqual(gs[L["head"]:L["end"]], bytes(L["end"] - L["head"]))

    def test_keep_transfers_lines_in_order(self):
        for width in B.PSP_LOG["chars"]:
            psp = make_psp(lines=self.LINES, width=width)
            self.assertEqual(B.psp_log_width(psp[GS:GS + B.PSP_GS_LEN]), width)
            got = B.read_pc_log(gs_of(B.convert_core(psp, backlog="keep")))
            self.assertEqual([("".join(map(chr, c)), m) for m, c, _ in got],
                             [(t, m) for m, t in self.LINES])
            self.assertEqual(got[0][2], [4] * len(self.LINES[0][1]))

    def test_long_lines_are_split_at_spaces(self):
        text = ("word " * 13).strip()      # 64 chars, one more than a PC row holds
        got = B.read_pc_log(gs_of(B.convert_core(
            make_psp(lines=[(b"\x01\x01\x00", text)], width=66), backlog="keep")))
        self.assertEqual(len(got), 2)
        self.assertEqual([m[1] for m, _, _ in got], [2, 0])
        self.assertEqual("".join(chr(c) for _, cs, _ in got for c in cs), text)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.psp = os.path.join(self.dir.name, "core.bin")
        with open(self.psp, "wb") as f:
            f.write(bytes(make_psp(chapter=2, coins=5, saves=3)))

    def tearDown(self):
        self.dir.cleanup()

    def path(self, name):
        return os.path.join(self.dir.name, name)

    def test_convert_without_any_pc_save(self):
        out = self.path("new.vfs")
        rc = B.main(["convert", "--pc-vfs", self.path("missing.vfs"),
                     "--map", f"3={self.psp}", "--out", out])
        self.assertEqual(rc, 0)
        ents = B.vfs_parse(open(out, "rb").read())
        self.assertEqual([n for n, _ in ents], ["data0002.bin"])
        self.assertTrue(B.entry_ok(ents[0][1]))

    def test_existing_slots_and_icons_are_kept(self):
        vfs = self.path("savedata.vfs")
        old, _ = B.build_entry(make_psp(chapter=5))
        with open(vfs, "wb") as f:
            f.write(B.vfs_pack([["data0000.bin", old], ["icon0000.png", bytearray(b"png")]]))
        self.assertEqual(B.main(["convert", "--pc-vfs", vfs, "--map", f"2={self.psp}",
                                 "--install"]), 0)
        ents = dict(B.vfs_parse(open(vfs, "rb").read()))
        self.assertEqual(ents["data0000.bin"], old)
        self.assertEqual(ents["icon0000.png"], b"png")
        self.assertEqual(B.pc_stats(ents["data0001.bin"][B.ENTRY_HDR:])["chapter"], 2)
        self.assertTrue(any(n.endswith(".bak") for n in os.listdir(self.dir.name)))

    def test_refuses_to_clobber_without_force(self):
        out = self.path("o.vfs")
        open(out, "wb").close()
        args = ["convert", "--pc-vfs", self.path("x.vfs"), "--map", f"1={self.psp}", "--out", out]
        self.assertEqual(B.main(args), 1)
        self.assertEqual(B.main(args + ["--force"]), 0)

    def test_bad_map_spec(self):
        self.assertEqual(B.main(["convert", "--pc-vfs", self.path("x.vfs"), "--map", "nope",
                                 "--out", self.path("o.vfs")]), 1)

    def test_missing_system_slot_is_reported(self):
        self.assertEqual(B.main(["convert", "--pc-vfs", self.path("x.vfs"),
                                 "--map", f"1={self.psp}", "--system-slot", "9",
                                 "--out", self.path("o.vfs")]), 1)

    def test_encrypted_save_without_keys_is_refused(self):
        p = self.path("enc.BIN")
        with open(p, "wb") as f:
            f.write(bytes(B.PSP_LEN + B.PSP_ENC_HEAD))
        out = self.path("o.vfs")
        self.assertEqual(B.main(["--keys", self.path("none.txt"), "convert", "--pc-vfs",
                                 self.path("x.vfs"), "--map", f"1={p}", "--out", out]), 1)
        self.assertFalse(os.path.exists(out))

    def test_decrypted_core_needs_no_keys(self):
        self.assertEqual(B.main(["--keys", self.path("none.txt"), "info", self.psp]), 0)

    def test_verify_against_a_matching_pc_slot(self):
        vfs = self.path("pc.vfs")
        e, _ = B.build_entry(make_psp(chapter=2, coins=5, saves=3), script="copy")
        with open(vfs, "wb") as f:
            f.write(B.vfs_pack([["data0000.bin", e]]))
        self.assertEqual(B.main(["verify", self.psp, vfs, "--slot", "1"]), 0)
        rows = B.verify_state(make_psp(chapter=2, coins=5, saves=3), e[B.ENTRY_HDR:])
        self.assertTrue(all(not d for lo, hi, k, d in rows if k in ("copy", "widen")))


class TestInfo(unittest.TestCase):
    def test_info_and_probe(self):
        with tempfile.TemporaryDirectory() as td:
            vfs, core = os.path.join(td, "savedata.vfs"), os.path.join(td, "core.bin")
            e, _ = B.build_entry(make_psp(chapter=5, coins=100, saves=7),
                                 now=datetime.datetime(2026, 1, 2, 3, 4, 5))
            with open(vfs, "wb") as f:
                f.write(B.vfs_pack([["data0000.bin", e]]))
            with open(core, "wb") as f:
                f.write(bytes(make_psp(lines=[(b"\x01\x01\x00", "hello")])))
            for args in (["info", vfs], ["info", core], ["probe", vfs], ["probe", core]):
                self.assertEqual(B.main(args), 0, args)


FAKE_KEYS = {n: bytes([i + 1]) * 16 for i, n in enumerate(B.KEY_NAMES)}


class FakeFingerprints:
    """Swap the real fingerprints for those of FAKE_KEYS for the test's duration."""
    def __enter__(self):
        self.saved = dict(B.KEY_FINGERPRINTS)
        B.KEY_FINGERPRINTS.update({n: B.fingerprint(k) for n, k in FAKE_KEYS.items()})

    def __exit__(self, *exc):
        B.KEY_FINGERPRINTS.clear()
        B.KEY_FINGERPRINTS.update(self.saved)


class Args:
    def __init__(self, keys, key=None):
        self.keys, self.key = keys, key


class TestKeys(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.file = os.path.join(self.dir.name, "keys.txt")

    def tearDown(self):
        self.dir.cleanup()

    def test_no_key_material_in_the_source(self):
        """Only 64-digit SHA-256 fingerprints may appear as long hex literals."""
        import re
        src = open(B.__file__, encoding="utf-8").read()
        self.assertEqual(re.findall(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{32}(?![0-9A-Fa-f])", src), [])
        self.assertEqual(re.findall(r"(0[xX][0-9A-Fa-f]{2}\s*,\s*){15}", src), [])
        for fp in B.KEY_FINGERPRINTS.values():
            self.assertRegex(fp, r"^[0-9a-f]{64}$")

    def test_keys_file_roundtrip(self):
        B.write_keys_file(self.file, {n: FAKE_KEYS[n] for n in B.KEY_NAMES[:2]})
        self.assertEqual(B.read_keys_file(self.file), {n: FAKE_KEYS[n] for n in B.KEY_NAMES[:2]})

    def test_bad_keys_file_line(self):
        with open(self.file, "w") as f:
            f.write("nonsense = 00\n")
        with self.assertRaises(B.BridgeError):
            B.read_keys_file(self.file)

    def test_missing_keys_are_named(self):
        with self.assertRaises(B.BridgeError) as cm:
            B.load_keys(Args(self.file))
        for n in B.KEY_NAMES:
            self.assertIn(n, str(cm.exception))

    def test_wrong_cipher_key_is_detected(self):
        with FakeFingerprints():
            bad = dict(FAKE_KEYS, kirk_key_64=bytes(16))
            B.write_keys_file(self.file, bad)
            with self.assertRaises(B.BridgeError) as cm:
                B.load_keys(Args(self.file))
            self.assertIn("kirk_key_64", str(cm.exception))

    def test_game_key_option_overrides_the_file(self):
        with FakeFingerprints():
            B.write_keys_file(self.file, FAKE_KEYS)
            keys = B.load_keys(Args(self.file, key="ab" * 16))
            self.assertEqual(keys[B.GAME_KEY], bytes([0xAB]) * 16)

    def test_import_from_sources_logs_and_binaries(self):
        d = self.dir.name
        arr = lambda k: "{" + ", ".join(f"0x{b:02X}" for b in k) + "}"
        with open(os.path.join(d, "sceChnnlsv.cpp"), "w") as f:
            f.write(f"static const u8 key19CC[16] = {arr(FAKE_KEYS['key19CC'])};\n"
                    f"static const u8 key19DC[16] = {arr(FAKE_KEYS['key19DC'])};\n")
        with open(os.path.join(d, "kirk_engine.c"), "w") as f:
            vault = [bytes([0x40 + i]) * 16 for i in range(0x80)]
            vault[0x12], vault[0x64] = FAKE_KEYS["kirk_key_12"], FAKE_KEYS["kirk_key_64"]
            f.write("keyvault[0x80][0x10] = {\n" + ",\n".join(arr(k) for k in vault) + "};\n")
        with open(os.path.join(d, "ppsspp.log"), "w") as f:
            f.write("sceUtilitySavedataInitStart(08000000) : Game key (hex): "
                    + FAKE_KEYS[B.GAME_KEY].hex() + "\n")
        with FakeFingerprints():
            self.assertEqual(B.main(["--keys", self.file, "keys", "--import", d]), 0)
            self.assertEqual(B.load_keys(Args(self.file)), FAKE_KEYS)
        with open(os.path.join(d, "gamekey.bin"), "wb") as f:
            f.write(FAKE_KEYS[B.GAME_KEY])
        with FakeFingerprints():
            found, _ = B.import_keys([os.path.join(d, "gamekey.bin")])
            self.assertEqual(found, {B.GAME_KEY: FAKE_KEYS[B.GAME_KEY]})


class TestKirk(unittest.TestCase):
    def test_sbox_is_the_aes_sbox(self):
        self.assertEqual((B._SBOX[0x00], B._SBOX[0x01], B._SBOX[0x53], B._SBOX[0xFF]),
                         (0x63, 0x7C, 0xED, 0x16))
        self.assertEqual(sorted(B._SBOX), list(range(256)))

    def test_aes_matches_fips197_vector(self):
        w = B._key_expand(bytes(range(16)))
        ct = bytes.fromhex("69c4e0d8 6a7b0430 d8cdb780 70b4c55a")
        self.assertEqual(B._aes_decrypt_block(w, ct), bytes(i * 0x11 for i in range(16)))

    def test_decrypt_is_deterministic_and_keyed(self):
        raw = bytes(range(256)) * 8
        other = dict(FAKE_KEYS, game_key=bytes(16))
        self.assertEqual(B.psp_decrypt(raw, FAKE_KEYS), B.psp_decrypt(raw, FAKE_KEYS))
        self.assertNotEqual(B.psp_decrypt(raw, FAKE_KEYS), B.psp_decrypt(raw, other))


if __name__ == "__main__":
    unittest.main(verbosity=2)
