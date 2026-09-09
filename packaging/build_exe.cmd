@echo off
REM Build single-exe distribution with PyInstaller.
REM Usage:
REM   packaging\build_exe.cmd            -> build both orcad2kicad.exe (GUI) and orcad2kicad-cli.exe (CLI/MCP)
REM   packaging\build_exe.cmd --cli-only -> build only orcad2kicad-cli.exe (faster, skips tkinter GUI build)
REM Output goes to dist\ (workfiles in build\pyinstaller). Requires PyInstaller installed
REM (python -m pip install pyinstaller) and this script run from the repo root.
setlocal

set "REPO_ROOT=%~dp0.."
set "SPEC=%~dp0orcad2kicad.spec"
set "WHAT=GUI + CLI exes"

REM NOTE: %VAR% inside a parenthesised block expands when the whole block is PARSED,
REM not when it runs. So the echo must come AFTER the block, or it would print the
REM old SPEC value. (Delayed expansion would work too, but this is simpler.)
if /I "%~1"=="--cli-only" (
    set "SPEC=%~dp0orcad2kicad-cli.spec"
    set "WHAT=CLI-only exe"
)

echo Building %WHAT% with %SPEC%

pushd "%REPO_ROOT%"
python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\pyinstaller "%SPEC%"
set "BUILD_RC=%ERRORLEVEL%"
popd

if not "%BUILD_RC%"=="0" (
    echo Build FAILED with exit code %BUILD_RC%
    exit /b %BUILD_RC%
)

echo Build OK. Output files:
if exist "%REPO_ROOT%\dist\orcad2kicad-cli.exe" (
    dir /-C "%REPO_ROOT%\dist\orcad2kicad-cli.exe"
)
if exist "%REPO_ROOT%\dist\orcad2kicad.exe" (
    dir /-C "%REPO_ROOT%\dist\orcad2kicad.exe"
)

endlocal
