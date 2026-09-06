#!/usr/bin/env python3
"""Tests for fantasian_transfer. Standard library only, no real save data needed.

    python3 test_transfer.py
"""

import contextlib
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
import fantasian_transfer as ft


def run(argv):
    """Call the tool, keeping its chatter out of the test report."""
    with contextlib.redirect_stdout(io.StringIO()):
        return ft.main(argv)


def make_slot(play_seconds, money, map_id, date):
    """A save record shaped the way the game writes them."""
    payload = {
        "keys": ["GameSystemInfo", "Date", "PlayerParty", "Inventory"],
        "values": [
            json.dumps({"_playTimeSec": play_seconds, "_money": money,
                        "_mapId": map_id}),
            date, "{}", "{}",
        ],
    }
    return json.dumps(payload)


SLOTS = {
    "Data/GameData0.json": make_slot(60000, 35868, "CityVibra", "2026/08/19 09:44:41"),
    "Data/GameData1.json": make_slot(59700, 35368, "CityVibra", "2021/08/23 11:42:06"),
    "Data/GameData2.json": make_slot(60000, 35868, "CityVibra", "2026/08/19 09:44:41"),
    "Data/GameData10.json": make_slot(3600, 3980, "NewTownEn", "2021/06/11 11:52:45"),
}


def arcade_blob():
    records = [{"path": p, "dataString": d} for p, d in SLOTS.items()]
    return zlib.compress(json.dumps({"records": records}).encode("utf-8"))


def write_arcade_db(path, keep_rows_in_wal=False, blob=None):
    """Build an Apple Arcade style database.

    With keep_rows_in_wal the rows are copied out while a connection is still
    open, so the resulting .sqlite is the empty checkpoint and the -wal beside it
    carries everything. That is the shape a live FANTASIAN save has on disk.
    """
    work = tempfile.mkdtemp(prefix="mkdb_")
    live = os.path.join(work, "SaveDataEntity.sqlite")
    con = sqlite3.connect(live)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE ZGAMEDATAENTITY (Z_PK INTEGER PRIMARY KEY, ZTIME FLOAT,"
                " ZDEVICENAME VARCHAR, ZID VARCHAR, ZUUID VARCHAR, ZDATA BLOB)")
    con.commit()
    if keep_rows_in_wal:
        # Everything from here on lands in the -wal, not the .sqlite.
        con.execute("PRAGMA wal_autocheckpoint=0")
    con.execute("INSERT INTO ZGAMEDATAENTITY (ZTIME, ZDEVICENAME, ZID, ZUUID, ZDATA)"
                " VALUES (?,?,?,?,?)",
                (810404856.1, "Test Mac", "root.json", "UUID-1",
                 blob if blob is not None else arcade_blob()))
    con.commit()

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(live + suffix):
            shutil.copyfile(live + suffix, path + suffix)
    con.close()
    shutil.rmtree(work, ignore_errors=True)


class Encryption(unittest.TestCase):
    def test_published_vectors(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ft.self_test(), 0)

    def test_round_trip(self):
        for size in (0, 1, 15, 16, 17, 4096):
            blob = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
            self.assertEqual(ft.decrypt(ft.encrypt(blob)), blob, size)

    def test_padding_is_always_added(self):
        # A whole-block input must still gain a full block of padding, or the
        # game rejects the record.
        self.assertEqual(len(ft.encrypt(b"A" * 16)), 32)

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            ft.decrypt(b"not a whole block")
        with self.assertRaises(ValueError):
            ft.decrypt(b"\x00" * 32)


