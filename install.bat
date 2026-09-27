@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"
title dr1-save-bridge

set "SAVEDIR=%USERPROFILE%\Documents\My Games\Danganronpa"
set "VFS=%SAVEDIR%\savedata.vfs"

where python >nul 2>&1 || (echo [x] Python 3 not found. Install it from https://www.python.org/ & pause & exit /b 1)
tasklist | find /I "Danganronpa" >nul && (echo [x] Close Danganronpa first. & pause & exit /b 1)
if not exist "keys.txt" (
  echo [!] keys.txt not found. Encrypted DATKG.BIN files need it, see README, Keys:
  echo     python dr1_save_bridge.py keys --import ^<PPSSPP source folder^> ^<PPSSPP log^>
  echo.
)
if exist "%VFS%" (
  echo === PC slots right now ===
  python dr1_save_bridge.py info "%VFS%"
) else (
  echo No PC save yet, a new savedata.vfs will be created.
  mkdir "%SAVEDIR%" >nul 2>&1
)
echo.

set "ARGS="
:ask
set "SRC="
set /p "SRC=PSP save DATKG.BIN (drag-and-drop, Enter = done): "
if not defined SRC goto chosen
set "SRC=!SRC:"=!"
if not exist "!SRC!" (echo [x] File not found: !SRC! & goto ask)
set "SLOT="
set /p "SLOT=  -> PC slot number (1-10): "
set "ARGS=!ARGS! --map "!SLOT!=!SRC!""
echo.
goto ask

:chosen
if not defined ARGS (echo Nothing to do. & pause & exit /b 0)

set "ANS="
set /p "ANS=Keep the PSP dialogue history in the log? (y/N): "
set "BACKLOG=clear"
if /i "!ANS!"=="y" set "BACKLOG=keep"

for /f %%t in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "TS=%%t"
set "BK=%~dp0backups\!TS!"
mkdir "!BK!" >nul 2>&1
if exist "%VFS%" copy /Y "%SAVEDIR%\savedata.*" "!BK!\" >nul || (echo [x] Backup failed, nothing was changed. & pause & exit /b 1)
echo.
echo Backup: !BK!

python dr1_save_bridge.py convert --pc-vfs "%VFS%" !ARGS! --backlog !BACKLOG! --install
if errorlevel 1 (
  if exist "!BK!\savedata.vfs" copy /Y "!BK!\savedata.vfs" "%VFS%" >nul
  echo.
  echo [x] Conversion failed, the original save was restored.
  pause & exit /b 1
)

echo.
echo Done. Undo with: copy "!BK!\savedata.vfs" "%SAVEDIR%\"
pause
