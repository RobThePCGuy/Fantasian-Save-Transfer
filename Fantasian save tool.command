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

say "  1  Move my Apple Arcade save to Steam (Neo Dimension)"
say "  2  Move a save into the Apple Arcade account on this Mac"
say "  3  Show me what is in a save"
say "  q  Quit"
printf '\n'
read -r -p '  Which one? ' choice
printf '\n'

ask_for_folder() {
    say "  Drag the FANTASIAN folder (or its zip) from the OTHER account into"
    say "  this window, then press return."
    printf '\n'
    read -r -e dropped
    # Terminal escapes spaces when you drop a path in; undo that.
    dropped="${dropped%\"}"; dropped="${dropped#\"}"
    dropped="${dropped//\\ / }"
    printf '%s' "$dropped"
}

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
    printf '\n'
    SRC="$(ask_for_folder)"
    if [ -z "$SRC" ]; then
        printf '\n  Nothing given, stopping.\n'
    else
        printf '\n'
        python3 fantasian.py to-account "$SRC" || printf '\n  It stopped. The reason is above.\n'
    fi
    ;;
3)
    say "  Press return to read this Mac's Apple Arcade save, or drag a"
    say "  FANTASIAN folder, a zip, or a root.json in first."
    printf '\n'
    read -r -e dropped
    dropped="${dropped%\"}"; dropped="${dropped#\"}"; dropped="${dropped//\\ / }"
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
