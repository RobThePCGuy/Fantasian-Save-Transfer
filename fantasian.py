#!/usr/bin/env python3
"""FANTASIAN save tool.

Moves a save between Apple Arcade accounts, carries one from Apple Arcade into
FANTASIAN Neo Dimension on Steam, and edits either one.

Both releases store the same save payload. Only the container differs:

    Apple Arcade   SaveDataEntity.sqlite -> ZGAMEDATAENTITY.ZDATA, zlib compressed
                   -> {"records": [{"path": "Data/GameData0.json",
                                    "dataString": "<plain json>"}, ...]}

    Neo Dimension  .../FANTASIAN Neo Dimension/Steam/<steamid>/_data/root.json
                   -> {"dataString": "{\"records\": [{\"path\": ...,
                                       \"encryptedString\": \"<aes-cbc + base64>\"}]}"}

Needs nothing but Python 3.8 or newer. No pip install, no compiler. The AES and
the item list live in this file.

The save format, the encryption key and the item list were worked out by
mathcodergamer in FantasianND-Save-Editor (GPL-3.0), and the editing commands
here descend from that work.
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

__version__ = "2.0.0"

# Baked into the game, the same on every platform and every copy.
AES_IV = b"Nq4G3pTQFLTCeiB7"
AES_KEY = b"yrhWj8EiU83kXupm"

ARCADE_SAVE_DIR = os.path.join(
    os.path.expanduser("~"), "Library", "Containers",
    "com.mistwalkercorp.fantasian", "Data", "Library",
    "Application Support", "FANTASIAN")

STEAM_APP_ID = "2844850"
GAME_DIR_NAME = "FANTASIAN Neo Dimension"

# CoreData counts seconds from 2001-01-01 UTC, not from the unix epoch.
COREDATA_EPOCH_OFFSET = 978307200


class SaveError(Exception):
    """Something the player can act on. Printed without a traceback."""


# ---------------------------------------------------------------------------
# AES-128-CBC with PKCS#7, standard library only.
#
# Checked against pycryptodome on the FIPS-197 C.1 known-answer vector, on 500
# random key and length combinations, and on real save records. `self-test`
# runs the published vectors on demand.
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
# A loaded save, whatever it came from
# ---------------------------------------------------------------------------

# Saves are named GameData0, GameData1, GameData2, GameData10 on disk, and that
# name is the only slot identity there is: nothing inside a save record says
# which slot it belongs to, so there is no way to derive what the game's own
# load screen calls it. Earlier versions of this tool printed "slot 1", "slot 2"
# and "autosave" against those numbers. That mapping was inherited, never
# checked, and a player reported it disagreeing with the Mac game's menu, so the
# file name is what gets shown now. It is also what --slot takes, which keeps
# the thing you read and the thing you type the same.


class Save:
    """Records held as plaintext, however the file on disk stored them.

    `origin` is "arcade" or "steam". `raw_blob` is the untouched Apple Arcade
    payload when there was one, so a transfer between accounts can carry the
    exact bytes the game wrote rather than a re-serialisation of them.
    """

    def __init__(self, records, origin, source, raw_blob=None):
        self.records = records
        self.origin = origin
        self.source = source
        self.raw_blob = raw_blob

    def __len__(self):
        return len(self.records)

    def ordered(self):
        return canonical_order(self.records)

    def slot(self, number):
        for r in self.records:
            if slot_number(r["path"]) == str(number):
                return r
        return None


def slot_number(path):
    base = os.path.basename(path)
    if base.startswith("GameData") and base.endswith(".json"):
        return base[len("GameData"):-len(".json")]
    return base


def slot_label(path):
    """What to call a save on screen: its file name, which is all the save
    itself knows about which slot it is."""
    n = slot_number(path)
    return "GameData" + n if n.isdigit() else n


def canonical_order(records):
    """Put records in GameData0, GameData10, GameData1, GameData2 order.

    Only the Neo Dimension file needs this, and only because
    FantasianND-Save-Editor addresses slots there by position rather than by
    name: it takes record 1 as the quicksave and the rest as ordinary saves. So
    a file handed to that editor in another order gets the wrong save edited.
    Whether record 1 really is the quicksave is that editor's claim, inherited
    here to stay compatible with it, and it is not something the save files
    themselves say. Apple Arcade payloads keep the game's own order instead.
    """
    def key(record):
        n = slot_number(record["path"])
        if not n.isdigit():
            return (3, 0, n)
        n = int(n)
        return (0, 0, "") if n == 0 else (1, 0, "") if n == 10 else (2, n, "")
    return sorted(records, key=key)


def record_plaintext(record):
    if "dataString" in record:
        return record["dataString"]
    return decrypt(base64.b64decode(record["encryptedString"])).decode("utf-8")


def describe(record):
    label = slot_label(record.get("path", "?"))
    try:
        data = json.loads(record_plaintext(record))
        d = dict(zip(data["keys"], data["values"]))
        info = json.loads(d.get("GameSystemInfo", "{}"))
        hours = info.get("_playTimeSec", 0) / 3600.0
        return (f"{label:<10} {d.get('Date', '?'):<20} {hours:5.1f}h "
                f"{info.get('_money', 0):>9,} G   {info.get('_mapId', '?')}")
    except Exception as exc:
        return f"{label:<10} <could not read this slot: {exc}>"


def show(records, indent="  "):
    for r in canonical_order(records):
        print(indent + describe(r))


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def _records_from_blob(raw):
    if raw[:1] == b"\x78":
        raw = zlib.decompress(raw)
    obj = json.loads(raw.decode("utf-8"))
    if "records" not in obj and "dataString" in obj:
        obj = json.loads(obj["dataString"])
    if "records" not in obj:
        raise SaveError("that file has no save records in it")
    return obj["records"]


def _copy_db_aside(db_path):
    """Copy a database and its sidecars somewhere disposable.

    Opening a SQLite database that has a write-ahead log checkpoints it, folding
    the -wal back into the .sqlite and rewriting both. On a live game save that
    is a real edit to the player's files, so reads happen against a copy.
    """
    tmp = tempfile.mkdtemp(prefix="fantasian_")
    target = os.path.join(tmp, "SaveDataEntity.sqlite")
    shutil.copyfile(db_path, target)
    sidecars = 0
    for suffix in ("-wal", "-shm"):
        if os.path.exists(db_path + suffix):
            shutil.copyfile(db_path + suffix, target + suffix)
            sidecars += 1
    return tmp, target, sidecars


def _arcade_rows(db_path):
    """Every root.json row in the database, newest first, read off a copy."""
    tmp, target, sidecars = _copy_db_aside(db_path)
    try:
        con = sqlite3.connect(target)
        try:
            rows = list(con.execute(
                "SELECT Z_PK, ZID, ZDATA FROM ZGAMEDATAENTITY ORDER BY ZTIME DESC"))
        except sqlite3.DatabaseError as exc:
            raise SaveError(_no_wal_advice(db_path, sidecars, str(exc)))
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    keep = [(pk, blob) for pk, zid, blob in rows
            if zid and "root" in zid.lower() and blob]
    if not keep:
        raise SaveError(_no_wal_advice(db_path, sidecars, "it holds no save rows"))
    return keep


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


def _load_zip(zip_path):
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
            db = os.path.join(tmp, "SaveDataEntity.sqlite")
            blob = _arcade_rows(db)[0][1]
            return Save(_records_from_blob(blob), "arcade", zip_path, raw_blob=blob)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


def arcade_db_in(folder):
    db = os.path.join(folder, "SaveDataEntity.sqlite")
    return db if os.path.isfile(db) else None


def load_save(path):
    """Accepts a zip of the FANTASIAN folder, the folder itself, a bare .sqlite,
    a ZGAMEDATAENTITY table dump, or a Neo Dimension root.json."""
    if not os.path.exists(path):
        raise SaveError(f"{path} does not exist")

    if os.path.isdir(path):
        db = arcade_db_in(path)
        if not db:
            raise SaveError(f"no SaveDataEntity.sqlite inside the folder {path}")
        blob = _arcade_rows(db)[0][1]
        return Save(_records_from_blob(blob), "arcade", path, raw_blob=blob)

    ext = os.path.splitext(path)[1].lower()
    if ext == ".zip":
        return _load_zip(path)
    if ext in (".sqlite", ".db", ".sqlite3"):
        blob = _arcade_rows(path)[0][1]
        return Save(_records_from_blob(blob), "arcade", path, raw_blob=blob)

    with open(path, "rb") as f:
        blob = f.read()

    if blob.lstrip()[:1] == b"[":
        for row in json.loads(blob.decode("utf-8")):
            if "ZDATA" in row and "root" in str(row.get("ZID", "root")).lower():
                inner = base64.b64decode(row["ZDATA"])
                return Save(_records_from_blob(inner), "arcade", path, raw_blob=inner)
        raise SaveError(f"no row with a ZDATA blob in {path}")

    obj = json.loads(blob.decode("utf-8"))
    if "dataString" in obj and "records" not in obj:
        records = json.loads(obj["dataString"])["records"]
        if records and "encryptedString" in records[0]:
            return Save(records, "steam", path)
    return Save(_records_from_blob(blob), "arcade", path, raw_blob=blob)


def find_arcade_save():
    return arcade_db_in(ARCADE_SAVE_DIR)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

def to_steam_record(record):
    return {
        "path": record["path"],
        "encryptedString": base64.b64encode(
            encrypt(record_plaintext(record).encode("utf-8"))).decode("utf-8"),
    }


def build_steam_root(records, template_records=None, base=None):
    """Merge records into a Neo Dimension root.json body and verify every one."""
    existing = list(template_records or [])
    by_path = {r["path"]: i for i, r in enumerate(existing)}
    added, replaced = [], []
    for src in canonical_order(records):
        converted = to_steam_record(src)
        if converted["path"] in by_path:
            existing[by_path[converted["path"]]] = converted
            replaced.append(converted["path"])
        else:
            existing.append(converted)
            added.append(converted["path"])

    existing = canonical_order(existing)
    root = dict(base or {})
    root["dataString"] = json.dumps({"records": existing}, separators=(",", ":"))

    for r in json.loads(root["dataString"])["records"]:
        data = json.loads(record_plaintext(r))
        if "keys" not in data or "values" not in data:
            raise SaveError(f"{r['path']} did not survive the round trip")

    return root, existing, added, replaced


def arcade_blob(records):
    """Rebuild the Apple Arcade payload byte for byte the way the game writes it:
    the outer object compact, each record's own JSON indented by four, records
    left in the order they were already in.

    Deliberately not canonical order. The game addresses slots by path and
    writes them in its own order (a real save had slot 2 before slot 1), so
    reordering here would rewrite a file that did not need rewriting. Canonical
    order matters only in the Neo Dimension file, where slots are addressed by
    position.
    """
    out = [{"path": r["path"], "dataString": record_plaintext(r)} for r in records]
    raw = json.dumps({"records": out}, separators=(",", ":")).encode("utf-8")
    return zlib.compress(raw, 9)


def backup_file(path):
    dest = "{}.backup_{}".format(path, time.strftime("%Y%m%d_%H%M%S"))
    shutil.copy2(path, dest)
    return dest


def backup_folder(folder):
    dest = "{}.backup_{}".format(folder.rstrip(os.sep),
                                time.strftime("%Y%m%d_%H%M%S"))
    shutil.copytree(folder, dest)
    return dest


def write_into_arcade_db(db_path, blob, dry_run=False):
    """Put a save payload into an existing Apple Arcade database.

    Only ZGAMEDATAENTITY.ZDATA and its timestamp change. Every CloudKit table
    is left exactly as it was, so the database keeps the identity the account
    that owns it already established. That is the whole point: a database
    carried wholesale from another account is rejected and resynced away,
    because its sync metadata belongs to somebody else.
    """
    rows = _arcade_rows(db_path)
    if dry_run:
        return [pk for pk, _ in rows]
    now = time.time() - COREDATA_EPOCH_OFFSET
    con = sqlite3.connect(db_path)
    try:
        with con:
            for pk, _ in rows:
                con.execute(
                    "UPDATE ZGAMEDATAENTITY SET ZDATA = ?, ZTIME = ? WHERE Z_PK = ?",
                    (sqlite3.Binary(blob), now, pk))
    finally:
        con.close()
    return [pk for pk, _ in rows]


# ---------------------------------------------------------------------------
# Finding the Neo Dimension save
# ---------------------------------------------------------------------------

def _windows_documents():
    """Where Windows actually keeps Documents for this user.

    Guessing ~/Documents is wrong wherever the folder has been redirected, which
    OneDrive does by default on plenty of installs. Windows records the real
    location, so ask it rather than guess.
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
    for root in (os.path.join(home, ".local", "share", "Steam"),
                 os.path.join(home, ".steam", "steam"),
                 os.path.join(home, ".var", "app", "com.valvesoftware.Steam",
                              ".local", "share", "Steam")):
        out.append(os.path.join(root, "steamapps", "compatdata", STEAM_APP_ID,
                                "pfx", "drive_c", "users", "steamuser", "Documents"))
    seen, uniq = set(), []
    for d in out:
        if d not in seen:
            seen.add(d)
            uniq.append(d)
    return uniq


