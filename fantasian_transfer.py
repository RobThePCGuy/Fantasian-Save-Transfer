#!/usr/bin/env python3
"""Move a FANTASIAN save from Apple Arcade to FANTASIAN Neo Dimension on Steam.

Both releases store the same save payload. Only the container around it differs:

    Apple Arcade   SaveDataEntity.sqlite -> ZGAMEDATAENTITY.ZDATA (zlib compressed)
                   -> {"records": [{"path": "Data/GameData0.json",
                                    "dataString": "<plaintext json>"}, ...]}

    Neo Dimension  .../FANTASIAN Neo Dimension/Steam/<steamid>/_data/root.json
                   -> {"dataString": "{\"records\": [{\"path\": ...,
                                       \"encryptedString\": \"<aes-cbc + base64>\"}]}"}

So the conversion is: pull the records out of the sqlite blob, AES-encrypt each one,
and rewrap.

Needs nothing but Python 3.8+. No pip install, no compiler. The AES lives in this file.
"""

import argparse
import base64
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import zipfile
import zlib

__version__ = "1.0.0"

# The key is baked into the game and is the same on every platform and every copy.
AES_IV = b"Nq4G3pTQFLTCeiB7"
AES_KEY = b"yrhWj8EiU83kXupm"

APPLE_ARCADE_SAVE_DIR = os.path.join(
    os.path.expanduser("~"), "Library", "Containers",
    "com.mistwalkercorp.fantasian", "Data", "Library",
    "Application Support", "FANTASIAN")

STEAM_APP_ID = "2844850"
GAME_DIR_NAME = "FANTASIAN Neo Dimension"


# ---------------------------------------------------------------------------
# AES-128-CBC with PKCS#7, standard library only.
#
# Verified byte-for-byte against pycryptodome on the FIPS-197 C.1 known-answer
# vector, on 500 random key/iv/length combinations, and on real save records.
# ---------------------------------------------------------------------------

_SBOX = bytearray(256)
_INV_SBOX = bytearray(256)


def _build_sbox():
    p = q = 1
    _SBOX[0] = 0x63
    while True:
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= (q << 1) & 0xFF
        q ^= (q << 2) & 0xFF
        q ^= (q << 4) & 0xFF
        if q & 0x80:
            q ^= 0x09
        x = q ^ ((q << 1) | (q >> 7)) ^ ((q << 2) | (q >> 6))
        x ^= ((q << 3) | (q >> 5)) ^ ((q << 4) | (q >> 4))
        _SBOX[p] = (x ^ 0x63) & 0xFF
        if p == 1:
            break
    for i, v in enumerate(_SBOX):
        _INV_SBOX[v] = i


_build_sbox()

_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1B, 0x36)


def _xtime(a):
    a <<= 1
    return (a ^ 0x1B) & 0xFF if a & 0x100 else a


_MUL = {}
for _factor in (2, 3, 9, 11, 13, 14):
    _table = bytearray(256)
    for _b in range(256):
        _acc, _cur, _m = 0, _b, _factor
        while _m:
            if _m & 1:
                _acc ^= _cur
            _cur = _xtime(_cur)
            _m >>= 1
        _table[_b] = _acc
    _MUL[_factor] = _table


