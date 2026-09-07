#!/usr/bin/env python3
"""Tests for fantasian.py. Standard library only, no real save data needed.

    python3 test_fantasian.py
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fantasian as ft


def run(argv):
    """Call the tool, keeping its chatter out of the test report."""
    with contextlib.redirect_stdout(io.StringIO()):
        return ft.main(argv)


def out_of(argv):
    """Call the tool and hand back what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ft.main(argv)
    return buf.getvalue()


@contextlib.contextmanager
def fake_home(path):
    """Point the tool at a throwaway home directory.

    HOME is enough on macOS and Linux. Windows resolves ~ from USERPROFILE and
    ignores HOME entirely, so both get set, and OneDrive is cleared so the
    runner's real one does not leak into the search.
    """
    patched = {"HOME": path, "USERPROFILE": path}
    saved = {k: os.environ.get(k)
             for k in list(patched) + ["OneDrive", "OneDriveConsumer"]}
    os.environ.update(patched)
    os.environ.pop("OneDrive", None)
    os.environ.pop("OneDriveConsumer", None)
    try:
        yield
    finally:
        for k, v in saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


# ---------------------------------------------------------------------------
# Fixtures shaped like real saves
# ---------------------------------------------------------------------------

def make_slot(play_seconds, money, map_id, date, party=8, items=None):
    status = {"_items": [{"_characterId": f"PC00{i + 1}", "_exp": 1000 * (i + 1),
                          "_growthPoint": 0, "_weapon": f"WpSword_Rk0{i % 3 + 1}"}
                         for i in range(party)]}
    inventory = {"itemTable": {"keyList": [], "valueList": []}}
    for item_id, count in (items or {}).items():
        inventory["itemTable"]["keyList"].append(item_id)
        inventory["itemTable"]["valueList"].append(
            {"count": count, "itemId": item_id, "newType": 3})
    battle = {"libraryInfoTable": {"valueList": [{"analyzed": False, "defeatCount": 3},
                                                 {"analyzed": True, "defeatCount": 9}]}}
    payload = {
        "keys": ["PlayerParty", "PlayerStatus", "FlagManager", "GameSystemInfo",
                 "Inventory", "BattleData", "Date"],
        "values": [
            json.dumps({"_units": [{"characterId": f"PC00{i + 1}"}
                                   for i in range(party)]}),
            json.dumps(status),
            json.dumps({"_globalFlags": {}}),
            json.dumps({"_playTimeSec": play_seconds, "_money": money,
                        "_mapId": map_id}),
            json.dumps(inventory),
            json.dumps(battle),
            date,
        ],
    }
    # Four-space indent, the way the game serialises each record.
    return json.dumps(payload, indent=4)


DEFAULT_ITEMS = {"Item_BoxKey_Red": 2, "Item_Recover_S": 5, "Item_Battle_Bomb": 1,
                 "Acce_AutoQuick": 1, "WpSword_OW_EhUlt_01": 4}

SLOTS = {
    "Data/GameData1.json": make_slot(59700, 35368, "CityVibra", "2021/08/23 11:42:06",
                                     items=DEFAULT_ITEMS),
    "Data/GameData0.json": make_slot(60000, 35868, "CityVibra", "2026/08/19 09:44:41",
                                     items=DEFAULT_ITEMS),
    "Data/GameData2.json": make_slot(59000, 30000, "CityVibra", "2026/08/18 09:44:41",
                                     items=DEFAULT_ITEMS),
    "Data/GameData10.json": make_slot(3600, 3980, "NewTownEn", "2021/06/11 11:52:45",
                                      items=DEFAULT_ITEMS),
}


def arcade_payload(slots=None):
    """The outer payload, compact, in the order the game happens to use.

    Note the order: a real save had slot 2 before slot 1, so the fixture keeps
    that rather than a tidy one.
    """
    slots = slots or SLOTS
    records = [{"path": p, "dataString": d} for p, d in slots.items()]
    return zlib.compress(
        json.dumps({"records": records}, separators=(",", ":")).encode("utf-8"), 9)


