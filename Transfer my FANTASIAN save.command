#!/bin/bash
# Double-click me on the Mac you played FANTASIAN on.
#
# Finds the Apple Arcade save, shows you what is in it, and writes a root.json
# on your Desktop that FANTASIAN Neo Dimension on Steam can load.

cd "$(dirname "$0")" || exit 1

OUT="$HOME/Desktop/root.json"

printf '\n  FANTASIAN save transfer\n  Apple Arcade to Neo Dimension\n\n'

if ! command -v python3 >/dev/null 2>&1; then
    printf '  Python 3 is not installed on this Mac.\n\n'
    printf '  Open Terminal and run:  xcode-select --install\n'
    printf '  then double-click this again.\n\n'
    read -r -p '  Press return to close. '
    exit 1
fi

if ! python3 fantasian_transfer.py --self-test >/dev/null 2>&1; then
    printf '  The built-in encryption check failed on this Mac.\n'
    printf '  Something is wrong with the download. Get a fresh copy.\n\n'
    read -r -p '  Press return to close. '
    exit 1
fi

if [ -e "$OUT" ]; then
    OUT="$HOME/Desktop/root_$(date +%Y%m%d_%H%M%S).json"
fi

if python3 fantasian_transfer.py -o "$OUT"; then
    printf '\n  Done. Your save is on the Desktop:\n    %s\n' "$(basename "$OUT")"
    printf '\n  Copy that onto your PC, into\n'
    printf '    Documents\\My Games\\FANTASIAN Neo Dimension\\Steam\\<your id>\\_data\\\n'
    printf '  replacing the root.json already there. Back that one up first.\n\n'
else
    printf '\n  It stopped. The reason is above.\n\n'
fi

read -r -p '  Press return to close. '
