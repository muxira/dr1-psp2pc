<div align="center">

# 🐰 dr1-save-bridge

**Move your Danganronpa: Trigger Happy Havoc save from PSP to PC (Steam)**

Story progress, flags, items, Monocoins and the save counter, straight into a PC
slot — as if you had played there. No PC save needed as a template.

![python](https://img.shields.io/badge/python-3.8%2B-blue) ![deps](https://img.shields.io/badge/dependencies-none-brightgreen) ![platform](https://img.shields.io/badge/PSP-NPJH50515-lightgrey) ![target](https://img.shields.io/badge/PC-Steam-black) ![license](https://img.shields.io/badge/license-MIT-green)

</div>

---

## ✨ What moves over

| | |
|---|---|
| 📖 Chapter, scene, script and story position | ✅ |
| 🚩 Progress flags, choices, trial records | ✅ |
| 🎒 Items and game data tables | ✅ |
| 🪙 Monocoins | ✅ |
| 🔢 Number of times saved, difficulty, chapter-clear flags | ✅ |
| 💬 Dialogue log | 🧹 cleared by default, optional with `--backlog keep` |
| ⏱️ Play time | 0:00:00 (the PSP save does not store it) |
| ⚙️ Options and unlocks | taken from your newest PC slot, or fresh-game defaults |

> [!IMPORTANT]
> **Status.** No PC save is needed as a template. The converter builds the
> whole PC slot itself: every byte of the PC game state is either transferred
> from the PSP save through the offset map in `docs/FORMAT.md` or set to the
> value a fresh PC game uses. The map was derived from the game executable and
> checked against real saves: a PSP prologue save converts to a game state that
> is identical to a real PC prologue save on 17812 of the 17816 transferred bytes
> (the rest are the script offsets, see [Limitations](#-limitations)).
>
> It has not been tested in the game yet. Run it on a copy the first time —
> rollback is one file copy, see [Rollback](#-rollback).

## 📦 What you need

- **Python 3.8+**, nothing else
- A PSP save folder `NPJH50515DATKG000x` with `DATKG.BIN` from
  `ms0:/PSP/SAVEDATA/`.
- The decryption keys in `keys.txt`, see [Keys](#-keys). They are not shipped
  with the tool.
- Optional: an existing PC `savedata.vfs`. Its other slots are kept, and the
  settings and unlocks of its newest slot are reused. Without one, a new
  `savedata.vfs` is created.

## 🚀 Quick start (Windows)

1. Close the game.
2. Set up `keys.txt` once, see [Keys](#-keys).
3. Run **`install.bat`**.
4. Drag the PSP `DATKG.BIN` into the window, pick the PC slot number, repeat for
   more saves, press Enter on an empty line when you are done.

The script

- copies your whole `savedata.*` set into `backups\<timestamp>\` first,
- verifies the checksums before and after,
- puts the original file back if anything goes wrong.

PC saves live in `%USERPROFILE%\Documents\My Games\Danganronpa\savedata.vfs`.

## 🖥️ Command line

```bash
# what is in the file
python dr1_save_bridge.py info savedata.vfs
python dr1_save_bridge.py info NPJH50515DATKG0000/DATKG.BIN   # decrypts it
python dr1_save_bridge.py probe savedata.vfs                  # full structure dump

# one PSP save -> PC slot 1 of a brand new savedata.vfs
python dr1_save_bridge.py convert --pc-vfs none.vfs \
    --map 1=NPJH50515DATKG0000/DATKG.BIN --out savedata.vfs

# two saves, written straight into the game folder, with a backup
python dr1_save_bridge.py convert \
    --map 1=NPJH50515DATKG0001/DATKG.BIN \
    --map 2=NPJH50515DATKG0000/DATKG.BIN --install

# check the map yourself: convert without a template and compare with a real
# PC slot saved at the same story point
python dr1_save_bridge.py verify NPJH50515DATKG0000/DATKG.BIN savedata.vfs --slot 1
```

| flag | |
|---|---|
| `--map SLOT=FILE` | PC slot number (from 1) = PSP save; repeat for more |
| `--keys FILE` | keys file, default `keys.txt` next to the script (global option) |
| `--key HEX` | game key, overrides the one in the keys file (global option) |
| `--system-slot N` | PC slot whose settings and unlocks are reused (default: newest) |
| `--default-system` | ignore existing slots, use fresh-game settings and unlocks |
| `--backlog clear\|keep` | clear the dialogue log (default) or copy the PSP lines |
| `--script zero\|copy` | restart the current script (default) or copy the PSP offset |
| `--install` | overwrite the input file, keeping `savedata.vfs.<stamp>.bak` |
| `--force` | overwrite `--out` without `--install` |

`--install` with no `--pc-vfs` and no `--out` uses
`%USERPROFILE%\Documents\My Games\Danganronpa\savedata.vfs` directly.

## 🔐 Keys

`DATKG.BIN` is encrypted by the console itself with the Kirk AES stream cipher
(mode 5, per-game key). The cipher is reimplemented here in pure Python, so **no
external tool is needed**, but **no key is shipped with it**: you provide five
16-byte keys in `keys.txt`, which git ignores.

| key | where to find it |
|---|---|
| `key19CC`, `key19DC` | PPSSPP source, `Core/HLE/sceChnnlsv.cpp` |
| `kirk_key_12`, `kirk_key_64` | PPSSPP source, `ext/libkirk/kirk_engine.c` (key vault slots 0x12 and 0x64) |
| `game_key` | PPSSPP log: run the game with logging on, load or save once, look for `Game key (hex):` |

The tool finds them for you in those files, by fingerprint:

```bash
python dr1_save_bridge.py keys --import path/to/ppsspp-source path/to/ppsspp.log
python dr1_save_bridge.py keys --game-key <32 hex digits>   # or set it by hand
python dr1_save_bridge.py keys                               # check what is set
```

Or copy `keys.example.txt` to `keys.txt` and fill it in. Every value is checked
against a SHA-256 fingerprint, so a wrong key is reported by name instead of
producing garbage. Already decrypted cores (0x19698 bytes) need no keys.

## 🔬 How it works

Both saves are memory images of the same game state structure. The PSP build
stores it in 0x1966C bytes; the PC build in 0x1D3DC bytes, because a few arrays
are larger (the dialogue log, two data tables) and a few fields were added.
The PC slot also holds a second game state for School Mode and a system block,
which the PSP build does not have. The converter walks a map that covers every
byte of the PC game state (`STATE_MAP`, documented in `docs/FORMAT.md`), fills
the PC-only parts with fresh-game values, and computes both checksums the PC
build verifies.

```bash
python -m unittest discover -s tests    # synthetic data only
```

## 💬 Dialogue log

The log holds 0x200 lines of 54 chars on PSP (66 in the Russian fan
translation, detected automatically) and 64 chars on PC.

* `--backlog clear` (default) empties it, exactly like a fresh game.
* `--backlog keep` copies the lines with their speaker, splitting anything
  longer than a PC row at a space. The English PC font may have no Cyrillic
  glyphs, so the text can come out as boxes. It has not been tested in the game.

## ↩️ Rollback

Copy `backups\<timestamp>\savedata.vfs` (or the `.bak` the tool prints) back into
`%USERPROFILE%\Documents\My Games\Danganronpa\`.

## ⚠️ Limitations

- Tested for PSP **NPJH50515** ("Danganronpa RUS / PSP the Best") → Steam.
- No PC → PSP direction.
- The position inside the current script is reset by default. The save knows
  which script file is running and the byte offset inside it, but the PSP and
  PC script files differ (the same prologue point is at 0x0AFA on PSP and
  0x0B02 on PC), so a PSP offset would point into the middle of a PC
  instruction. With the offset at 0 the game runs the current script file from
  its beginning: you replay the current scene segment up to the point where
  you saved. Story flags, items and Monocoins are already in the save; how the
  replayed part behaves has not been tested in the game yet. `--script copy`
  keeps the PSP offset and is only safe if the script files are identical.
- School Mode progress and the PC-only progress counters start fresh; a 100 %
  PSP save keeps its chapter-clear flags but does not unlock PC-only content by
  itself.
- This project is not affiliated with Spike Chunsoft. Saves, keys and game files
  are not part of the repository (see `.gitignore`).

## 📄 License

[MIT](LICENSE) © 2026 muxira

---

<div align="center"><sub>Hope's Peak save transfer. 🔨</sub></div>