def write_arcade_db(path, keep_rows_in_wal=False, slots=None, metadata=True):
    """Build an Apple Arcade style database.

    With keep_rows_in_wal the rows are copied out while a connection is still
    open, so the .sqlite is the empty checkpoint and the -wal beside it carries
    everything, which is the shape a live FANTASIAN save has on disk.
    """
    work = tempfile.mkdtemp(prefix="mkdb_")
    live = os.path.join(work, "SaveDataEntity.sqlite")
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE ZGAMEDATAENTITY (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER,"
                " Z_OPT INTEGER, ZVERSION INTEGER, ZTIME FLOAT, ZDEVICENAME VARCHAR,"
                " ZID VARCHAR, ZLOCALPLAYERNAME VARCHAR, ZUUID VARCHAR, ZDATA BLOB)")
    if metadata:
        # Stand-ins for the CloudKit mirroring tables, which carry the account
        # identity a transfer must not disturb.
        con.execute("CREATE TABLE ANSCKRECORDMETADATA (Z_PK INTEGER, ZRECORDNAME TEXT)")
        con.execute("INSERT INTO ANSCKRECORDMETADATA VALUES (1, 'owned-by-this-account')")
        con.execute("CREATE TABLE ANSCKDATABASEMETADATA (Z_PK INTEGER, ZTOKEN TEXT)")
        con.execute("INSERT INTO ANSCKDATABASEMETADATA VALUES (1, 'server-change-token')")
    con.commit()
    if keep_rows_in_wal:
        con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute(
        "INSERT INTO ZGAMEDATAENTITY (Z_PK, Z_ENT, Z_OPT, ZVERSION, ZTIME, ZDEVICENAME,"
        " ZID, ZUUID, ZDATA) VALUES (7,1,1,0,?,?,?,?,?)",
        (810404856.1, "Test Mac", "root.json", "UUID-KEEP-ME", arcade_payload(slots)))
    con.commit()

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(live + suffix):
            shutil.copyfile(live + suffix, path + suffix)
    con.close()
    shutil.rmtree(work, ignore_errors=True)


def table_fingerprints(db_path):
    tmp = tempfile.mkdtemp()
    try:
        for s in ("", "-wal", "-shm"):
            if os.path.exists(db_path + s):
                shutil.copyfile(db_path + s, os.path.join(tmp, "d.sqlite" + s))
        con = sqlite3.connect(os.path.join(tmp, "d.sqlite"))
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        out = {}
        for t in names:
            rows = con.execute("SELECT * FROM '%s'" % t).fetchall()
            out[t] = hashlib.sha256(repr(rows).encode()).hexdigest()
        con.close()
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def arcade(self, name="FANTASIAN", **kw):
        folder = os.path.join(self.tmp, name)
        write_arcade_db(os.path.join(folder, "SaveDataEntity.sqlite"), **kw)
        return folder

    def steam_records(self, path):
        with open(path, encoding="utf-8") as f:
            return json.loads(json.load(f)["dataString"])["records"]


# ---------------------------------------------------------------------------

class Encryption(unittest.TestCase):
    def test_published_vectors(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ft.cmd_self_test(), 0)

    def test_round_trip(self):
        for size in (0, 1, 15, 16, 17, 4096):
            blob = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
            self.assertEqual(ft.decrypt(ft.encrypt(blob)), blob, size)

    def test_padding_is_always_added(self):
        self.assertEqual(len(ft.encrypt(b"A" * 16)), 32)

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            ft.decrypt(b"not a whole block")
        with self.assertRaises(ValueError):
            ft.decrypt(b"\x00" * 32)


class ItemList(unittest.TestCase):
    def test_carried_in_the_file(self):
        self.assertGreater(len(ft.ITEM_IDS), 300)
        self.assertIn("Item_SpAdd_Capsule", ft.ITEM_IDS)

    def test_a_file_beside_the_script_wins(self):
        beside = os.path.join(os.path.dirname(os.path.abspath(ft.__file__)),
                              "known_item_ids.json")
        if os.path.exists(beside):
            self.skipTest("a real list is already sitting beside the script")
        with open(beside, "w", encoding="utf-8") as f:
            json.dump(["Only_This_One"], f)
        try:
            self.assertEqual(ft.known_items(), ["Only_This_One"])
        finally:
            os.remove(beside)
        self.assertGreater(len(ft.known_items()), 300)


