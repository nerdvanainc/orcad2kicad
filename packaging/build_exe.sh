#!/usr/bin/env bash
# Bash equivalent of build_exe.cmd (for Git Bash / WSL / Linux where PyInstaller can build a
# Windows exe when run under Windows Python, or a native exe on that OS).
# Usage:
#   packaging/build_exe.sh              -> build both orcad2kicad.exe (GUI) and orcad2kicad-cli.exe
#   packaging/build_exe.sh --cli-only   -> build only orcad2kicad-cli.exe (faster)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
SPEC="$SCRIPT_DIR/orcad2kicad.spec"
MODE="full"

if [[ "${1:-}" == "--cli-only" ]]; then
    MODE="cli-only"
    SPEC="$SCRIPT_DIR/orcad2kicad-cli.spec"
fi

echo "Building ($MODE) with $SPEC"

cd "$REPO_ROOT"
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller "$SPEC"

echo "Build OK. Output files:"
for f in dist/orcad2kicad-cli.exe dist/orcad2kicad.exe; do
    if [[ -f "$f" ]]; then
        ls -l "$f"
    fi
done
