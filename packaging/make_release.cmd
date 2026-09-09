@echo off
REM Assemble the public release zip (exe + docs) via make_release.py.
REM Usage:
REM   packaging\make_release.cmd                 -> build exe(s), then assemble + zip + hash
REM   packaging\make_release.cmd --no-build       -> assemble from existing dist\*.exe only
REM   packaging\make_release.cmd --version-check  -> also confirm exe --version matches VERSION
REM Any arguments are passed through to make_release.py as-is.
setlocal

set "REPO_ROOT=%~dp0.."

pushd "%REPO_ROOT%"
python packaging\make_release.py %*
set "RC=%ERRORLEVEL%"
popd

if not "%RC%"=="0" (
    echo make_release FAILED with exit code %RC%
)

endlocal & exit /b %RC%