class Reading(Base):
    def test_reads_a_folder(self):
        self.assertEqual(len(ft.load_save(self.arcade())), 4)

    def test_reads_the_wal_not_just_the_sqlite(self):
        folder = self.arcade(keep_rows_in_wal=True)
        db = os.path.join(folder, "SaveDataEntity.sqlite")
        self.assertGreater(os.path.getsize(db + "-wal"), 0)
        self.assertEqual(len(ft.load_save(db)), 4)

    def test_leaves_the_source_untouched(self):
        """Opening a WAL database checkpoints it. That must not happen to a save."""
        folder = self.arcade(keep_rows_in_wal=True)
        db = os.path.join(folder, "SaveDataEntity.sqlite")
        before = {s: (os.path.getsize(db + s), os.stat(db + s).st_mtime_ns)
                  for s in ("", "-wal", "-shm") if os.path.exists(db + s)}
        self.assertIn("-wal", before)
        ft.load_save(db)
        after = {s: (os.path.getsize(db + s), os.stat(db + s).st_mtime_ns)
                 for s in ("", "-wal", "-shm") if os.path.exists(db + s)}
        self.assertEqual(before, after)

    def test_sqlite_without_its_wal_explains_itself(self):
        folder = self.arcade(keep_rows_in_wal=True)
        lonely = os.path.join(self.tmp, "alone", "SaveDataEntity.sqlite")
        os.makedirs(os.path.dirname(lonely))
        shutil.copyfile(os.path.join(folder, "SaveDataEntity.sqlite"), lonely)
        with self.assertRaises(ft.SaveError) as caught:
            ft.load_save(lonely)
        self.assertIn("-wal", str(caught.exception))

    def test_reads_a_zip(self):
        folder = self.arcade(keep_rows_in_wal=True)
        db = os.path.join(folder, "SaveDataEntity.sqlite")
        zpath = os.path.join(self.tmp, "FANTASIAN.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            for s in ("", "-wal", "-shm"):
                if os.path.exists(db + s):
                    z.write(db + s, "FANTASIAN/SaveDataEntity.sqlite" + s)
            z.writestr("__MACOSX/FANTASIAN/._SaveDataEntity.sqlite", b"junk")
        self.assertEqual(len(ft.load_save(zpath)), 4)

    def test_reads_back_a_steam_file_it_wrote(self):
        out = os.path.join(self.tmp, "root.json")
        run(["to-steam", self.arcade(), "-o", out])
        save = ft.load_save(out)
        self.assertEqual(save.origin, "steam")
        self.assertEqual(len(save), 4)

    def test_missing_source(self):
        with self.assertRaises(ft.SaveError):
            ft.load_save(os.path.join(self.tmp, "nope.sqlite"))

    def test_describes_a_slot(self):
        """The file name is always shown, because that is what --slot takes."""
        line = ft.describe({"path": "Data/GameData10.json",
                            "dataString": SLOTS["Data/GameData10.json"]})
        self.assertIn("GameData10", line)
        self.assertIn("3,980 G", line)
        self.assertIn("NewTownEn", line)

    def test_gamedata10_is_slot_ten_not_the_autosave(self):
        """Checked against the game's own Load screen: it offers ten manual
        slots, and GameData0 is the autosave rather than one of them. The
        mapping this tool was built on had these two the other way round."""
        self.assertEqual(ft.SLOT_NAMES["10"], "slot 10")
        self.assertEqual(ft.SLOT_NAMES["0"], "autosave")
        self.assertEqual(ft.SLOT_NAMES["1"], "slot 1")
        self.assertEqual(max(int(n) for n in ft.SLOT_NAMES), 10)

    def test_every_save_shows_its_file_name(self):
        for path in ("Data/GameData0.json", "Data/GameData1.json",
                     "Data/GameData10.json"):
            line = ft.describe({"path": path,
                                "dataString": SLOTS.get(path, SLOTS["Data/GameData0.json"])})
            self.assertIn("GameData" + ft.slot_number(path), line)

    def test_describe_survives_a_broken_slot(self):
        line = ft.describe({"path": "Data/GameData0.json", "dataString": "{not json"})
        self.assertIn("could not read", line)


class Ordering(unittest.TestCase):
    def test_game_order(self):
        shuffled = [{"path": p} for p in ("Data/GameData1.json", "Data/GameData0.json",
                                          "Data/GameData2.json", "Data/GameData10.json")]
        self.assertEqual([r["path"] for r in ft.canonical_order(shuffled)],
                         ["Data/GameData0.json", "Data/GameData10.json",
                          "Data/GameData1.json", "Data/GameData2.json"])

    def test_ten_is_the_autosave_not_slot_ten(self):
        order = ft.canonical_order([{"path": f"Data/GameData{n}.json"}
                                    for n in (0, 1, 2, 10)])
        self.assertEqual(ft.slot_number(order[1]["path"]), "10")

    def test_unknown_names_go_last_and_do_not_crash(self):
        order = ft.canonical_order([{"path": "Data/Weird.json"},
                                    {"path": "Data/GameData0.json"}])
        self.assertEqual(order[0]["path"], "Data/GameData0.json")


class ToSteam(Base):
    def setUp(self):
        super().setUp()
        self.folder = self.arcade(keep_rows_in_wal=True)
        self.out = os.path.join(self.tmp, "root.json")

    def test_writes_neo_dimension_shape(self):
        run(["to-steam", self.folder, "-o", self.out])
        for r in self.steam_records(self.out):
            self.assertIn("encryptedString", r)
            self.assertNotIn("dataString", r)
            json.loads(ft.record_plaintext(r))

    def test_output_is_in_game_order(self):
        run(["to-steam", self.folder, "-o", self.out])
        self.assertEqual([r["path"] for r in self.steam_records(self.out)],
                         ["Data/GameData0.json", "Data/GameData10.json",
                          "Data/GameData1.json", "Data/GameData2.json"])

    def test_slots_filter(self):
        run(["to-steam", self.folder, "-o", self.out, "--slots", "0", "10"])
        self.assertEqual([ft.slot_number(r["path"])
                          for r in self.steam_records(self.out)], ["0", "10"])

    def test_slots_that_match_nothing_stops_before_writing(self):
        with self.assertRaises(ft.SaveError):
            run(["to-steam", self.folder, "-o", self.out, "--slots", "7"])
        self.assertFalse(os.path.exists(self.out))

    def test_template_keeps_slots_the_source_does_not_cover(self):
        template = os.path.join(self.tmp, "template.json")
        kept = ft.to_steam_record(
            {"path": "Data/GameData2.json",
             "dataString": make_slot(999, 777, "KeptMap", "2030/01/01 00:00:00")})
        with open(template, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": [kept]})}, f)

        run(["to-steam", self.folder, "-o", self.out, "--slots", "0", "-t", template])
        got = {r["path"]: r for r in self.steam_records(self.out)}
        self.assertEqual(set(got), {"Data/GameData0.json", "Data/GameData2.json"})
        self.assertIn("KeptMap", ft.record_plaintext(got["Data/GameData2.json"]))

    def test_merging_reorders_for_the_editor(self):
        """New slots must not simply land at the end. Slots are addressed by
        position in this format, so an appended autosave shifts everything."""
        template = os.path.join(self.tmp, "one_slot.json")
        only = ft.to_steam_record({"path": "Data/GameData0.json",
                                   "dataString": SLOTS["Data/GameData0.json"]})
        with open(template, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": [only]})}, f)
        run(["to-steam", self.folder, "-o", self.out, "-t", template])
        self.assertEqual([r["path"] for r in self.steam_records(self.out)][1],
                         "Data/GameData10.json")

    def test_install_backs_up_first(self):
        home = os.path.join(self.tmp, "home")
        data = os.path.join(home, "Documents", "My Games", ft.GAME_DIR_NAME,
                            "Steam", "76561", "_data")
        os.makedirs(data)
        root = os.path.join(data, "root.json")
        original = ft.to_steam_record({"path": "Data/GameData0.json",
                                       "dataString": SLOTS["Data/GameData0.json"]})
        with open(root, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": [original]})}, f)
        with open(root, encoding="utf-8") as f:
            before = f.read()

        with fake_home(home):
            run(["to-steam", self.folder, "--install"])

        backups = [f for f in os.listdir(data) if ".backup_" in f]
        self.assertEqual(len(backups), 1, os.listdir(data))
        with open(os.path.join(data, backups[0]), encoding="utf-8") as f:
            self.assertEqual(f.read(), before)
        self.assertEqual(len(self.steam_records(root)), 4)

    def test_install_without_a_steam_save_refuses(self):
        home = os.path.join(self.tmp, "emptyhome")
        os.makedirs(home)
        with fake_home(home):
            with self.assertRaises(ft.SaveError):
                run(["to-steam", self.folder, "--install"])

    def test_warns_when_gamedata10_is_missing(self):
        text = out_of(["to-steam", self.folder, "-o", self.out, "--slots", "0", "1"])
        self.assertIn("no GameData10", text)

    def test_no_warning_when_gamedata10_is_there(self):
        self.assertNotIn("no GameData10",
                         out_of(["to-steam", self.folder, "-o", self.out]))

    def test_reconverting_its_own_output_is_stable(self):
        run(["to-steam", self.folder, "-o", self.out])
        again = os.path.join(self.tmp, "again.json")
        run(["to-steam", self.out, "-o", again])
        self.assertEqual(self.steam_records(self.out), self.steam_records(again))


class ToAccount(Base):
    """The transfer is a guided procedure: the tool moves files, the player
    drives the game. What is worth testing is the file state at each point, and
    that the account's own save comes back exactly as it was."""

    def setUp(self):
        super().setUp()
        self.old = self.arcade("OLD", keep_rows_in_wal=True)
        self.new = self.arcade("NEW", slots={
            "Data/GameData0.json": make_slot(60, 100, "Prologue", "2026/09/01 00:00:00"),
        })
        self.new_db = os.path.join(self.new, "SaveDataEntity.sqlite")

    def digest(self, folder):
        out = {}
        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    out[name] = hashlib.sha256(f.read()).hexdigest()
        return out

    def drive(self, argv, at_each_prompt=None):
        """Run the guided flow, answering every prompt, optionally looking at
        the folder each time it pauses. Adds --anyway, because the command
        refuses on its own: it crashes the game on 2.5.3."""
        argv = list(argv) + ["--anyway"]
        seen = []

        def fake_wait(prompt):
            seen.append(self.digest(self.new))
            if at_each_prompt:
                at_each_prompt(len(seen))
            return True

        real = ft._wait
        ft._wait = fake_wait
        try:
            run(argv)
        finally:
            ft._wait = real
        return seen

    def test_the_accounts_own_save_comes_back_untouched(self):
        before = self.digest(self.new)
        self.drive(["to-account", self.old, "--into", self.new])
        self.assertEqual(before, self.digest(self.new))

    def test_the_old_save_is_in_place_while_the_game_is_asked_to_load_it(self):
        """Between step 2 and step 3 the folder must hold the OLD account's
        saves, because that is what the game's load screen reads."""
        looked = {}

        def peek(n):
            if n == 2:      # the pause after the swap
                looked["paths"] = sorted(
                    ft.slot_number(r["path"])
                    for r in ft.load_save(self.new).records)

        self.drive(["to-account", self.old, "--into", self.new], at_each_prompt=peek)
        self.assertEqual(looked["paths"], ["0", "1", "10", "2"])

    def test_the_accounts_own_save_is_back_before_it_is_asked_to_save(self):
        """By step 5 the originals must be restored, so the game's own save
        writes into the database this account owns."""
        looked = {}

        def peek(n):
            if n == 3:      # the pause before saving in game
                looked["paths"] = sorted(
                    ft.slot_number(r["path"])
                    for r in ft.load_save(self.new).records)

        self.drive(["to-account", self.old, "--into", self.new], at_each_prompt=peek)
        self.assertEqual(looked["paths"], ["0"])

    def test_it_pauses_for_the_player_three_times(self):
        self.assertEqual(len(self.drive(["to-account", self.old, "--into", self.new])), 3)

    def test_a_backup_is_taken_first(self):
        self.drive(["to-account", self.old, "--into", self.new])
        backups = [d for d in os.listdir(self.tmp)
                   if d.startswith("NEW.backup_") and not d.endswith(".in-use")]
        self.assertEqual(len(backups), 1, os.listdir(self.tmp))
        kept = ft.load_save(os.path.join(self.tmp, backups[0]))
        self.assertEqual(len(kept), 1)

    def test_dry_run_moves_nothing(self):
        before = self.digest(self.new)
        run(["to-account", self.old, "--into", self.new, "--dry-run"])
        self.assertEqual(before, self.digest(self.new))
        self.assertFalse([d for d in os.listdir(self.tmp) if ".backup_" in d])

    def test_it_reads_a_zip_source(self):
        zpath = os.path.join(self.tmp, "OLD.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            for name in os.listdir(self.old):
                z.write(os.path.join(self.old, name), "FANTASIAN/" + name)
        looked = {}

        def peek(n):
            if n == 2:
                looked["count"] = len(ft.load_save(self.new))

        self.drive(["to-account", zpath, "--into", self.new], at_each_prompt=peek)
        self.assertEqual(looked["count"], 4)

    def test_refuses_a_target_with_no_save(self):
        empty = os.path.join(self.tmp, "empty")
        os.makedirs(empty)
        with self.assertRaises(ft.SaveError) as caught:
            run(["to-account", self.old, "--into", empty, "--anyway"])
        self.assertIn("saves once", str(caught.exception))

    def test_refuses_a_steam_source(self):
        steam = os.path.join(self.tmp, "root.json")
        run(["to-steam", self.old, "-o", steam])
        with self.assertRaises(ft.SaveError) as caught:
            run(["to-account", steam, "--into", self.new, "--anyway"])
        self.assertIn("to-steam", str(caught.exception))

    def test_it_refuses_by_default(self):
        """It crashes the game on 2.5.3, so it does not run unless asked twice."""
        with self.assertRaises(ft.SaveError) as caught:
            run(["to-account", self.old, "--into", self.new])
        message = str(caught.exception)
        self.assertIn("2.5.3", message)
        self.assertIn("saveContext", message)
        self.assertIn("--anyway", message)
        self.assertEqual(self.digest(self.new), self.digest(self.new))
        self.assertFalse([d for d in os.listdir(self.tmp) if ".backup_" in d])

    def test_dry_run_does_not_need_anyway(self):
        """Reading the plan should not require agreeing to break something."""
        before = self.digest(self.new)
        run(["to-account", self.old, "--into", self.new, "--dry-run"])
        self.assertEqual(before, self.digest(self.new))

    def test_needs_a_source(self):
        with self.assertRaises(ft.SaveError):
            run(["to-account", "--into", self.new, "--anyway"])

    def test_it_will_not_run_with_nobody_at_the_keyboard(self):
        """Every step needs a person driving the game, so a run with nothing on
        stdin must stop rather than march through the swaps on its own."""
        real_stdin = sys.stdin
        sys.stdin = io.StringIO("")
        try:
            with self.assertRaises(ft.SaveError):
                ft._wait("go? ")
        finally:
            sys.stdin = real_stdin


class FileSwapping(Base):
    def setUp(self):
        super().setUp()
        self.folder = self.arcade("LIVE", keep_rows_in_wal=True)
        self.other = self.arcade("OTHER")

    def names(self, folder):
        return sorted(n for n in os.listdir(folder) if n.startswith("SaveDataEntity"))

    def test_the_wal_and_shm_travel_with_the_sqlite(self):
        """The current progress lives in the -wal, so a swap that carries only
        the .sqlite hands the game an empty database."""
        removed, added = ft.replace_saves(self.other, self.folder)
        self.assertIn("SaveDataEntity.sqlite-wal", removed)
        self.assertIn("SaveDataEntity.sqlite-wal", added)

    def test_a_full_round_trip_restores_every_byte(self):
        keep = os.path.join(self.tmp, "keep")
        ft.copy_aside(self.folder, keep)
        before = {}
        for n in self.names(self.folder):
            with open(os.path.join(self.folder, n), "rb") as f:
                before[n] = f.read()
        ft.replace_saves(self.other, self.folder)
        ft.replace_saves(keep, self.folder)
        after = {}
        for n in self.names(self.folder):
            with open(os.path.join(self.folder, n), "rb") as f:
                after[n] = f.read()
        self.assertEqual(before, after)

    def test_replacing_changes_what_the_game_would_read(self):
        ft.replace_saves(self.other, self.folder)
        self.assertEqual(len(ft.load_save(self.folder)), 4)

    def test_replacing_gives_the_file_a_new_inode(self):
        """This is the whole problem. A program holding the old file open is
        left on an orphan, which is what kills the save afterwards."""
        path = os.path.join(self.folder, "SaveDataEntity.sqlite")
        before = os.stat(path).st_ino
        ft.replace_saves(self.other, self.folder)
        self.assertNotEqual(os.stat(path).st_ino, before)

    def test_writing_over_the_top_keeps_the_inode(self):
        """The other half of the trap. The handle stays valid, but SQLite has
        the -shm mapped, so the game does not see the new saves either."""
        path = os.path.join(self.folder, "SaveDataEntity.sqlite")
        before = os.stat(path).st_ino
        ft.copy_over(self.other, self.folder)
        self.assertEqual(os.stat(path).st_ino, before)


class Editing(Base):
    def setUp(self):
        super().setUp()
        self.folder = self.arcade(keep_rows_in_wal=True)
        self.steam = os.path.join(self.tmp, "root.json")
        run(["to-steam", self.folder, "-o", self.steam])

    def slot(self, path, number="0"):
        save = ft.load_save(path)
        return ft.unpack(save.slot(number))

    def test_add_money(self):
        run(["edit", self.steam, "--add-money", "1000"])
        info = json.loads(self.slot(self.steam)["GameSystemInfo"])
        self.assertEqual(info["_money"], 35868 + 1000)

    def test_sp_points_honour_the_argument(self):
        """The original editor hardcoded 400 and ignored what was asked for."""
        run(["edit", self.steam, "--add-sp-points", "5000"])
        status = json.loads(self.slot(self.steam)["PlayerStatus"])
        self.assertEqual(status["_items"][0]["_growthPoint"], 5000)

    def test_an_inventory_command_on_its_own_still_works(self):
        """In the original, four of the insert commands were left out of the
        gate that opened the inventory, so alone they silently did nothing."""
        for flag in ("--insert-all-accessories", "--insert-all-gate-items",
                     "--insert-all-upgrade-materials", "--insert-or-add-sp-capsules"):
            with self.subTest(flag=flag):
                target = os.path.join(self.tmp, "s.json")
                shutil.copyfile(self.steam, target)
                before = len(json.loads(self.slot(target)["Inventory"])
                             ["itemTable"]["keyList"])
                run(["edit", target, flag])
                after = len(json.loads(self.slot(target)["Inventory"])
                            ["itemTable"]["keyList"])
                self.assertGreater(after, before, flag)

    def test_divine_artifacts_are_left_alone(self):
        run(["edit", self.steam, "--insert-all-accessories"])
        keys = json.loads(self.slot(self.steam)["Inventory"])["itemTable"]["keyList"]
        self.assertFalse([k for k in keys if k.startswith("Acce_God")])

    def test_key_and_quest_items_are_refused(self):
        with self.assertRaises(ft.SaveError):
            run(["edit", self.steam, "--insert-items", "Item_Quest_Shell_Black"])

    def test_unknown_items_are_refused(self):
        with self.assertRaises(ft.SaveError):
            run(["edit", self.steam, "--insert-items", "Not_A_Real_Item"])

    def test_analyze_all(self):
        run(["edit", self.steam, "--analyze-all"])
        battle = json.loads(self.slot(self.steam)["BattleData"])
        self.assertTrue(all(v["analyzed"]
                            for v in battle["libraryInfoTable"]["valueList"]))

    def test_says_so_when_nothing_changed(self):
        run(["edit", self.steam, "--analyze-all"])
        self.assertIn("Nothing changed", out_of(["edit", self.steam, "--analyze-all"]))

    def test_refuses_with_no_command(self):
        with self.assertRaises(ft.SaveError):
            run(["edit", self.steam])

    def test_unknown_slot_is_refused(self):
        with self.assertRaises(ft.SaveError):
            run(["edit", self.steam, "--slot", "7", "--add-money", "1"])

    def test_default_picks_the_longest_save(self):
        """Checked by which save actually changed, not by what was printed."""
        run(["edit", self.steam, "--add-money", "7"])
        money = {ft.slot_number(r["path"]):
                 json.loads(ft.unpack(r)["GameSystemInfo"])["_money"]
                 for r in ft.load_save(self.steam).records}
        self.assertEqual(money, {"0": 35875, "1": 35368, "2": 30000, "10": 3980})

    def test_default_will_take_gamedata10_when_it_is_the_longest(self):
        """No save is skipped for looking like an autosave. Which file that is
        cannot be known, so guessing would edit the wrong save."""
        folder = self.arcade("LONGAUTO", slots={
            "Data/GameData0.json": make_slot(100, 1, "A", "2026/01/01 00:00:00"),
            "Data/GameData10.json": make_slot(99999, 2, "B", "2026/01/02 00:00:00"),
        })
        out = os.path.join(self.tmp, "longauto.json")
        run(["to-steam", folder, "-o", out])
        run(["edit", out, "--add-money", "5"])
        money = {ft.slot_number(r["path"]):
                 json.loads(ft.unpack(r)["GameSystemInfo"])["_money"]
                 for r in ft.load_save(out).records}
        self.assertEqual(money, {"0": 1, "10": 7})

    def test_backs_up_the_steam_file(self):
        run(["edit", self.steam, "--add-money", "1"])
        self.assertTrue([f for f in os.listdir(self.tmp)
                         if f.startswith("root.json.backup_")])

    def test_edits_an_arcade_save_in_place(self):
        run(["edit", self.folder, "--add-money", "1000"])
        info = json.loads(self.slot(self.folder)["GameSystemInfo"])
        self.assertEqual(info["_money"], 35868 + 1000)

    def test_editing_an_arcade_save_touches_only_that_slot(self):
        before = {r["path"]: ft.record_plaintext(r)
                  for r in ft.load_save(self.folder).records}
        run(["edit", self.folder, "--add-money", "1000"])
        after = {r["path"]: ft.record_plaintext(r)
                 for r in ft.load_save(self.folder).records}
        changed = [p for p in before if before[p] != after[p]]
        self.assertEqual(changed, ["Data/GameData0.json"])

    def test_an_untouched_arcade_save_rebuilds_byte_for_byte(self):
        save = ft.load_save(self.folder)
        self.assertEqual(zlib.decompress(ft.arcade_blob(save.records)),
                         zlib.decompress(save.raw_blob))

    def test_arcade_record_order_is_preserved(self):
        """The game writes its own order, and a real save had slot 2 first."""
        before = [r["path"] for r in ft.load_save(self.folder).records]
        run(["edit", self.folder, "--add-money", "1"])
        self.assertEqual([r["path"] for r in ft.load_save(self.folder).records], before)

    def test_output_redirects_instead_of_writing_back(self):
        elsewhere = os.path.join(self.tmp, "elsewhere.json")
        run(["edit", self.steam, "--add-money", "1000", "-o", elsewhere])
        self.assertTrue(os.path.exists(elsewhere))
        self.assertEqual(json.loads(self.slot(self.steam)["GameSystemInfo"])["_money"],
                         35868)


class Discovery(Base):
    def plant(self, *parts):
        data = os.path.join(self.tmp, *parts, "My Games", ft.GAME_DIR_NAME,
                            "Steam", "7656", "_data")
        os.makedirs(data)
        path = os.path.join(data, "root.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": []})}, f)
        return path

    def found(self):
        with fake_home(self.tmp):
            return ft.find_steam_roots()

    def test_plain_documents(self):
        self.assertIn(self.plant("Documents"), self.found())

    def test_documents_redirected_into_onedrive(self):
        self.assertIn(self.plant("OneDrive", "Documents"), self.found())

    def test_proton_prefix(self):
        expected = self.plant(".local", "share", "Steam", "steamapps", "compatdata",
                              ft.STEAM_APP_ID, "pfx", "drive_c", "users", "steamuser",
                              "Documents")
        self.assertIn(expected, self.found())

    def test_nothing_planted(self):
        self.assertFalse([p for p in self.found() if p.startswith(self.tmp)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