def find_steam_roots():
    found = []
    for documents in _documents_dirs():
        steam_dir = os.path.join(documents, "My Games", GAME_DIR_NAME, "Steam")
        if not os.path.isdir(steam_dir):
            continue
        for entry in sorted(os.listdir(steam_dir)):
            candidate = os.path.join(steam_dir, entry, "_data", "root.json")
            if os.path.isfile(candidate):
                found.append(candidate)
    found.sort(key=os.path.getmtime, reverse=True)
    return found


# ---------------------------------------------------------------------------
# Editing
#
# The save is a dict of twelve keys, each holding its own JSON as a string.
# Every edit below takes that dict, changes one area, and says whether it
# changed anything, so a command that matches nothing reports honestly instead
# of writing a file that is identical to the one it read.
# ---------------------------------------------------------------------------

ITEM_IDS = None          # filled in below


def known_items():
    """The item list, from a known_item_ids.json beside this script if there is
    one, otherwise the copy carried inside it."""
    beside = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "known_item_ids.json")
    if os.path.isfile(beside):
        try:
            with open(beside, encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list) and loaded:
                return loaded
        except (ValueError, OSError):
            pass
    return ITEM_IDS


def unpack(record):
    data = json.loads(record_plaintext(record))
    return dict(zip(data["keys"], data["values"]))


