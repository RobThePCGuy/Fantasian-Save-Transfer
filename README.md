# FANTASIAN Save Transfer

You put sixty hours into FANTASIAN on Apple Arcade. Neo Dimension came out on Steam, you
bought it, and it started you at zero.

The save is still yours. This moves it.

```
Found 4 save slot(s):

  slot 1     2026/08/19 09:44:41   16.8h    35,868 G   CityVibra
  autosave   2021/06/11 11:52:45    1.0h     3,980 G   NewTownEn
  slot 2     2021/08/23 11:42:06   16.6h    35,368 G   CityVibra
  slot 3     2026/08/19 09:44:41   16.8h    35,868 G   CityVibra
```

## Get your save across

**On the Mac you played on**, double-click `Transfer my FANTASIAN save.command`. It finds
the save, shows you what is in it, and puts a `root.json` on your Desktop.

The first time, macOS will refuse to run a file you downloaded. Right-click it, pick
**Open**, then **Open** again. That happens once.

**On your PC**, back up the file at

```
Documents\My Games\FANTASIAN Neo Dimension\Steam\<your steam id>\_data\root.json
```

then copy your new `root.json` over it. Close the game first. Load the slot and look at it
before you trust it.

If Neo Dimension will not load the file, you have not saved in it yet. Play until the game
writes its first save, then read [Keeping the saves you already
have](#keeping-the-saves-you-already-have).

That is the whole thing. There is nothing to install.

## If you did not play on a Mac

The save lives inside the app's container and this tool needs the whole folder:

```
~/Library/Containers/com.mistwalkercorp.fantasian/Data/Library/Application Support/FANTASIAN
```

Copy that folder off the Mac that has it, zip it, and point the tool at the zip:

```bash
python3 fantasian_transfer.py FANTASIAN.zip
```

**Copy the whole folder, not just `SaveDataEntity.sqlite`.** FANTASIAN keeps your current
progress in the `SaveDataEntity.sqlite-wal` file beside it. Take the `.sqlite` on its own
and you get an empty database, which is why some people end up converting a save from
months ago and never work out what went wrong. On the save this was built against, the
`.sqlite` alone held **zero** slots and everything real was in the `-wal`.

## Keeping the saves you already have

By default this writes a fresh save file with your Apple Arcade slots in it. If you have
Steam progress worth keeping, merge instead:

```bash
python3 fantasian_transfer.py FANTASIAN.zip --auto-template
```

Slots the Apple Arcade save does not cover stay exactly as they are.

`--install` goes one step further and writes straight into your Neo Dimension save folder,
after backing up what is there:

```bash
python3 fantasian_transfer.py FANTASIAN.zip --install
```

It finds the folder itself, including when Windows has redirected Documents into OneDrive
and when you are on a Steam Deck.

## Everything else

```
python3 fantasian_transfer.py --list             what is in the save, then stop
python3 fantasian_transfer.py --slots 0 10       only slot 1 and the autosave
python3 fantasian_transfer.py -o mysave.json     write somewhere else
python3 fantasian_transfer.py --self-test        check the encryption on your machine
```

Slot numbering is the game's, not the menu's: `0` is slot 1, `1` is slot 2, `2` is slot 3,
`10` is the autosave.

## How it works

Both releases store the same save. Only the wrapper around it changed.

| | Apple Arcade | Neo Dimension |
|---|---|---|
| where | `SaveDataEntity.sqlite`, in the `ZGAMEDATAENTITY` table, zlib compressed | `_data/root.json` |
| shape | `{"records":[{"path","dataString"}]}` | `{"dataString":"{\"records\":[{\"path\",\"encryptedString\"}]}"}` |
| the record | plain JSON | AES-128-CBC, then base64 |

Inside, both hold the same twelve keys (`PlayerParty`, `PlayerStatus`, `FlagManager`,
`GameSystemInfo`, `Inventory`, `BattleData`, `DimensionManager`, `ScenarioInfoData`,
`QuestData`, `AchievementCountData`, `Version`, `Date`), and `GameSystemInfo` carries the
same field names in both. So the conversion is: decompress, encrypt each record, rewrap.

Three things this is careful about, all of which cost somebody their save at some point:

**It never touches your Apple Arcade save.** Opening a SQLite database that has a
write-ahead log folds that log back into the main file and rewrites both. On a live game
save that is a real edit to your files. This copies the database and its sidecars
somewhere disposable and reads the copy.

**It puts the slots in the order the game writes them.** Slot 1, autosave, then slots 2
and 3. [mathcodergamer's save editor](https://github.com/mathcodergamer/FantasianND-Save-Editor)
picks slots by position rather than by name, so a file in a different order makes it edit
a save you did not ask for. Asking it for slot 2 on a wrongly ordered file edited the
one-hour autosave instead.

**It checks its own work.** Every record it writes is decrypted again and re-parsed before
the file is saved.

## No dependencies, on purpose

One file, standard library only, Python 3.8 and up. The AES is written out in
`fantasian_transfer.py` rather than pulled from a package, so there is no `pip install`
between you and your save.

Hand-written encryption should have to prove itself, so `--self-test` checks it against
the published NIST vectors (FIPS-197 C.1 for the block cipher, SP 800-38A F.2.1 for CBC
chaining) and runs a round trip. During development it was also checked against
pycryptodome on 500 random key and length combinations and on real save records, byte for
byte.

## Standing on

[mathcodergamer's FantasianND-Save-Editor](https://github.com/mathcodergamer/FantasianND-Save-Editor)
worked out the Neo Dimension save format and the encryption. This tool exists because of
that work, and its output is meant to feed straight into that editor.

[pustal's FantasianSGE](https://github.com/pustal/FantasianSGE) got there first on the
Apple Arcade side and is the reason a lot of people knew this was possible at all. Thanks
also to uior, Xsonicdragon and Square_Ad1583, credited there for the original digging.

## The honest caveat

Neo Dimension is a remaster. The save schema matches, but individual map, flag and item
IDs are not guaranteed identical across both releases. This has been confirmed working on
a mid-Part-1 save, four slots, roughly seventeen hours. Back your Steam save up, and look
at the imported slot in-game before you put another sixty hours on top of it.

Not affiliated with Mistwalker or Square Enix. GPL-3.0.
