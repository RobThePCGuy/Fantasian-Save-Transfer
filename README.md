# FANTASIAN Save Tool

[![tests](https://github.com/RobThePCGuy/Fantasian-Save-Transfer/actions/workflows/test.yml/badge.svg)](https://github.com/RobThePCGuy/Fantasian-Save-Transfer/actions/workflows/test.yml)

You put sixty hours into FANTASIAN on Apple Arcade. Then Neo Dimension came out, or your
Apple ID changed, and the game started you at zero.

The save is still yours. This moves it.

```
Found 4 save slot(s):

  GameData0  2026/08/19 09:44:41   16.8h    35,868 G   CityVibra
  GameData10 2021/06/11 11:52:45    1.0h     3,980 G   NewTownEn
  GameData1  2021/08/23 11:42:06   16.6h    35,368 G   CityVibra
  GameData2  2026/08/19 09:44:41   16.8h    35,868 G   CityVibra
```

One file, standard library only, Python 3.8 and up. Nothing to install.

On a Mac, double-click **Fantasian save tool.command** and pick from the menu. The first
time, macOS refuses to run a file you downloaded: right-click it, pick **Open**, then
**Open** again. That happens once.

Everything below is the same thing from the command line.

## Move a save to another Apple Arcade account

The one iCloud fights you on. Copying the save folder from the old account into the new
one does not work: the game starts, syncs, and replaces your progress with the new
account's empty save. The database carries sync metadata that belongs to the old account,
iCloud sees a stranger's file, and throws it away.

So this does not copy the folder. It puts the old save **inside the database the new
account already owns**, leaving every piece of that account's sync metadata untouched.

```bash
python3 fantasian.py to-account /path/to/old/FANTASIAN
```

The new account needs to have saved at least once, so there is a database to write into.
Sign in, start FANTASIAN, play to the first save, quit. Then run the command, start the
game, load the slot, and **save once through the game's own menu**. That save is what
pushes your progress up under the new account.

`--dry-run` shows what it would do. `--into FOLDER` writes somewhere other than this Mac's
own save. The whole folder is backed up before anything is written.

## Move a save to Neo Dimension on Steam

```bash
python3 fantasian.py to-steam                    # the save on this Mac
python3 fantasian.py to-steam FANTASIAN.zip      # a save from another machine
```

That writes `root.json`. On your PC, back up the file at

```
Documents\My Games\FANTASIAN Neo Dimension\Steam\<your steam id>\_data\root.json
```

and copy the new one over it, with the game closed.

If you have Steam progress worth keeping, merge instead of replacing:

```bash
python3 fantasian.py to-steam FANTASIAN.zip --auto-template
```

Slots the Apple Arcade save does not cover stay exactly as they are. `--install` goes one
further and writes into the Neo Dimension save folder itself, backing up what is there
first. It finds that folder on its own, including when Windows has redirected Documents
into OneDrive and when you are on a Steam Deck.

## Edit a save

Works on an Apple Arcade save and a Neo Dimension `root.json` alike.

```bash
python3 fantasian.py edit root.json --add-money --analyze-all --add-box-keys
python3 fantasian.py edit root.json --insert-all-weapons --insert-all-armors
python3 fantasian.py edit root.json --add-exp-mult 4 --add-sp-points 400
```

By default it edits whichever save has the most time on it; `--slot N` names one, using the
same `GameData` numbers the listing shows. The file is backed up before it is written, and
a command that finds nothing to do says so and writes nothing.

`--insert-or-add-sp-capsules`, `--insert-all-gate-items` and
`--insert-all-upgrade-materials` are Part 2 content. Do not use them before you have
unlocked skill points or started act 2.

Run `python3 fantasian.py edit --help` for the full list.

## If you did not play on a Mac

The save sits inside the app's container, and this needs the whole folder:

```
~/Library/Containers/com.mistwalkercorp.fantasian/Data/Library/Application Support/FANTASIAN
```

Copy that folder off the Mac that has it, zip it, and point the tool at the zip.

**Copy the whole folder, not just `SaveDataEntity.sqlite`.** FANTASIAN keeps your current
progress in the `SaveDataEntity.sqlite-wal` file beside it. Take the `.sqlite` on its own
and you get an empty database, which is how people end up transferring a save from months
ago and never work out why. On the save this was built against, the `.sqlite` alone held
**zero** slots and everything real lived in the `-wal`.

## Everything else

```
python3 fantasian.py slots                 what is in a save
python3 fantasian.py self-test             check the encryption on your machine
```

Saves are listed by file name: `GameData0`, `GameData1`, `GameData2`, `GameData10`. That
name is the only slot identity a save has. **Nothing inside a save says which slot the
game's own menu calls it**, so this tool does not claim to know: it shows you the date,
playtime, money and location, which is what you will recognise, and leaves the numbering
alone. Earlier versions printed "slot 1" and "autosave" against those numbers; that mapping
was inherited from elsewhere, never checked, and a player found it disagreeing with what
the Mac game showed.

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
same field names in both.

Four things this is careful about:

**It never writes to a save it was only asked to read.** Opening a SQLite database that
has a write-ahead log folds that log back into the main file and rewrites both. On a live
game save that is a real edit to your files. Reads happen against a copy.

**It changes only what it was asked to change.** An account transfer touches one column of
one row; of the twenty-five tables in a real save database, twenty-four come through
byte-identical, and the row keeps its own key, UUID and device name. Editing one slot
leaves the others alone, and an untouched Apple Arcade save rebuilds byte for byte,
records in the order the game itself wrote them.

**It orders the Neo Dimension file the way that game's save editor expects.** That editor
addresses saves by position rather than by name, so a file in a different order gets the
wrong save edited: on a wrongly ordered file, asking it for the second save reached a
one-hour save from years earlier instead. Apple Arcade payloads are left in the game's own
order, which is not the same one.

**It checks its own work.** Every record written is decrypted again and re-parsed first,
and an account transfer reads the result back and compares it against the source before it
says it worked.

There is one thing it cannot fix, so it warns instead: an Apple Arcade save with no
`GameData10` in it. The game handles that fine, but the Neo Dimension save editor expects
one and counts positions, so it will misread which save is which. Name saves with `--slot`
rather than trusting that editor's numbering.

## No dependencies, on purpose

The AES and the item list are written into `fantasian.py` rather than pulled from
packages, so there is no `pip install` between you and your save.

Hand-written encryption should have to prove itself, so `self-test` checks it against the
published NIST vectors (FIPS-197 C.1 for the block cipher, SP 800-38A F.2.1 for CBC
chaining) and runs a round trip. During development it was also checked against
pycryptodome on 500 random key and length combinations and on real save records, byte for
byte.

A `known_item_ids.json` placed beside the script overrides the built-in list, so it can be
extended without editing the code.

## Standing on

[mathcodergamer's FantasianND-Save-Editor](https://github.com/mathcodergamer/FantasianND-Save-Editor)
worked out the Neo Dimension save format, the encryption, and the 375-entry item list
carried here. The editing commands descend from that work and are here under the same
GPL-3.0. Three things were fixed on the way across:

- Four of the insert commands were missing from the check that opened the inventory, so
  asking for only one of those four printed a success line and wrote nothing.
- `--add-sp-points` ignored the number you gave it and always added 400.
- The item list was loaded from a relative path, so the tool only ran from inside its own
  directory.

[pustal's FantasianSGE](https://github.com/pustal/FantasianSGE) got there first on the
Apple Arcade side and is the reason a lot of people knew this was possible at all. Thanks
also to uior, Xsonicdragon and Square_Ad1583, credited there for the original digging.

## The honest caveat

Neo Dimension is a remaster. The save schema matches, but individual map, flag and item
IDs are not guaranteed identical across both releases. The Steam transfer is confirmed
working on a mid-Part-1 save, four slots, roughly seventeen hours.

The account transfer is verified down to the database: the right save goes in, the account
metadata comes through untouched, and it reads back matching the source. Whether the game
then binds it to the new account on your machine is the part only you can see, which is
why it backs the whole folder up first and tells you where the backup went.

Back up, and look at the slot in-game before you put another sixty hours on top of it.

Not affiliated with Mistwalker or Square Enix. GPL-3.0.
