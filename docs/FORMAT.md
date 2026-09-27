# Save format notes

Reverse-engineered from `DR1_us.exe` (Steam build of *Danganronpa: Trigger Happy
Havoc*) and from real saves of both versions: the PSP release `NPJH50515`
(Japanese *PSP the Best* build with the Russian fan translation) and the Steam
build. All numbers are hex, integers are little-endian, text is UTF-16LE.

Every offset below comes from one of two sources, noted in the tables:

* **code**: field boundaries, sizes and loop bounds read from the disassembly of
  `DR1_us.exe` (all 2555 accesses to the live game state were collected);
* **data**: byte-for-byte agreement between a PSP save and a PC save made at the
  same story point (prologue pair), cross-checked with the PSP 100 % save
  against two independent PC epilogue saves.

## PSP savedata encryption

`DATKG.BIN` is encrypted by the console with mode 5 (per-game key, firmware
2.5.2+):

| offset | content |
|---|---|
| 0x00 | 16 byte IV, XORed with the 16 byte game key |
| 0x10 | ciphertext, zero padded to a multiple of 0x10 |

The cipher is the Kirk AES-128-CBC keystream generator of `sceChnnlsv`
(PPSSPP `Core/HLE/sceChnnlsv.cpp`). `dr1_save_bridge.py` reimplements it in pure
Python. No key is shipped: the user supplies `game_key`, `key19CC`, `key19DC`
(PPSSPP `Core/HLE/sceChnnlsv.cpp`) and Kirk key vault slots 0x12 and 0x64
(`kirk_key_12`, `kirk_key_64`, PPSSPP `ext/libkirk/kirk_engine.c`) in
`keys.txt`. The tool keeps only SHA-256 fingerprints to validate them.

## PSP core (decrypted `DATKG.BIN`, 0x19698 bytes)

| offset | size | content |
|---|---|---|
| 0x0000 | 4 | magic `64 95 AE 16` |
| 0x0004 | 3 | sound volumes (PSP scale, not transferred) |
| 0x0007 | 2 | difficulty (logic, action), same encoding as PC |
| 0x0014 | 4 | chapter-clear mask (`0xFF` after the epilogue) |
| 0x0018 | 4 | completion flags (`0x100` after the epilogue) |
| 0x001C | 4 | byte sum of 0x00..0x1B |
| 0x0020 | 0x19674 | body: magic `64 95 AE 16`, 4 zero bytes, game state (0x1966C) |
| 0x19694 | 4 | byte sum of 0x20..0x19693 |

The PSP save stores no play time.

## PC `savedata.vfs`

Container: `[u32 name_len][name][u32 size][data]...` + footer `EF BE AD DE`.
Entries are `data%04d.bin` (slots, 0-based) and `icon%04d.png`.

Slot entry (`0x2E4 + 0x3A9B0` bytes):

| offset | size | content |
|---|---|---|
| 0x000 | 0x40 | title `Danganronpa: Trigger Happy Havoc` |
| 0x040 | 0x80 | chapter label (`Prologue School Life: Checkpoint`) |
| 0x0C0 | 0x200 | `Monocoins acquired: Ncoins\nTime Played H:MM:SS\nNumber of times saved: N` |
| 0x2C0 | 0x20 | timestamp `DD.MM.YYYY/HH.MM.SS` |
| 0x2E0 | 4 | core size `0x3A9B0` |
| 0x2E4 | 0x3A9B0 | core |

## PC core (0x3A9B0 bytes)

| offset | size | content |
|---|---|---|
| 0x0000 | 4 | magic `64 95 AE 16` |
| 0x0004 | 8 | options: 3 volumes, 2 difficulty bytes, 3 more option bytes |
| 0x000C | 4 | sub-second play time counter |
| 0x0010 | 3 | play time: seconds, minutes, hours |
| 0x0014 | 4 | extra 24 h blocks, added to the hours |
| 0x0018 | 4 | chapter-clear mask (bit 0x200 is School Mode, PC only) |
| 0x001C | 4 | number of times saved |
| 0x0020 | 4 | completion flags |
| 0x0038 | 4 | **byte sum of 0x00..0x37** |
| 0x003C | 0x3A970 | body |
| 0x3A9AC | 4 | **byte sum of 0x3C..0x3A9AB** |