class Reading(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst_")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_reads_a_database(self):
        db = os.path.join(self.tmp, "SaveDataEntity.sqlite")
        write_arcade_db(db)
        self.assertEqual(len(ft.load_source_records(db)), 4)

    def test_reads_the_wal_not_just_the_sqlite(self):
        db = os.path.join(self.tmp, "SaveDataEntity.sqlite")
        write_arcade_db(db, keep_rows_in_wal=True)
        self.assertTrue(os.path.getsize(db + "-wal") > 0)
        self.assertEqual(len(ft.load_source_records(db)), 4)

    def test_leaves_the_source_untouched(self):
        """Opening a WAL database checkpoints it. That must not happen to a save."""
        db = os.path.join(self.tmp, "SaveDataEntity.sqlite")
        write_arcade_db(db, keep_rows_in_wal=True)
        before = {s: (os.path.getsize(db + s), os.stat(db + s).st_mtime_ns)
                  for s in ("", "-wal", "-shm") if os.path.exists(db + s)}
        self.assertIn("-wal", before)
        ft.load_source_records(db)
        after = {s: (os.path.getsize(db + s), os.stat(db + s).st_mtime_ns)
                 for s in ("", "-wal", "-shm") if os.path.exists(db + s)}
        self.assertEqual(before, after)

    def test_sqlite_without_its_wal_explains_itself(self):
        db = os.path.join(self.tmp, "SaveDataEntity.sqlite")
        write_arcade_db(db, keep_rows_in_wal=True)
        lonely = os.path.join(self.tmp, "alone", "SaveDataEntity.sqlite")
        os.makedirs(os.path.dirname(lonely))
        shutil.copyfile(db, lonely)
        with self.assertRaises(ft.SaveError) as caught:
            ft.load_source_records(lonely)
        self.assertIn("-wal", str(caught.exception))

    def test_reads_a_zip(self):
        db = os.path.join(self.tmp, "SaveDataEntity.sqlite")
        write_arcade_db(db, keep_rows_in_wal=True)
        zpath = os.path.join(self.tmp, "FANTASIAN.zip")
        with zipfile.ZipFile(zpath, "w") as z:
            for s in ("", "-wal", "-shm"):
                if os.path.exists(db + s):
                    z.write(db + s, "FANTASIAN/SaveDataEntity.sqlite" + s)
            # The junk macOS puts in a zip must be ignored.
            z.writestr("__MACOSX/FANTASIAN/._SaveDataEntity.sqlite", b"junk")
        self.assertEqual(len(ft.load_source_records(zpath)), 4)

    def test_reads_a_folder(self):
        folder = os.path.join(self.tmp, "FANTASIAN")
        write_arcade_db(os.path.join(folder, "SaveDataEntity.sqlite"))
        self.assertEqual(len(ft.load_source_records(folder)), 4)

    def test_missing_source(self):
        with self.assertRaises(ft.SaveError):
            ft.load_source_records(os.path.join(self.tmp, "nope.sqlite"))

    def test_describes_a_slot(self):
        line = ft.describe({"path": "Data/GameData10.json",
                            "dataString": SLOTS["Data/GameData10.json"]})
        self.assertIn("autosave", line)
        self.assertIn("3,980 G", line)
        self.assertIn("NewTownEn", line)

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
        """Sorting the numbers as text would put 10 between 1 and 2."""
        order = ft.canonical_order([{"path": f"Data/GameData{n}.json"}
                                    for n in (0, 1, 2, 10)])
        self.assertEqual(ft.slot_number(order[1]["path"]), "10")

    def test_unknown_names_go_last_and_do_not_crash(self):
        order = ft.canonical_order([{"path": "Data/Weird.json"},
                                    {"path": "Data/GameData0.json"}])
        self.assertEqual(order[0]["path"], "Data/GameData0.json")


class Converting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst_out_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.db = os.path.join(self.tmp, "SaveDataEntity.sqlite")
        write_arcade_db(self.db, keep_rows_in_wal=True)
        self.out = os.path.join(self.tmp, "root.json")

    def records(self, path):
        with open(path, encoding="utf-8") as f:
            return json.loads(json.load(f)["dataString"])["records"]

    def test_writes_neo_dimension_shape(self):
        run([self.db, "-o", self.out])
        for r in self.records(self.out):
            self.assertIn("encryptedString", r)
            self.assertNotIn("dataString", r)
            json.loads(ft.record_plaintext(r))   # decrypts to a real save

    def test_output_is_in_game_order(self):
        run([self.db, "-o", self.out])
        self.assertEqual([r["path"] for r in self.records(self.out)],
                         ["Data/GameData0.json", "Data/GameData10.json",
                          "Data/GameData1.json", "Data/GameData2.json"])

    def test_slots_filter(self):
        run([self.db, "-o", self.out, "--slots", "0", "10"])
        self.assertEqual([ft.slot_number(r["path"]) for r in self.records(self.out)],
                         ["0", "10"])

    def test_slots_that_match_nothing_stops_before_writing(self):
        with self.assertRaises(ft.SaveError):
            run([self.db, "-o", self.out, "--slots", "7"])
        self.assertFalse(os.path.exists(self.out))

    def test_list_writes_nothing(self):
        run([self.db, "--list", "-o", self.out])
        self.assertFalse(os.path.exists(self.out))

    def test_template_keeps_slots_the_source_does_not_cover(self):
        template = os.path.join(self.tmp, "template.json")
        kept = {"path": "Data/GameData2.json",
                "encryptedString": ft.to_steam_record(
                    {"path": "Data/GameData2.json",
                     "dataString": make_slot(999, 777, "KeptMap", "2030/01/01 00:00:00")}
                )["encryptedString"]}
        with open(template, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": [kept]})}, f)

        run([self.db, "-o", self.out, "--slots", "0", "-t", template])
        out = {r["path"]: r for r in self.records(self.out)}
        self.assertEqual(set(out), {"Data/GameData0.json", "Data/GameData2.json"})
        self.assertIn("KeptMap", ft.record_plaintext(out["Data/GameData2.json"]))

    def test_merging_reorders_for_the_save_editor(self):
        """New slots must not simply land at the end. The editor picks slots by
        position, so an appended autosave makes it edit the wrong save."""
        template = os.path.join(self.tmp, "one_slot.json")
        only = ft.to_steam_record({"path": "Data/GameData0.json",
                                   "dataString": SLOTS["Data/GameData0.json"]})
        with open(template, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": [only]})}, f)

        run([self.db, "-o", self.out, "-t", template])
        paths = [r["path"] for r in self.records(self.out)]
        self.assertEqual(paths[1], "Data/GameData10.json", paths)

    def test_install_backs_up_first(self):
        home = os.path.join(self.tmp, "home")
        data = os.path.join(home, "Documents", "My Games",
                            "FANTASIAN Neo Dimension", "Steam", "76561", "_data")
        os.makedirs(data)
        root = os.path.join(data, "root.json")
        original = ft.to_steam_record({"path": "Data/GameData0.json",
                                       "dataString": SLOTS["Data/GameData0.json"]})
        with open(root, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": [original]})}, f)
        before = open(root, encoding="utf-8").read()

        real_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            run([self.db, "--install"])
        finally:
            if real_home is not None:
                os.environ["HOME"] = real_home

        backups = [f for f in os.listdir(data) if ".backup_" in f]
        self.assertEqual(len(backups), 1, os.listdir(data))
        self.assertEqual(open(os.path.join(data, backups[0]), encoding="utf-8").read(),
                         before)
        self.assertEqual(len(self.records(root)), 4)

    def test_install_without_a_steam_save_refuses(self):
        home = os.path.join(self.tmp, "emptyhome")
        os.makedirs(home)
        real_home = os.environ.get("HOME")
        os.environ["HOME"] = home
        try:
            with self.assertRaises(ft.SaveError):
                run([self.db, "--install"])
        finally:
            if real_home is not None:
                os.environ["HOME"] = real_home

    def test_reconverting_its_own_output_is_stable(self):
        run([self.db, "-o", self.out])
        again = os.path.join(self.tmp, "again.json")
        run([self.out, "-o", again])
        self.assertEqual(self.records(self.out), self.records(again))


class Discovery(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fst_find_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.real_home = os.environ.get("HOME")
        self.real_onedrive = os.environ.get("OneDrive")

    def tearDown(self):
        if self.real_home is not None:
            os.environ["HOME"] = self.real_home
        os.environ.pop("OneDrive", None)
        if self.real_onedrive is not None:
            os.environ["OneDrive"] = self.real_onedrive

    def plant(self, *parts):
        data = os.path.join(self.tmp, *parts, "My Games",
                            "FANTASIAN Neo Dimension", "Steam", "7656", "_data")
        os.makedirs(data)
        path = os.path.join(data, "root.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"dataString": json.dumps({"records": []})}, f)
        return path

    def test_plain_documents(self):
        expected = self.plant("Documents")
        os.environ["HOME"] = self.tmp
        self.assertIn(expected, ft.find_steam_roots())

    def test_documents_redirected_into_onedrive(self):
        expected = self.plant("OneDrive", "Documents")
        os.environ["HOME"] = self.tmp
        self.assertIn(expected, ft.find_steam_roots())

    def test_proton_prefix(self):
        expected = self.plant(".local", "share", "Steam", "steamapps", "compatdata",
                              "2844850", "pfx", "drive_c", "users", "steamuser",
                              "Documents")
        os.environ["HOME"] = self.tmp
        self.assertIn(expected, ft.find_steam_roots())

    def test_nothing_planted(self):
        os.environ["HOME"] = self.tmp
        self.assertEqual(ft.find_steam_roots(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
