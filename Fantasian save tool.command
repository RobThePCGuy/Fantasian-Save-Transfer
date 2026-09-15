#!/bin/bash
# Double-click me on a Mac. Nothing to install.

cd "$(dirname "$0")" || exit 1

say() { printf '%s\n' "$*"; }
pause() { printf '\n'; read -r -p '  Press return to close. ' _; }

printf '\n  FANTASIAN save tool\n  %s\n\n' "$(printf '%.0s-' {1..40})"

if ! command -v python3 >/dev/null 2>&1; then
    say "  Python 3 is not installed on this Mac."
    say ""
    say "  Open Terminal and run:  xcode-select --install"
    say "  then double-click this again."
    pause; exit 1
fi

if ! python3 fantasian.py self-test >/dev/null 2>&1; then
    say "  The built-in encryption check failed on this Mac."
    say "  Something is wrong with the download. Get a fresh copy."
    pause; exit 1
fi

read_path() {   # Terminal escapes spaces when you drop a path in; undo that.
    local dropped
    read -r -e dropped
    dropped="${dropped%\"}"; dropped="${dropped#\"}"
    dropped="${dropped//\\ / }"
    printf '%s' "$dropped"
}

say "  1  Move my Apple Arcade save to Steam (Neo Dimension)"
say "  2  Move a save in from another Apple Arcade account"
say "  3  Edit a save (money, items, experience)"
say "  4  Show me what is in a save"
say "  q  Quit"
printf '\n'
read -r -p '  Which one? ' choice
printf '\n'

case "$choice" in
1)
    OUT="$HOME/Desktop/root.json"
    [ -e "$OUT" ] && OUT="$HOME/Desktop/root_$(date +%Y%m%d_%H%M%S).json"
    if python3 fantasian.py to-steam -o "$OUT"; then
        printf '\n  Done. Your save is on the Desktop:\n    %s\n' "$(basename "$OUT")"
        say ""
        say "  Copy that onto your PC, into"
        say "    Documents\\My Games\\FANTASIAN Neo Dimension\\Steam\\<your id>\\_data\\"
        say "  over the root.json already there. Back that one up first."
    else
        printf '\n  It stopped. The reason is above.\n'
    fi
    ;;
2)
    say "  This puts a save from another Apple Arcade account into the account"
    say "  signed in on this Mac. That account needs to have saved once already."
    say ""
    say "  You drive the game; this moves the files at the right moments."
    printf '\n'
    python3 fantasian.py to-account --list-accounts
    printf '\n'
    say "  Type the name of the account to take the save FROM, or drag in a"
    say "  FANTASIAN folder or zip instead. Press return on its own to stop."
    printf '\n'
    SRC="$(read_path)"
    printf '\n'
    if [ -z "$SRC" ]; then
        printf '  Nothing given, stopping.\n'
    elif [ -e "$SRC" ]; then
        python3 fantasian.py to-account "$SRC" || printf '\n  It stopped. The reason is above.\n'
    else
        # not a path, so treat it as an account name on this Mac
        python3 fantasian.py to-account --from-user "$SRC" || printf '\n  It stopped. The reason is above.\n'
    fi
    ;;
3)
    say "  Press return to edit this Mac's Apple Arcade save, or drag in a"
    say "  FANTASIAN folder, a zip, or a Neo Dimension root.json first."
    printf '\n'
    TARGET="$(read_path)"
    printf '\n'
    say "  a  Money, and analyze every enemy you have met"
    say "  b  All of the above, plus every weapon, armour and accessory"
    say "  c  Everything, including Part 2 items (only after you unlock skill points)"
    printf '\n'
    read -r -p '  Which one? ' how
    printf '\n'
    case "$how" in
      a) FLAGS=(--add-money --analyze-all --add-box-keys
                --add-recovery-items --add-battle-items) ;;
      b) FLAGS=(--add-money --analyze-all --add-box-keys
                --add-recovery-items --add-battle-items --add-accessories
                --insert-all-weapons --insert-all-armors --insert-all-accessories) ;;
      c) FLAGS=(--add-money --analyze-all --add-box-keys
                --add-recovery-items --add-battle-items --add-accessories
                --insert-all-weapons --insert-all-armors --insert-all-accessories
                --insert-or-add-sp-capsules --insert-all-gate-items
                --insert-all-upgrade-materials) ;;
      *) printf '  Not one of the choices.\n'; pause; exit 0 ;;
    esac
    if [ -z "$TARGET" ]; then
        python3 fantasian.py edit "${FLAGS[@]}" || printf '\n  It stopped. The reason is above.\n'
    else
        python3 fantasian.py edit "$TARGET" "${FLAGS[@]}" || printf '\n  It stopped. The reason is above.\n'
    fi
    ;;
4)
    say "  Press return to read this Mac's Apple Arcade save, or drag a"
    say "  FANTASIAN folder, a zip, or a root.json in first."
    printf '\n'
    dropped="$(read_path)"
    printf '\n'
    if [ -z "$dropped" ]; then
        python3 fantasian.py slots || true
    else
        python3 fantasian.py slots "$dropped" || true
    fi
    ;;
q|Q|"")
    printf '  Nothing done.\n'
    ;;
*)
    printf '  Not one of the choices.\n'
    ;;
esac

pause