def repack(save_dict):
    """Re-serialise a save exactly the way the game does, four-space indented,
    so an untouched save re-encrypts to the bytes it came from."""
    return json.dumps({"keys": list(save_dict.keys()),
                       "values": list(save_dict.values())}, indent=4)


def _area(save_dict, key):
    return json.loads(save_dict[key])


def _store(save_dict, key, value):
    save_dict[key] = json.dumps(value, separators=(",", ":"))


def add_money(save_dict, amount):
    info = _area(save_dict, "GameSystemInfo")
    info["_money"] += amount
    _store(save_dict, "GameSystemInfo", info)
    return f"money +{amount:,} (now {info['_money']:,})"


def analyze_all(save_dict):
    battle = _area(save_dict, "BattleData")
    changed = 0
    for value in battle["libraryInfoTable"]["valueList"]:
        if not value.get("analyzed"):
            value["analyzed"] = True
            changed += 1
    if not changed:
        return None
    _store(save_dict, "BattleData", battle)
    return f"analyzed {changed} enemy entries"


def _insert_item(inventory, item_id, count, mode, allowed):
    """mode: "skip" leaves an owned item alone, "add" tops it up."""
    if item_id not in allowed:
        raise SaveError(f"unknown item id: {item_id}")
    if item_id.startswith("Item_Key_") or item_id.startswith("Item_Quest_"):
        raise SaveError(f"{item_id} is a key or quest item, inserting it can break "
                        "the story")
    table = inventory["itemTable"]
    if item_id in table["keyList"]:
        index = table["keyList"].index(item_id)
        entry = table["valueList"][index]
        if mode == "add" or entry["count"] == 0:
            entry["count"] += count
            return True
        if mode == "skip":
            return False
        raise SaveError(f"{item_id} is already in your inventory")
    table["keyList"].append(item_id)
    # newType: 0 unknown, 1 discovery, 2 get, 3 confirm
    table["valueList"].append({"count": count, "itemId": item_id, "newType": 2})
    return True


def edit_inventory(save_dict, args):
    """Every inventory command in one pass. Returns a list of what it did.

    All of them are gathered here on purpose: in the original editor the list of
    commands that opened the inventory left four of them out, so asking for only
    one of those four silently did nothing and wrote no file.
    """
    allowed = known_items()
    inventory = _area(save_dict, "Inventory")
    table = inventory["itemTable"]
    notes = []

    if args.remove_extra_unsellable_weapons:
        equipped = [p["_weapon"] for p in _area(save_dict, "PlayerStatus")["_items"]]
        removed = 0
        for value in table["valueList"]:
            if value["itemId"].startswith("Wp") and "_OW_EhUlt_" in value["itemId"]:
                wanted = 1 if value["itemId"] in equipped else 0
                if value["count"] != wanted:
                    removed += value["count"] - wanted
                    value["count"] = wanted
        if removed:
            notes.append(f"removed {removed} spare ultimate weapons")

    bumps = [
        (args.add_box_keys, "Item_BoxKey_", 25, 25, "box keys"),
        (args.add_recovery_items, "Item_Recover_", 100, 100, "recovery items"),
        (args.add_battle_items, "Item_Battle_", 100, None, "battle items"),
        (args.add_accessories, "Acce_", 8, 8, "accessories"),
    ]
    for wanted, prefix, amount, ceiling, label in bumps:
        if not wanted:
            continue
        touched = 0
        for value in table["valueList"]:
            if value["itemId"].startswith(prefix) and (
                    ceiling is None or value["count"] < ceiling):
                value["count"] += amount
                touched += 1
        if touched:
            notes.append(f"topped up {touched} {label}")

    families = [
        (args.insert_all_weapons, lambda i: i.startswith("Wp"), 8, "skip", "weapons"),
        (args.insert_all_armors,
         lambda i: i.startswith("ArmorM_") or i.startswith("ArmorS_"), 8, "skip",
         "armour"),
        (args.insert_all_accessories,
         lambda i: i.startswith("Acce_") and not i.startswith("Acce_God"), 8, "skip",
         "accessories"),
        (args.insert_all_gate_items, lambda i: i.startswith("Item_Gate_"), 1, "skip",
         "growth map gate items"),
        (args.insert_all_upgrade_materials,
         lambda i: i.startswith("Item_Material_"), 24, "add", "upgrade materials"),
    ]
    for wanted, matches, count, mode, label in families:
        if not wanted:
            continue
        added = sum(1 for item_id in allowed
                    if matches(item_id)
                    and _insert_item(inventory, item_id, count, mode, allowed))
        if added:
            notes.append(f"added {added} {label}")

    if args.insert_or_add_sp_capsules:
        _insert_item(inventory, "Item_SpAdd_Capsule", 9999, "add", allowed)
        notes.append("9999 SP capsules")

    for item_id in args.insert_items:
        _insert_item(inventory, item_id, 8, "error", allowed)
    if args.insert_items:
        notes.append(f"inserted {len(args.insert_items)} named items")

    if notes:
        _store(save_dict, "Inventory", inventory)
    return notes