Body:

| body offset | size | content |
|---|---|---|
| 0x00000 | 4 | magic `64 95 AE 16` |
| 0x00004 | 4 | zero |
| 0x00008 | 0x1D3DC | game state 0: the main game |
| 0x1D3E4 | 0x1D3DC | game state 1: School Mode (PC only) |
| 0x3A7C0 | 0x1B0 | system block: unlocks and settings (PC only) |

Both checksums are verified by the game: the validator at VA `0x43FE00` checks
the magic and the sum over `[+0x00, +0x38)`, the second one is called with
`core + 0x3C`. The save routine at VA `0x43EF10` builds the core from the header
buffer at `0x6A9C70` and the body buffer at `0x6A9CE8`; the function at
`0x43E620` copies the live game state (`0x6E4660`, 0x1D3DC bytes) into game
state slot 0 or 1 and ORs bit `0x80` into its word at `+0x34`.
`0x701A40` is only a snapshot of the body used by the retry feature.

## Game state map (PC game state 0 <- PSP game state)

The PC build is a port of the same source with a few arrays enlarged and a few
fields added. The map below tiles all 0x1D3DC bytes of the PC game state; it is
the `STATE_MAP` table of `dr1_save_bridge.py`. Offsets are relative to the game
state (PC body + 8, PSP body + 8).

| PC | PSP | kind | content | evidence |
|---|---|---|---|---|
| 0x00000-0x00018 | 0x00000-0x0000C | widen | 12 x u8 on PSP, u16 on PC: chapter, scene, variant of the current script and of the calling scripts | code, data |
| 0x00018-0x00140 | 0x0000C-0x00134 | copy | script instruction pointer (u32 +0x18), text id (+0x2E), text offset (+0x30), flag bytes, flag words 0-1 at +0x138 | code, data |
| 0x00140-0x00148 | - | zero | flag words 2-3 (PC clears `u32[4]` at +0x138 with two 8-byte stores) | code |
| 0x00148-0x00648 | 0x00134-0x00634 | copy | items, Monocoins (+0x168), progress tables | data |
| 0x00648-0x18C4E | 0x00634-0x1503A | log | message log, see below | code, data |
| 0x18C4E-0x18C50 | 0x1503A-0x1503C | copy | two u8 fields | code, data |
| 0x18C50-0x19010 | 0x1503C-0x15366 | zero | text window, 5 rows of text and attributes (PSP 54 chars, PC 64) | code, data |
| 0x19010-0x19018 | 0x15366-0x1536E | window | text window state, cleared together with the window | code |
| 0x19018-0x194E6 | 0x1536E-0x1583C | copy | choice menu: `u16[0xFA]` x 2, `u16[20]` x 2, `u8[40]`, `u8` count, `u16[30]`, `u8[30]` | code, data |
| 0x194E6-0x194E8 | - | zero | alignment padding: the following `u32` is 4-aligned on both builds | code |
| 0x194E8-0x1954A | 0x1583C-0x1589E | copy | trial state, count of trial records at +0x19548 | code, data |
| 0x1954A-0x1A63E | 0x1589E-0x16992 | copy | table A, records 0-216 of 10 x u16 | code |
| 0x1A63E-0x1A6A2 | - | zero | table A, records 217-221 | code |
| 0x1A6A2-0x1B796 | 0x16992-0x17A86 | copy | table B, records 0-216 | code |
| 0x1B796-0x1B7FA | - | zero | table B, records 217-221 | code |
| 0x1B7FA-0x1BF90 | 0x17A86-0x1821C | copy | tables C and D (`u8[222]` each), state, current trial record | code, data |
| 0x1BF90-0x1BF94 | - | zero | last field of the current trial record (24 bytes on PC, 20 on PSP) | code, data |
| 0x1BF94-0x1D02C | 0x1821C-0x192B4 | copy | trial records `[20]`, per chapter records `[7][20]` of 24 bytes, flags | code, data |
| 0x1D02C-0x1D03C | 0x192B8-0x192C8 | copy | four u8 fields, reserved | code, data |
| 0x1D03C-0x1D3DC | - | zero | PC-only progress counters, reserved | code |