def _expand_key(key):
    if len(key) != 16:
        raise ValueError("AES-128 only (16-byte key)")
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [_SBOX[b] for b in t[1:] + t[:1]]
            t[0] ^= _RCON[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [sum(w[r * 4:r * 4 + 4], []) for r in range(11)]


def _encrypt_block(block, rk):
    s = [block[i] ^ rk[0][i] for i in range(16)]
    m2, m3 = _MUL[2], _MUL[3]
    for rnd in range(1, 11):
        s = [_SBOX[b] for b in s]
        s = [s[0], s[5], s[10], s[15], s[4], s[9], s[14], s[3],
             s[8], s[13], s[2], s[7], s[12], s[1], s[6], s[11]]
        if rnd != 10:
            t = []
            for c in range(0, 16, 4):
                a0, a1, a2, a3 = s[c:c + 4]
                t += [m2[a0] ^ m3[a1] ^ a2 ^ a3,
                      a0 ^ m2[a1] ^ m3[a2] ^ a3,
                      a0 ^ a1 ^ m2[a2] ^ m3[a3],
                      m3[a0] ^ a1 ^ a2 ^ m2[a3]]
            s = t
        k = rk[rnd]
        s = [s[i] ^ k[i] for i in range(16)]
    return bytes(s)


def _decrypt_block(block, rk):
    s = [block[i] ^ rk[10][i] for i in range(16)]
    m9, m11, m13, m14 = _MUL[9], _MUL[11], _MUL[13], _MUL[14]
    for rnd in range(9, -1, -1):
        s = [s[0], s[13], s[10], s[7], s[4], s[1], s[14], s[11],
             s[8], s[5], s[2], s[15], s[12], s[9], s[6], s[3]]
        s = [_INV_SBOX[b] for b in s]
        k = rk[rnd]
        s = [s[i] ^ k[i] for i in range(16)]
        if rnd != 0:
            t = []
            for c in range(0, 16, 4):
                a0, a1, a2, a3 = s[c:c + 4]
                t += [m14[a0] ^ m11[a1] ^ m13[a2] ^ m9[a3],
                      m9[a0] ^ m14[a1] ^ m11[a2] ^ m13[a3],
                      m13[a0] ^ m9[a1] ^ m14[a2] ^ m11[a3],
                      m11[a0] ^ m13[a1] ^ m9[a2] ^ m14[a3]]
            s = t
    return bytes(s)


def encrypt(data):
    rk = _expand_key(AES_KEY)
    padding = 16 - (len(data) % 16)
    data += bytes([padding]) * padding
    out, prev = bytearray(), AES_IV
    for i in range(0, len(data), 16):
        prev = _encrypt_block(bytes(a ^ b for a, b in zip(data[i:i + 16], prev)), rk)
        out += prev
    return bytes(out)


def decrypt(data):
    if not data or len(data) % 16:
        raise ValueError("not a whole number of AES blocks")
    rk = _expand_key(AES_KEY)
    out, prev = bytearray(), AES_IV
    for i in range(0, len(data), 16):
        block = data[i:i + 16]
        out += bytes(a ^ b for a, b in zip(_decrypt_block(block, rk), prev))
        prev = block
    padding = out[-1]
    if not 1 <= padding <= 16 or bytes(out[-padding:]) != bytes([padding]) * padding:
        raise ValueError("bad padding, this does not look like a FANTASIAN save")
    return bytes(out[:-padding])


# ---------------------------------------------------------------------------
# Reading the Apple Arcade side
# ---------------------------------------------------------------------------

class SaveError(Exception):
    """Something the user can act on. Printed without a traceback."""


def _records_from_blob(raw):
    if raw[:1] == b"\x78":
        raw = zlib.decompress(raw)
    obj = json.loads(raw.decode("utf-8"))
    if "records" not in obj and "dataString" in obj:
        obj = json.loads(obj["dataString"])
    if "records" not in obj:
        raise SaveError("that file has no save records in it")
    return obj["records"]


def _read_sqlite(db_path):
    """Read ZGAMEDATAENTITY without touching the file we were pointed at.

    Opening a sqlite database with a write-ahead log checkpoints it, folding the
    -wal back into the .sqlite and rewriting both. On a live game save that is a
    real edit to the player's files. So the database and its sidecars are copied
    somewhere disposable and the copy is opened instead.
    """
    tmp = tempfile.mkdtemp(prefix="fantasian_")
    try:
        target = os.path.join(tmp, "SaveDataEntity.sqlite")
        shutil.copyfile(db_path, target)
        sidecars = 0
        for suffix in ("-wal", "-shm"):
            sidecar = db_path + suffix
            if os.path.exists(sidecar):
                shutil.copyfile(sidecar, target + suffix)
                sidecars += 1

        con = sqlite3.connect(target)
        try:
            rows = list(con.execute(
                "SELECT ZID, ZDATA FROM ZGAMEDATAENTITY ORDER BY ZTIME DESC"))
        except sqlite3.DatabaseError as exc:
            # A save copied away from its -wal often has not even had the table
            # written into the .sqlite yet, so this lands here rather than on an
            # empty result. Same cause, same advice.
            raise SaveError(_no_wal_advice(db_path, sidecars, str(exc)))
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for zid, zdata in rows:
        if zid and "root" in zid.lower() and zdata:
            return _records_from_blob(zdata)

    raise SaveError(_no_wal_advice(db_path, sidecars, "it holds no save rows"))


def _no_wal_advice(db_path, sidecars, detail):
    name = os.path.basename(db_path)
    if sidecars:
        return (f"There is no FANTASIAN save in {name} ({detail}).\n"
                "Its -wal file was there and was read, so this does not look like a "
                "FANTASIAN save database.")
    return (
        f"There is no FANTASIAN save in {name} ({detail}), and its -wal file was not "
        "next to it.\n\n"
        "FANTASIAN keeps your current progress in SaveDataEntity.sqlite-wal, not in the "
        ".sqlite itself, so copying the .sqlite on its own gets you an empty database.\n"
        "Copy the whole FANTASIAN folder across, or at least the .sqlite together with "
        "its -wal and -shm files, and try again.")


def _read_zip(zip_path):
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist()
                 if os.path.basename(n).startswith("SaveDataEntity.sqlite")
                 and not os.path.basename(n).startswith("._")]
        if not names:
            raise SaveError(f"no SaveDataEntity.sqlite inside {zip_path}")
        tmp = tempfile.mkdtemp(prefix="fantasian_zip_")
        try:
            for n in names:
                with open(os.path.join(tmp, os.path.basename(n)), "wb") as f:
                    f.write(z.read(n))
            return _read_sqlite(os.path.join(tmp, "SaveDataEntity.sqlite"))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def load_source_records(path):
    """Accepts a zip of the FANTASIAN folder, the folder itself, a bare .sqlite,
    a ZGAMEDATAENTITY table dump, or an already-converted root.json."""
    if not os.path.exists(path):
        raise SaveError(f"{path} does not exist")

    if os.path.isdir(path):
        inner = os.path.join(path, "SaveDataEntity.sqlite")
        if not os.path.isfile(inner):
            raise SaveError(f"no SaveDataEntity.sqlite inside the folder {path}")
        return _read_sqlite(inner)

    ext = os.path.splitext(path)[1].lower()
    if ext == ".zip":
        return _read_zip(path)
    if ext in (".sqlite", ".db", ".sqlite3"):
        return _read_sqlite(path)

    with open(path, "rb") as f:
        blob = f.read()
    if blob.lstrip()[:1] == b"[":
        for row in json.loads(blob.decode("utf-8")):
            if "ZDATA" in row and "root" in str(row.get("ZID", "root")).lower():
                return _records_from_blob(base64.b64decode(row["ZDATA"]))
        raise SaveError(f"no row with a ZDATA blob in {path}")
    return _records_from_blob(blob)


