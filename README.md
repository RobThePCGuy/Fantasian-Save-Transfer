# FANTASIAN Save Tool

[![tests](https://github.com/RobThePCGuy/Fantasian-Save-Transfer/actions/workflows/test.yml/badge.svg)](https://github.com/RobThePCGuy/Fantasian-Save-Transfer/actions/workflows/test.yml)

You put sixty hours into FANTASIAN on Apple Arcade. Then Neo Dimension came out, or your
Apple ID changed, and the game started you at zero.

The save is still yours. This moves it.

```
Found 4 save slot(s):

  GameData0  autosave 2026/08/19 09:44:41   16.8h    35,868 G   CityVibra
  GameData10 slot 10  2021/06/11 11:52:45    1.0h     3,980 G   NewTownEn
  GameData1  slot 1   2021/08/23 11:42:06   16.6h    35,368 G   CityVibra
  GameData2  slot 2   2026/08/19 09:44:41   16.8h    35,868 G   CityVibra
```

One file, standard library only, Python 3.8 and up. Nothing to install.

On a Mac, double-click **Fantasian save tool.command** and pick from the menu. The first
time, macOS refuses to run a file you downloaded: right-click it, pick **Open**, then
**Open** again. That happens once.

Everything below is the same thing from the command line.

## Moving a save to another Apple Arcade account

This one is a guided procedure. The tool moves the files; you drive the game.

**Why it cannot just be done for you.** FANTASIAN mirrors its save to iCloud through Core
Data, and Core Data records a change by writing rows into its own history tables,
`ATRANSACTION` and `ACHANGE`. iCloud only ever uploads what it finds there. A write from
outside the game leaves no history, so iCloud never learns it happened, never uploads it,
and the next launch pulls the server's copy back over the top. Preserving the account's
sync metadata makes this worse rather than better: an intact server change token is exactly
what lets iCloud conclude it is already in sync and overwrite without hesitating.

Watched happening, three times in a row: the other account's saves were confirmed in place
on disk, and about thirty seconds later the running game had replaced them with the
account's own again.

**So the only write that counts is one the game itself makes.** The old account's saves go
in front of a running game, you load one, the account's own database goes back underneath,
and then you save in game. That save carries the loaded progress into the account signed
in here, with the history iCloud needs.

```bash
python3 fantasian.py to-account --list-accounts          # who is on this Mac
python3 fantasian.py to-account --from-user OTHERNAME    # read their save, transfer it
python3 fantasian.py to-account /path/to/FANTASIAN       # or from a folder or zip
python3 fantasian.py to-account --from-user OTHERNAME --dry-run
```

`--from-user` reads the other account's own game folder and asks for your password if that
folder is not readable as you. Everything after that copy runs as you. Your save folder is
copied aside before anything moves.

**Two things that will bite you.** After the swap back the Load screen can show NO DATA or
keep listing the old saves; that is a stale handle on a database that moved, and the tool
reads the files back to prove they are fine. Do not quit when you see it, because quitting
throws away the progress in memory, which is the whole point. Save first.

And the game may crash at the save instead:

```
SaveDataManager_SaveGameData
 → GameDataContainer_Save
  → CloudSaveDataStream_Save
   → CoreDataService.writeRecord
    → NSManagedObjectContext performBlockAndWait
     → GameDataEntityController.saveContext(author:)   EXC_BAD_INSTRUCTION
```

That happened on two earlier attempts on 2.5.3. It did not happen on the run this
procedure is built from, and what was different is not known. Nothing was damaged any of
those times: the database came back byte identical and passing an integrity check.

**"Confirm Save Data".** On a later launch the game may say your progress on this device
and on iCloud differ, and offer a card for each with a timestamp, a Checkpoints level and a
slot count. Both cards carry the device name, so both can read the same; the level and slot
count are what tell them apart. Whichever you pick wins.

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

## Getting the save out of another account on the same Mac

If the account you are moving *from* is a second macOS user on this machine, you cannot
read its save from your own login. macOS denies it outright:

```
$ ls /Users/otheruser/Library
ls: /Users/otheruser/Library: Permission denied
```

**Do not reach for sudo.** A tool that moves game saves has no business asking for an
administrator password, and on current macOS elevation alone often still will not get you
into another user's Library. The route that needs no permissions at all:

1. Log in as that other user.
2. Copy their `FANTASIAN` folder (the path below) into `/Users/Shared`.
3. Log back into your own account and point this tool at it.

`/Users/Shared` is world-writable for exactly this, so the copy needs nothing special at
either end. A USB stick or an AirDrop to yourself works the same way.

The tool takes a folder or a zip from anywhere, so once the files are somewhere you can
read, nothing else changes.

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

Saves are listed by file name, `GameData0` through `GameData10`, next to what the game
calls them. `--slot` always takes the file number.

**`GameData<N>` is Slot N, and `GameData0` is the autosave.** That was checked against the
game rather than assumed: on the macOS Apple Arcade build the Load screen offers exactly
ten manual slots, Slot 1 to Slot 10, and the save shown in Slot 1 was `GameData1`, matching
to the second on both date and play time. `GameData0` held a newer save that appeared in no
manual slot.

Worth saying plainly, because this tool started out with it backwards and so does the Neo
Dimension save editor: **`GameData10` is Slot 10, an ordinary save.** It is not an
autosave. Nothing inside a save record says which slot it is, so this can only be
established by looking, and it has only been looked at on that one build. If yours
disagrees, the file names are still exact and `--slot` still takes them.

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

**It changes only what it was asked to change.** Editing one save leaves the others alone,
and an untouched Apple Arcade save rebuilds byte for byte, records in the order the game
itself wrote them. Through an account transfer, the account's own save files are moved
aside whole and put back byte for byte.

**It orders the Neo Dimension file the way that game's save editor expects.** That editor
counts positions rather than reading names, so a file in a different order gets the wrong
save edited: on a wrongly ordered file, asking it for the second save reached a one-hour
save from years earlier. The order is kept for compatibility, not because the positions
mean what that editor thinks they mean. Apple Arcade payloads are left in the game's own
order, which is a different one again.

**It checks its own work.** Every record written is decrypted again and re-parsed before
the file is saved, and each stage of an account transfer reads the folder back and shows
you what the game will now find there.

There is one thing it cannot fix, so it warns instead: a file with no `GameData10` in it.
That is fine for the game, but the Neo Dimension save editor counts positions and expects
one there, so its slot numbers end up pointing at the wrong saves. Use `--slot` here, which
takes the file number.

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

The account transfer has been carried through on 2.5.3, once, watched at the filesystem
the whole way. Two earlier attempts on that build crashed the game instead, and what was
different is not known, so treat it as a procedure with a helper attached rather than a
solved problem. What is verified either way is that it never costs you the save: through
both crashes the database came back byte identical and passing an integrity check, and the
folder is copied aside before anything moves.

Back up, and look at the slot in-game before you put another sixty hours on top of it.

Not affiliated with Mistwalker or Square Enix. GPL-3.0.