PSP bytes that are not in the map:

* `0x192B4` u32 number of times saved, which the PC stores in the core header;
* the message log and the text window, which are language specific;
* `0x192C8-0x1966C`, zero in every PSP save examined.

### Evidence notes

* **Log geometry.** The PC keeps 0x200 rows of 64 chars (`meta` 3 bytes per row
  at +0x64E, text at +0xC4E, attributes at +0x10C4E). The PSP arrays have the
  same row count with 54 chars (meta at +0x63A, text at +0xC3A, attributes at
  +0xE43A, end at +0x1503A); these bases make the meta, text, attribute and
  window blocks meet exactly. The Russian fan translation writes 66 char rows
  from the same bases, so its rows appear at a 0x84 byte pitch.
* **Tables A and B.** The PC clears both with one loop over 222 records of
  10 x u16 (`0x445A1D`), together with the byte tables C and D. The shift of the
  following data grows by exactly 0xC8 = 2 x 5 records, so the PSP tables hold
  217 records. The records are zero in both PSP saves examined, so the split is
  inferred from the sizes.
* **Trial records.** `0x43E49A` fills a 24-byte record at +0x1BF7C from the
  current trial fields; `0x437E26` copies records 1-20 (0x1E0 bytes) into the
  per chapter array at +0x1C174. The per chapter array sits at PSP +0x183FC with
  the same 0x1E0 pitch; its rows match the PC rows field by field.
* **Choice menu padding.** P7 bytes at PSP +0x157BA equal three PC saves at
  +0x19464 (shift +0x3CAA), and the fields at PSP +0x1585A equal them at shift
  +0x3CAC; the only place in between where two bytes can go without breaking
  the 4-byte alignment of the `u32` at PC +0x194E8 is after `u8[30]`.

### Fields that do not carry over verbatim

| field | PC | handling |
|---|---|---|
| script instruction pointer | +0x18 u32 | byte offset inside the current script file (chapter, scene, variant at +0x00). Reset to 0 by default: PSP and PC script files differ (the same prologue point is `0x0AFA` on PSP and `0x0B02` on PC), so the current script restarts from its beginning. Real PC saves normally hold a nonzero offset here (15 of 17 examined); 0 occurs in saves made at the start of a script, e.g. the end-of-game saves. |
| text offset | +0x30 u16 | reset to 0 together with the pointer |
| save flag word | +0x34 | bit `0x80` is set, as the PC save routine does |
| message log | +0x648.. | cleared (flag 0, `0xFF`, head 0, write 0) or rebuilt from the PSP lines with `--backlog keep` |
| difficulty | core 0x07, 0x08 | copied from the PSP core header |
| chapter-clear mask, completion flags | core 0x18, 0x20 | copied from PSP core 0x14, 0x18 |
| number of times saved | core 0x1C | from PSP game state +0x192B4 |
| play time | core 0x0C..0x17 | 0, the PSP save has none |

## PC-only data

The converter writes the values of a fresh PC game, taken from a new-game PC
save and stored in the tool as run-length tables:

* option bytes `32 46 64 xx xx 01 02 00` (difficulty from the PSP save);
* text window state `02 00 00 00 00 00 00 00`;
* game state 1 (School Mode) as after starting a new game;
* the system block.

When the target `savedata.vfs` already has healthy slots, the option bytes and
the system block of the newest one are reused instead, so the player keeps
their settings and unlocks. The School Mode state is always fresh.

## Verification

`python dr1_save_bridge.py verify DATKG.BIN savedata.vfs --slot N` converts a PSP
save without any template and compares the result with a real PC slot, row by
row of the map.

* Prologue pair (PSP and PC saves at the same checkpoint): 17812 of 17816
  transferred bytes are identical. The four others are the two script offsets
  and one volatile byte of the current trial record (+0x1BF88). Outside the
  map, only the message log, stale text window attributes, the play time and
  the checksums differ.
* PSP 100 % save against two PC epilogue saves of other players: no shifted
  block anywhere; the differences are limited to playthrough-dependent data
  (inventory, trial scores and times, PC-only counters).