def find_apple_arcade_save():
    db = os.path.join(APPLE_ARCADE_SAVE_DIR, "SaveDataEntity.sqlite")
    return db if os.path.isfile(db) else None


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------

SLOT_NAMES = {"0": "slot 1", "1": "slot 2", "2": "slot 3", "10": "autosave"}


def record_plaintext(record):
    if "dataString" in record:
        return record["dataString"]
    return decrypt(base64.b64decode(record["encryptedString"])).decode("utf-8")


def slot_number(path):
    base = os.path.basename(path)
    if base.startswith("GameData") and base.endswith(".json"):
        return base[len("GameData"):-len(".json")]
    return base


def describe(record):
    n = slot_number(record.get("path", "?"))
    label = SLOT_NAMES.get(n, f"slot {n}")
    try:
        data = json.loads(record_plaintext(record))
        d = dict(zip(data["keys"], data["values"]))
        info = json.loads(d.get("GameSystemInfo", "{}"))
        hours = info.get("_playTimeSec", 0) / 3600.0
        return (f"{label:<10} {d.get('Date', '?'):<20} {hours:5.1f}h "
                f"{info.get('_money', 0):>9,} G   {info.get('_mapId', '?')}")
    except Exception as exc:
        return f"{label:<10} <could not read this slot: {exc}>"