def edit_party(save_dict, args):
    status = _area(save_dict, "PlayerStatus")
    notes = []

    if args.add_exp_mult:
        party = _area(save_dict, "PlayerParty")
        recruited = {u["characterId"] for u in party["_units"]}
        if not status["_items"]:
            raise SaveError("this save has no party members in it yet")
        # The first entry is Leo, whose curve the others are scaled against.
        lead_exp = status["_items"][0]["_exp"]
        touched = 0
        for player in status["_items"]:
            if player["_characterId"] in recruited:
                player["_exp"] += int(lead_exp * args.add_exp_mult)
                touched += 1
        notes.append(f"gave {touched} recruited characters "
                     f"{args.add_exp_mult:g}x the lead's experience")

    if args.add_sp_points:
        for player in status["_items"]:
            player["_growthPoint"] += args.add_sp_points
        notes.append(f"+{args.add_sp_points} SP to {len(status['_items'])} characters")

    if notes:
        _store(save_dict, "PlayerStatus", status)
    return notes


EDIT_FLAGS = (
    "add_money", "analyze_all", "add_box_keys", "add_recovery_items",
    "add_battle_items", "add_accessories", "insert_all_weapons",
    "insert_all_armors", "insert_all_accessories", "insert_or_add_sp_capsules",
    "insert_all_gate_items", "insert_all_upgrade_materials",
    "remove_extra_unsellable_weapons", "add_exp_mult", "add_sp_points",
    "insert_items",
)


def apply_edits(save_dict, args):
    """Returns what changed and what was already the way it was asked to be.

    Only the first list decides whether a file gets written. A command that
    found nothing to do must not produce a rewritten save.
    """
    changes, remarks = [], []
    if args.add_money:
        changes.append(add_money(save_dict, args.add_money))
    if args.analyze_all:
        note = analyze_all(save_dict)
        (changes if note else remarks).append(
            note or "every enemy was already analyzed")
    changes += edit_inventory(save_dict, args)
    changes += edit_party(save_dict, args)
    return [c for c in changes if c], remarks


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def resolve_source(path, what="save"):
    if path:
        return path
    found = find_arcade_save()
    if not found:
        raise SaveError(
            f"no Apple Arcade FANTASIAN {what} on this machine, and none was given.\n"
            f"Looked in {ARCADE_SAVE_DIR}\n"
            "If you played on another Mac, copy that FANTASIAN folder over and pass it "
            "as an argument.")
    print("Reading the Apple Arcade save installed on this Mac.")
    return found


def cmd_slots(args):
    save = load_save(resolve_source(args.source))
    print(f"\n{len(save)} save slot(s) in {save.source}:\n")
    show(save.records)
    print()
    return 0