def to_steam_record(record):
    return {
        "path": record["path"],
        "encryptedString": base64.b64encode(
            encrypt(record_plaintext(record).encode("utf-8"))).decode("utf-8"),
    }


def canonical_order(records):
    """Order records the way the game writes them: manual slot 1, the autosave,
    then the remaining manual slots.

    This is not cosmetic. mathcodergamer's fantasia.py save editor indexes slots
    positionally, treating records[1] as the autosave, so a file in a different
    order makes it edit the wrong save.
    """
    def key(record):
        n = slot_number(record["path"])
        if not n.isdigit():
            return (3, 0, n)
        n = int(n)
        if n == 0:
            return (0, 0, "")
        if n == 10:
            return (1, 0, "")
        return (2, n, "")
    return sorted(records, key=key)


# ---------------------------------------------------------------------------
# Finding the Steam save
# ---------------------------------------------------------------------------

def _windows_documents():
    """Where Windows actually keeps Documents for this user.

    Guessing ~/Documents is wrong on any machine where the folder has been
    redirected, which OneDrive does by default on a lot of installs. Windows
    records the real location, so ask it rather than guess.
    """
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders")
        with key:
            value, _ = winreg.QueryValueEx(key, "Personal")
        return os.path.expandvars(value) if value else None
    except OSError:
        return None


def _documents_dirs():
    """Every plausible Documents folder. Windows redirects it into OneDrive on a
    lot of machines, which is why this is a list and not a path."""
    home = os.path.expanduser("~")
    out = []
    real = _windows_documents()
    if real:
        out.append(real)
    out.append(os.path.join(home, "Documents"))
    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    if onedrive:
        out.append(os.path.join(onedrive, "Documents"))
    out.append(os.path.join(home, "OneDrive", "Documents"))
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        out.append(os.path.join(userprofile, "Documents"))
        out.append(os.path.join(userprofile, "OneDrive", "Documents"))
    # Proton / Steam Deck: the game runs inside a Windows prefix.
    home_ = home
    prefixes = [
        os.path.join(home_, ".local", "share", "Steam"),
        os.path.join(home_, ".steam", "steam"),
        os.path.join(home_, ".var", "app", "com.valvesoftware.Steam",
                     ".local", "share", "Steam"),
    ]
    for root in prefixes:
        out.append(os.path.join(
            root, "steamapps", "compatdata", STEAM_APP_ID, "pfx",
            "drive_c", "users", "steamuser", "Documents"))
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def find_steam_roots():
    """Every root.json we can find, newest first."""
    found = []
    for documents in _documents_dirs():
        steam_dir = os.path.join(documents, "My Games", GAME_DIR_NAME, "Steam")
        if not os.path.isdir(steam_dir):
            continue
        for entry in sorted(os.listdir(steam_dir)):
            candidate = os.path.join(steam_dir, entry, "_data", "root.json")
            if os.path.isfile(candidate):
                found.append(candidate)
    found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return found


def backup_file(path):
    stamp = time.strftime("%Y%m%d_%H%M%S")
    dest = f"{path}.backup_{stamp}"
    shutil.copy2(path, dest)
    return dest


# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="fantasian_transfer.py",
        description="Move a FANTASIAN save from Apple Arcade to Neo Dimension on Steam.",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, width=96),
        epilog="Run with no arguments on the Mac you played on and it will find the save "
               "itself.")
    p.add_argument(
        "source", nargs="?",
        help="the FANTASIAN folder off your Apple device, a zip of it, "
             "SaveDataEntity.sqlite, or a ZGAMEDATAENTITY dump. Left out, the Apple "
             "Arcade save installed on this Mac is used.")
    p.add_argument("-o", "--output", default="root.json",
                   help="where to write the Neo Dimension save (default: root.json)")
    p.add_argument("-l", "--list", action="store_true",
                   help="show what is in the save and stop")
    p.add_argument("-t", "--template", metavar="ROOT_JSON",
                   help="an existing Neo Dimension root.json to merge into, so slots the "
                        "Apple Arcade save does not cover survive")
    p.add_argument("--auto-template", action="store_true",
                   help="find the Neo Dimension save on this machine and merge into it")
    p.add_argument("--slots", metavar="N", nargs="+", default=None,
                   help="only bring these slots across, by GameData number "
                        "(0 = slot 1, 1 = slot 2, 2 = slot 3, 10 = autosave)")
    p.add_argument("--install", action="store_true",
                   help="write straight into the Neo Dimension save folder on this "
                        "machine, backing up what is there first. Close the game first.")
    p.add_argument("--self-test", action="store_true",
                   help="check the built-in encryption against the published AES test "
                        "vectors and exit")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def self_test():
    """The encryption in this file is hand-written, so it gets to prove itself.

    The two blocks are the FIPS-197 appendix C.1 known-answer vector for AES-128
    and appendix F.2 of NIST SP 800-38A for CBC chaining.
    """
    rk = _expand_key(bytes.fromhex("000102030405060708090a0b0c0d0e0f"))
    block = bytes.fromhex("00112233445566778899aabbccddeeff")
    want = bytes.fromhex("69c4e0d86a7b0430d8cdb78070b4c55a")
    got = _encrypt_block(block, rk)
    if got != want:
        raise SaveError(f"AES self-test failed: got {got.hex()}, expected {want.hex()}")
    if _decrypt_block(want, rk) != block:
        raise SaveError("AES self-test failed: decrypt did not undo encrypt")
    print("AES-128 block          FIPS-197 C.1 vector matches")

    # SP 800-38A F.2.1, CBC-AES128, first two blocks. Checked without the padding
    # this tool adds, by encrypting exactly two whole blocks and dropping the
    # padding block the CBC helper appends.
    key = bytes.fromhex("2b7e151628aed2a6abf7158809cf4f3c")
    iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    plain = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a"
                          "ae2d8a571e03ac9c9eb76fac45af8e51")
    want_cbc = bytes.fromhex("7649abac8119b246cee98e9b12e9197d"
                             "5086cb9b507219ee95db113a917678b2")
    rk2 = _expand_key(key)
    out, prev = bytearray(), iv
    for i in range(0, len(plain), 16):
        prev = _encrypt_block(bytes(a ^ b for a, b in zip(plain[i:i + 16], prev)), rk2)
        out += prev
    if bytes(out) != want_cbc:
        raise SaveError("CBC self-test failed")
    print("AES-128-CBC chaining   NIST SP 800-38A F.2.1 vector matches")

    # And the round trip this tool actually depends on.
    sample = json.dumps({"keys": ["Version"], "values": ["1"]}).encode("utf-8")
    if decrypt(encrypt(sample)) != sample:
        raise SaveError("round-trip self-test failed")
    print("Save round trip        encrypt then decrypt returns the original")
    print("\nEncryption is good. This build can read and write FANTASIAN saves.")
    return 0