def cmd_to_steam(args):
    save = load_save(resolve_source(args.source))
    print(f"\nFound {len(save)} save slot(s):\n")
    show(save.records)
    print()

    records = save.records
    if args.slots is not None:
        wanted = set(args.slots)
        records = [r for r in records
                   if r["path"] in wanted or slot_number(r["path"]) in wanted]
        if not records:
            raise SaveError(
                "--slots {} matched none of these slots. Available: {}".format(
                    " ".join(args.slots),
                    " ".join(sorted(slot_number(r["path"]) for r in save.records))))

    install_target = None
    if args.install:
        roots = find_steam_roots()
        if not roots:
            raise SaveError(
                "--install could not find a Neo Dimension save on this machine.\n"
                "Start the game, make one save, and try again. Or drop --install and "
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

    base, template_records = None, None
    if template_path:
        with open(template_path, encoding="utf-8") as f:
            base = json.load(f)
        if "dataString" not in base:
            raise SaveError(f"{template_path} is not a Neo Dimension root.json")
        template_records = json.loads(base["dataString"])["records"]
        print(f"\nIt already holds {len(template_records)} slot(s):\n")
        show(template_records)
        print()
    else:
        print("Writing a fresh save file. If the game will not load it, play far "
              "enough to save once, then run this again with --auto-template.\n")

    root, final, added, replaced = build_steam_root(records, template_records, base)

    print("Bringing across:")
    for path in canonical_order([{"path": p} for p in added + replaced]):
        p = path["path"]
        print(f"  {slot_label(p):<10} "
              f"{'overwrites the Neo Dimension one' if p in replaced else 'added'}")

    out_path = install_target or args.output
    if install_target:
        print(f"\nBacked up your Neo Dimension save to\n  {backup_file(install_target)}")

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(root, f, indent=4)

    print(f"\nWrote {out_path} with {len(final)} slot(s).")
    warn_missing_autosave(final)
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


def warn_missing_autosave(records):
    if not any(slot_number(r["path"]) == "10" for r in records):
        print("\nHeads up: there is no GameData10 in this save. The game is fine with "
              "that, but FantasianND-Save-Editor addresses slots by position and expects "
              "one, so it will read the second save here as the quicksave. Pick saves by "
              "name with --slot rather than trusting its numbering.")


def cmd_to_account(args):
    """Carry a save into the Apple Arcade account signed in on this Mac."""
    if not args.source:
        raise SaveError(
            "give me the FANTASIAN folder or zip from the OLD account.\n"
            "It is the save you want to keep, copied off the Mac or the account that "
            "has it.")
    source = load_save(args.source)
    if source.origin != "arcade" or source.raw_blob is None:
        raise SaveError(
            f"{args.source} is not an Apple Arcade save. This command moves a save "
            "between Apple Arcade accounts. To go to Steam, use the to-steam command.")

    target_folder = args.into or ARCADE_SAVE_DIR
    target_db = arcade_db_in(target_folder)
    if not target_db:
        raise SaveError(
            f"no FANTASIAN save in {target_folder}\n\n"
            "This writes into the save the NEW account already has, so that account "
            "needs one first. Sign in as the new account, start FANTASIAN, play until "
            "it saves once, quit, and run this again.")

    print(f"\nCarrying over from {args.source}:\n")
    show(source.records)

    target = load_save(target_folder)
    print(f"\nThe account on this Mac currently has ({target_folder}):\n")
    show(target.records)

    rows = write_into_arcade_db(target_db, source.raw_blob, dry_run=True)
    print(f"\n{len(rows)} save row(s) in the target database would be replaced.")

    if args.dry_run:
        print("\nDry run, nothing was written.")
        return 0

    backup = backup_folder(target_folder)
    print(f"\nBacked up the whole folder to\n  {backup}")

    write_into_arcade_db(target_db, source.raw_blob)

    check = load_save(target_folder)
    written = {r["path"]: record_plaintext(r) for r in check.records}
    expected = {r["path"]: record_plaintext(r) for r in source.records}
    if written != expected:
        raise SaveError(
            "the save did not read back as expected. Nothing is lost: copy\n"
            f"  {backup}\nback over\n  {target_folder}\nto put it as it was.")

    print(f"\nDone. {len(check)} slot(s) now in this account's save:\n")
    show(check.records)
    print("""
Only the save itself was replaced. The sync metadata that ties this database to
the account signed in on this Mac was left alone, which is the part a wholesale
folder copy gets wrong: iCloud sees a database belonging to somebody else and
replaces it with its own copy.

Now bind it to the account:

  1. Start FANTASIAN and load one of the slots above.
  2. Save through the game's own menu.

That save is what pushes your progress up under the new account. Until you do
it, the file is only local.""")
    print(f"\nIf anything looks wrong, copy\n  {backup}\nback over\n  {target_folder}")
    return 0


def cmd_edit(args):
    if not any(getattr(args, flag) for flag in EDIT_FLAGS) and not args.print_save:
        raise SaveError("give me something to change. Run `edit --help` to see the list.")

    save = load_save(resolve_source(args.save))
    print(f"\n{len(save)} save slot(s) in {save.source}:\n")
    show(save.records)

    if args.slot is None:
        # Whichever save has the most time on it, autosaves included. Skipping
        # what looks like an autosave would mean guessing which file that is,
        # and nothing in a save says so.
        record = max(save.records,
                     key=lambda r: json.loads(unpack(r).get("GameSystemInfo", "{}"))
                                       .get("_playTimeSec", 0))
        print(f"\nNo --slot given, so taking the one with the most time on it. "
              f"Pick another by name, for example --slot "
              f"{slot_number(save.records[0]['path'])}.")
    else:
        record = save.slot(args.slot)
        if record is None:
            raise SaveError(
                "no slot {} in this save. Available: {}".format(
                    args.slot,
                    " ".join(sorted(slot_number(r["path"]) for r in save.records))))

    print(f"\nEditing {slot_label(record['path'])}:\n  {describe(record)}\n")
    save_dict = unpack(record)

    # An untouched save must re-serialise to the bytes it came from, or an edit
    # would be riding on a reconstruction rather than on the real file.
    if repack(save_dict) != record_plaintext(record):
        raise SaveError("this save does not round-trip cleanly, refusing to edit it")

    if args.print_save:
        import pprint
        for key, value in save_dict.items():
            print("-" * 78)
            print(key)
            print("-" * 78)
            try:
                pprint.pprint(json.loads(value))
            except ValueError:
                print(value)
            print()

    changes, remarks = apply_edits(save_dict, args)
    for remark in remarks:
        print("  " + remark)
    if not changes:
        print("\nNothing changed, so nothing was written. Every command you gave was "
              "already satisfied.")
        return 0

    for change in changes:
        print("  " + change)

    record["dataString"] = repack(save_dict)
    record.pop("encryptedString", None)

    if save.origin == "steam":
        out = args.output or save.source
        if os.path.exists(out):
            print(f"\nBacked up to\n  {backup_file(out)}")
        root, final, _, _ = build_steam_root(save.records)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(root, f, indent=4)
        print(f"\nWrote {out}.")
    else:
        folder = save.source if os.path.isdir(save.source) else \
            os.path.dirname(os.path.abspath(save.source))
        db = arcade_db_in(folder)
        if not db or args.output:
            out = args.output or "root.json"
            root, final, _, _ = build_steam_root(save.records)
            with open(out, "w", encoding="utf-8") as f:
                json.dump(root, f, indent=4)
            print(f"\nWrote {out} in Neo Dimension format.")
        else:
            print(f"\nBacked up the whole folder to\n  {backup_folder(folder)}")
            write_into_arcade_db(db, arcade_blob(save.records))
            print(f"\nWrote the edit into {db}. Close the game first if it is open, "
                  "then start it and check the slot.")
    return 0


def cmd_self_test(args=None):
    """The encryption here is hand-written, so it gets to prove itself.

    The vectors are FIPS-197 appendix C.1 for the AES-128 block cipher and NIST
    SP 800-38A appendix F.2.1 for CBC chaining.
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

    sample = json.dumps({"keys": ["Version"], "values": ["1"]}).encode("utf-8")
    if decrypt(encrypt(sample)) != sample:
        raise SaveError("round-trip self-test failed")
    print("Save round trip        encrypt then decrypt returns the original")

    items = known_items()
    if len(items) < 300:
        raise SaveError(f"the item list looks wrong ({len(items)} entries)")
    print(f"Item list              {len(items)} ids")
    print("\nGood. This build can read, write and edit FANTASIAN saves.")
    return 0


# ---------------------------------------------------------------------------

def build_parser():
    p = argparse.ArgumentParser(
        prog="fantasian.py",
        description="Move and edit FANTASIAN saves.",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, width=96))
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command")

    src = ("the FANTASIAN folder off your Apple device, a zip of it, "
           "SaveDataEntity.sqlite, or a Neo Dimension root.json. Left out, the Apple "
           "Arcade save on this Mac is used.")

    s = sub.add_parser("slots", help="show what is in a save")
    s.add_argument("source", nargs="?", help=src)
    s.set_defaults(func=cmd_slots)

    t = sub.add_parser("to-steam",
                       help="carry an Apple Arcade save into Neo Dimension on Steam")
    t.add_argument("source", nargs="?", help=src)
    t.add_argument("-o", "--output", default="root.json",
                   help="where to write it (default: root.json)")
    t.add_argument("-t", "--template", metavar="ROOT_JSON",
                   help="an existing Neo Dimension root.json to merge into, so slots "
                        "the Apple Arcade save does not cover survive")
    t.add_argument("--auto-template", action="store_true",
                   help="find the Neo Dimension save on this machine and merge into it")
    t.add_argument("--slots", metavar="N", nargs="+", default=None,
                   help="only these saves, by GameData number, as shown by `slots`")
    t.add_argument("--install", action="store_true",
                   help="write straight into the Neo Dimension save folder, backing up "
                        "what is there first. Close the game first.")
    t.set_defaults(func=cmd_to_steam)

    a = sub.add_parser(
        "to-account",
        help="carry a save into the Apple Arcade account signed in on this Mac",
        description="Put a save from one Apple Arcade account into another.\n\n"
                    "Only the save itself is replaced. The sync metadata tying the "
                    "database to the account on this Mac is left alone, which is the "
                    "part a wholesale folder copy gets wrong: iCloud sees a database "
                    "belonging to somebody else and replaces it with its own copy.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument("source", nargs="?",
                   help="the FANTASIAN folder or zip from the OLD account")
    a.add_argument("--into", metavar="FOLDER",
                   help=f"the save folder to write into (default: {ARCADE_SAVE_DIR})")
    a.add_argument("--dry-run", action="store_true",
                   help="say what would happen and write nothing")
    a.set_defaults(func=cmd_to_account)

    e = sub.add_parser("edit", help="change a save: money, items, experience")
    e.add_argument("save", nargs="?", help=src)
    e.add_argument("--slot", metavar="N",
                   help="which save, by GameData number: 0 for GameData0, 10 for "
                        "GameData10. Default: whichever has the most time on it.")
    e.add_argument("-o", "--output",
                   help="write here instead of back into the save it read")
    e.add_argument("--print-save", action="store_true",
                   help="print everything in the slot")
    e.add_argument("--add-money", nargs="?", type=int, const=1_000_000, metavar="AMOUNT",
                   help="add money (default 1,000,000)")
    e.add_argument("--analyze-all", action="store_true",
                   help="mark every enemy you have met as analyzed")
    e.add_argument("--add-box-keys", action="store_true",
                   help="add 25 of each box key you own (24 is the most any key opens)")
    e.add_argument("--add-recovery-items", action="store_true",
                   help="add 100 of each recovery item you own")
    e.add_argument("--add-battle-items", action="store_true",
                   help="add 100 of each battle item you own")
    e.add_argument("--add-accessories", action="store_true",
                   help="add 8 of each accessory you own")
    e.add_argument("--insert-all-weapons", action="store_true",
                   help="8 of every known weapon")
    e.add_argument("--insert-all-armors", action="store_true",
                   help="8 of every known armour")
    e.add_argument("--insert-all-accessories", action="store_true",
                   help="8 of every known accessory, skipping Divine Artifacts so the "
                        "story is left alone")
    e.add_argument("--insert-or-add-sp-capsules", action="store_true",
                   help="9999 SP capsules. Part 2 content.")
    e.add_argument("--insert-all-gate-items", action="store_true",
                   help="every growth map gate item. Part 2 content.")
    e.add_argument("--insert-all-upgrade-materials", action="store_true",
                   help="24 of each upgrade material. Part 2 content.")
    e.add_argument("--remove-extra-unsellable-weapons", action="store_true",
                   help="clear out spare ultimate weapons, keeping equipped ones")
    e.add_argument("--add-exp-mult", nargs="?", type=float, const=4, metavar="MULT",
                   help="give every recruited character MULT times the lead's "
                        "experience (default 4)")
    e.add_argument("--add-sp-points", nargs="?", type=int, const=400, metavar="POINTS",
                   help="add SP to every character (default 400)")
    e.add_argument("--insert-items", metavar="ITEM_ID", nargs="*", default=[],
                   help="insert 8 each of these item ids")
    e.set_defaults(func=cmd_edit)

    st = sub.add_parser("self-test", help="check the encryption on this machine")
    st.set_defaults(func=lambda args: cmd_self_test())

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args)


# The 375 item ids mathcodergamer compiled, carried here so the tool is one
# file. A known_item_ids.json placed beside this script overrides it.
ITEM_IDS = json.loads("""[
"Acce_AutoQuick","Acce_AutoRegene","Acce_ConvertHpToMp_L","Acce_ConvertHpToMp_S",
"Acce_ConvertMpToHp_L","Acce_ConvertMpToHp_S","Acce_DeathRaise",
"Acce_DyingCureAndResit","Acce_ExpUp1","Acce_ExpUp2","Acce_GodChoker02",
"Acce_GodGauntlet02","Acce_GodGoggles02","Acce_GodMask02","Acce_GodNecklace02",
"Acce_GodNimbus02","Acce_GodPierce02","Acce_GodTiara02","Acce_Guts",
"Acce_HealthBody","Acce_KillHealHp_L","Acce_KillHealHp_S","Acce_KillHealMp_L",
"Acce_KillHealMp_S","Acce_ParaAgi_L","Acce_ParaAgi_S","Acce_ParaAgi_XL",
"Acce_ParaAtk_L","Acce_ParaAtk_M","Acce_ParaAtk_S","Acce_ParaAtk_XL",
"Acce_ParaAvo_L","Acce_ParaAvo_S","Acce_ParaDef_L","Acce_ParaDef_M","Acce_ParaDef_S",
"Acce_ParaDef_XL","Acce_ParaHit_S","Acce_ParaHp_L","Acce_ParaHp_M","Acce_ParaHp_S",
"Acce_ParaHp_XL","Acce_ParaHp_XXL","Acce_ParaMp_L","Acce_ParaMp_M","Acce_ParaMp_S",
"Acce_ParaMp_XL","Acce_RemoveBombComplete","Acce_ResistBadStates",
"Acce_ResistBerserk","Acce_ResistCurse","Acce_ResistDark_L","Acce_ResistDark_M",
"Acce_ResistDark_S","Acce_ResistDark_XL","Acce_ResistDeath","Acce_ResistEarth_L",
"Acce_ResistEarth_M","Acce_ResistEarth_S","Acce_ResistEarth_XL","Acce_ResistFire_L",
"Acce_ResistFire_M","Acce_ResistFire_S","Acce_ResistFire_XL","Acce_ResistIce_L",
"Acce_ResistIce_M","Acce_ResistIce_S","Acce_ResistIce_XL","Acce_ResistLight_L",
"Acce_ResistLight_M","Acce_ResistLight_S","Acce_ResistLight_XL","Acce_ResistMist",
"Acce_ResistPoison","Acce_ResistSeal","Acce_ResistSleep","Acce_ResistSlow",
"Acce_ResistStone","Acce_ResistThunder_L","Acce_ResistThunder_M",
"Acce_ResistThunder_S","Acce_ResistThunder_XL","Acce_ResistWeakenElements",
"Acce_SurviveCureAndResit","Acce_ThroughForce","Acce_TurnHealHp_L",
"Acce_TurnHealHp_S","ArmorM_OW_Nm01","ArmorM_OW_Nm02","ArmorM_OW_Nm03",
"ArmorM_OW_Nm03_Eh","ArmorM_OW_Nm04","ArmorM_OW_Nm05","ArmorM_OW_Nm05_Eh",
"ArmorM_Rk01","ArmorM_Rk02","ArmorM_Rk03","ArmorM_Rk04","ArmorM_Rk05","ArmorM_Rk06",
"ArmorS_OW_Eh01_00","ArmorS_OW_Eh02_00","ArmorS_OW_Eh02_01","ArmorS_OW_Eh02_02",
"ArmorS_OW_Eh03_00","ArmorS_OW_Eh03_01","ArmorS_OW_Eh03_02","ArmorS_OW_Eh04_00",
"ArmorS_OW_Eh04_01","ArmorS_OW_Eh04_02","ArmorS_OW_Eh05_00","ArmorS_OW_Eh05_01",
"ArmorS_OW_Eh05_02","ArmorS_Rk01","ArmorS_Rk02","ArmorS_Rk03","ArmorS_Rk03_Barrier",
"ArmorS_Rk04","ArmorS_Rk05","ArmorS_Rk06","Item_Battle_AtkUp","Item_Battle_Baterry",
"Item_Battle_Chip","Item_Battle_Core","Item_Battle_Crystal","Item_Battle_Dark",
"Item_Battle_DefUp","Item_Battle_Earth","Item_Battle_Fire","Item_Battle_Ice",
"Item_Battle_Light","Item_Battle_Poison","Item_Battle_Research","Item_Battle_Shell",
"Item_Battle_Slow","Item_Battle_Stone","Item_Battle_Thunder","Item_BoxKey_Chaos",
"Item_BoxKey_DeathMech","Item_BoxKey_Gods","Item_BoxKey_Iron","Item_BoxKey_Machine",
"Item_Collect_DeathSilver","Item_Gate_Barrier","Item_Gate_Curse",
"Item_Gate_DamageDiffuse","Item_Gate_Dark","Item_Gate_DeliveryLocker",
"Item_Gate_Doping","Item_Gate_Earth","Item_Gate_ExtremeBook","Item_Gate_Fire",
"Item_Gate_HackBible","Item_Gate_Heal","Item_Gate_Ice","Item_Gate_Light",
"Item_Gate_Negotiate","Item_Gate_Quick","Item_Gate_RudyTreasure","Item_Gate_Steal",
"Item_Gate_Thunder","Item_Key_AntiDeathMech","Item_Key_BenceHouseKey",
"Item_Key_BlueJewel","Item_Key_BlueStone","Item_Key_CherylRecorder",
"Item_Key_CinderellaBadge01","Item_Key_CinderellaBadge02",
"Item_Key_CinderellaBadge03","Item_Key_CinderellaBadge04",
"Item_Key_CinderellaBadge05","Item_Key_CinderellaBadge06",
"Item_Key_CinderellaBadge07","Item_Key_CrystalBall","Item_Key_Dimension",
"Item_Key_DimensionAnother","Item_Key_Dimension_Q","Item_Key_EzBlueprint",
"Item_Key_GodMask01","Item_Key_Gondra","Item_Key_GondraTicket","Item_Key_ImmVoice",
"Item_Key_LeoaPendant01","Item_Key_LeoaPendant02","Item_Key_LeoaPendant03",
"Item_Key_MagicCoin","Item_Key_Manipulate_Stone","Item_Key_RopeShangrila",
"Item_Key_STMachine","Item_Key_SoundCrystal","Item_Key_TimeWarp",
"Item_Key_UzuraTicket","Item_Key_VibraFire","Item_Key_WarpMachine01",
"Item_Key_WarpMachine02","Item_Key_WarpMachineHigh","Item_Material_Ex01",
"Item_Material_Ex02","Item_Material_StoneBronze","Item_Material_StoneGold",
"Item_Material_StoneIron","Item_Material_StoneSilver","Item_Quest_AncientRelics",
"Item_Quest_Card","Item_Quest_ChaosMaterial","Item_Quest_DealTicket",
"Item_Quest_DeathBallParts_A","Item_Quest_DeathBallParts_B",
"Item_Quest_DeathBallParts_C","Item_Quest_DeathBallSample",
"Item_Quest_DimensionBoard_A","Item_Quest_DimensionBoard_B",
"Item_Quest_DimensionBoard_C","Item_Quest_EzuMaterial_A","Item_Quest_EzuMaterial_B",
"Item_Quest_EzuMaterial_C","Item_Quest_FishBone","Item_Quest_FishFeed",
"Item_Quest_Gear_A","Item_Quest_Gear_B","Item_Quest_Gear_C","Item_Quest_Handbill",
"Item_Quest_Letter_Friend","Item_Quest_Letter_FriendBack","Item_Quest_Letter_Lover",
"Item_Quest_MagicCoin","Item_Quest_NormalEgg","Item_Quest_Scrap","Item_Quest_Shell",
"Item_Quest_Shell_Blue","Item_Quest_Shell_Rainbow","Item_Quest_Shell_Yellow",
"Item_Quest_SorceryCore","Item_Quest_ThunderBirdEgg","Item_Quest_UltimateEgg",
"Item_Recover_EtherL","Item_Recover_EtherS","Item_Recover_HealEx",
"Item_Recover_HealStone","Item_Recover_PotionL","Item_Recover_PotionS",
"Item_Recover_RemoveBomb","Item_Recover_RemoveCurse","Item_Recover_RemoveMist",
"Item_Recover_RemovePoison","Item_Recover_RemoveSeal","Item_Recover_RemoveSleep",
"Item_Recover_RemoveSlow","Item_Recover_RemoveState","Item_Recover_RemoveStone",
"Item_Recover_Revive","Item_SpAdd_Capsule","WpBoomer_OW_Eh02_00",
"WpBoomer_OW_Eh02_10","WpBoomer_OW_Eh03_00","WpBoomer_OW_Eh03_10",
"WpBoomer_OW_EhUlt_00","WpBoomer_OW_EhUlt_01","WpBoomer_OW_EhUlt_10",
"WpBoomer_OW_EhUlt_11","WpBoomer_OW_EhUlt_12","WpBoomer_OW_EhUlt_13",
"WpBoomer_OW_Nm01","WpBoomer_OW_Nm02","WpBoomer_Rk04","WpBoomer_Rk05",
"WpBoomer_Rk06","WpGadget_OW_Eh01_00","WpGadget_OW_Eh02_00","WpGadget_OW_Eh03_00",
"WpGadget_OW_Eh03_10","WpGadget_OW_EhUlt_00","WpGadget_OW_EhUlt_01",
"WpGadget_OW_EhUlt_10","WpGadget_OW_EhUlt_11","WpGadget_OW_EhUlt_12",
"WpGadget_OW_EhUlt_13","WpGadget_OW_Nm01","WpGadget_OW_Nm02","WpGadget_OW_Nm03",
"WpGun_OW_Eh01_00","WpGun_OW_Eh02_00","WpGun_OW_Eh03_00","WpGun_OW_Eh03_10",
"WpGun_OW_EhUlt_00","WpGun_OW_EhUlt_01","WpGun_OW_EhUlt_10","WpGun_OW_EhUlt_11",
"WpGun_OW_EhUlt_12","WpGun_OW_EhUlt_13","WpGun_OW_Nm01","WpGun_OW_Nm02","WpGun_Rk01",
"WpKnuckle_OW_Eh02_00","WpKnuckle_OW_Eh03_00","WpKnuckle_OW_Eh03_10",
"WpKnuckle_OW_EhUlt_00","WpKnuckle_OW_EhUlt_01","WpKnuckle_OW_EhUlt_10",
"WpKnuckle_OW_EhUlt_11","WpKnuckle_OW_EhUlt_12","WpKnuckle_OW_EhUlt_13",
"WpKnuckle_OW_Nm01","WpKnuckle_OW_Nm02","WpKnuckle_Rk04","WpKnuckle_Rk05",
"WpKnuckle_Rk05_VsMachine","WpKnuckle_Rk06","WpLantern_OW_Eh01_00",
"WpLantern_OW_Eh02_00","WpLantern_OW_Eh02_09","WpLantern_OW_Eh03_00",
"WpLantern_OW_Eh03_10","WpLantern_OW_EhUlt_00","WpLantern_OW_EhUlt_01",
"WpLantern_OW_EhUlt_10","WpLantern_OW_EhUlt_11","WpLantern_OW_EhUlt_12",
"WpLantern_OW_EhUlt_13","WpLantern_OW_Nm01","WpLantern_OW_Nm03","WpRing_OW_Eh02_00",
"WpRing_OW_Eh02_10","WpRing_OW_Eh03_10","WpRing_OW_Eh03_11","WpRing_OW_EhUlt_00",
"WpRing_OW_EhUlt_01","WpRing_OW_EhUlt_10","WpRing_OW_EhUlt_11","WpRing_OW_EhUlt_12",
"WpRing_OW_EhUlt_13","WpRing_OW_Nm01","WpRing_Rk02","WpRing_Rk02_Ice","WpRing_Rk03",
"WpRing_Rk04","WpRing_Rk04_VsMachine","WpSword_OW_Eh01_00","WpSword_OW_Eh02_00",
"WpSword_OW_Eh03_00","WpSword_OW_Eh03_01","WpSword_OW_Eh03_02","WpSword_OW_EhUlt_00",
"WpSword_OW_EhUlt_01","WpSword_OW_EhUlt_10","WpSword_OW_EhUlt_11",
"WpSword_OW_EhUlt_12","WpSword_OW_EhUlt_13","WpSword_OW_Nm01","WpSword_OW_Nm02",
"WpSword_Rk01","WpSword_Rk02","WpSword_Rk03","WpSword_Rk03_TurnAtkUp","WpSword_Rk04",
"WpSword_Rk05","WpSword_Rk06","WpWand_OW_Eh02_00","WpWand_OW_Eh02_10",
"WpWand_OW_Eh03_00","WpWand_OW_Eh03_10","WpWand_OW_EhUlt_00","WpWand_OW_EhUlt_01",
"WpWand_OW_EhUlt_10","WpWand_OW_EhUlt_11","WpWand_OW_EhUlt_12","WpWand_OW_EhUlt_13",
"WpWand_OW_Nm01","WpWand_OW_Nm02","WpWand_Rk01","WpWand_Rk02","WpWand_Rk02_Poison",
"WpWand_Rk03","WpWand_Rk04","WpWand_Rk04_Healing"
]""")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SaveError as exc:
        print(f"\n{exc}\n", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