def main(argv=None):
    args = build_parser().parse_args(argv)

    if args.self_test:
        return self_test()

    source = args.source
    if source is None:
        source = find_apple_arcade_save()
        if not source:
            build_parser().print_usage()
            raise SaveError(
                "no Apple Arcade FANTASIAN save on this machine, and no file was given.\n"
                f"Looked in {APPLE_ARCADE_SAVE_DIR}\n"
                "If you played on another Mac, copy that FANTASIAN folder over and pass "
                "it as an argument.")
        print(f"Reading the Apple Arcade save installed on this Mac.")

    records = load_source_records(source)
    if not records:
        raise SaveError("that save has no slots in it")

    print(f"\nFound {len(records)} save slot(s):\n")
    for r in canonical_order(records):
        print("  " + describe(r))
    print()
    if args.list:
        return 0

    if args.slots is not None:
        wanted = set(args.slots)
        kept = [r for r in records
                if r["path"] in wanted or slot_number(r["path"]) in wanted]
        if not kept:
            raise SaveError(
                f"--slots {' '.join(args.slots)} matched none of these slots. "
                f"Available: {' '.join(sorted(slot_number(r['path']) for r in records))}")
        records = kept

    # Where are we writing?
    install_target = None
    if args.install:
        roots = find_steam_roots()
        if not roots:
            raise SaveError(
                "--install could not find a Neo Dimension save on this machine.\n"
                "Start the game, make one save, and try again. Or drop the --install and "
                "copy the root.json across yourself.")
        install_target = roots[0]
        print(f"Neo Dimension save: {install_target}")

    template_path = args.template
    if args.auto_template and not template_path:
        roots = find_steam_roots()
        if roots:
            template_path = roots[0]
            print(f"Merging into {template_path}")
        else:
            print("No Neo Dimension save found to merge into, writing a fresh one.",
                  file=sys.stderr)
    if install_target and not template_path:
        template_path = install_target

    if template_path:
        with open(template_path, encoding="utf-8") as f:
            root = json.load(f)
        if "dataString" not in root:
            raise SaveError(f"{template_path} is not a Neo Dimension root.json")
        existing = json.loads(root["dataString"])["records"]
        print(f"\nIt already holds {len(existing)} slot(s):\n")
        for r in canonical_order(existing):
            print("  " + describe(r))
        print()
    else:
        print("Writing a fresh save file. If the game will not load it, play far enough "
              "to save once, then rerun with --auto-template.\n")
        root, existing = {}, []

    by_path = {r["path"]: i for i, r in enumerate(existing)}
    print("Bringing across:")
    for src in canonical_order(records):
        converted = to_steam_record(src)
        label = SLOT_NAMES.get(slot_number(converted["path"]), converted["path"])
        if converted["path"] in by_path:
            existing[by_path[converted["path"]]] = converted
            print(f"  {label:<10} overwrites the Neo Dimension one")
        else:
            existing.append(converted)
            print(f"  {label:<10} added")

    # Order matters to the fantasia.py save editor, which indexes slots positionally.
    existing = canonical_order(existing)
    root["dataString"] = json.dumps({"records": existing}, separators=(",", ":"))

    # Everything written must decrypt back to a save the game can parse.
    for r in json.loads(root["dataString"])["records"]:
        data = json.loads(record_plaintext(r))
        if "keys" not in data or "values" not in data:
            raise SaveError(f"{r['path']} did not survive the round trip")

    if install_target:
        saved = backup_file(install_target)
        print(f"\nBacked up your Neo Dimension save to\n  {saved}")
        out_path = install_target
    else:
        out_path = args.output

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(root, f, indent=4)

    print(f"\nWrote {out_path} with {len(existing)} slot(s).")

    if not any(slot_number(r["path"]) == "10" for r in existing):
        # fantasia.py assumes the second record is always the autosave. Without
        # one it reads a real manual slot as the autosave and hides it.
        print("\nHeads up: this save has no autosave slot in it. The game is fine with "
              "that, but the FantasianND Save Editor assumes every save has one and "
              "will treat your slot 2 as the autosave. Play until the game writes its "
              "own autosave before editing.")
    if install_target:
        print("\nStart the game and load the slot to check it before you rely on it. "
              "To undo, copy the backup above back over root.json.")
    else:
        print("\nBack up the root.json already in your Neo Dimension save folder, then "
              "copy this one over it:")
        print(f"  Windows     %USERPROFILE%\\Documents\\My Games\\{GAME_DIR_NAME}"
              f"\\Steam\\<your steam id>\\_data\\root.json")
        print(f"  Steam Deck  ~/.local/share/Steam/steamapps/compatdata/{STEAM_APP_ID}"
              f"/pfx/drive_c/users/steamuser/Documents/My Games/{GAME_DIR_NAME}/...")
        print("\nClose the game before swapping the file, then load the slot and check "
              "it before you rely on it.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SaveError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
