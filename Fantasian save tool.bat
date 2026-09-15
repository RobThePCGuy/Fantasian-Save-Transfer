@echo off
REM Double-click me on Windows. Nothing to install.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo.
echo   FANTASIAN save tool
echo   ----------------------------------------
echo.

set PY=
where py >nul 2>&1 && set PY=py -3
if not defined PY (where python >nul 2>&1 && set PY=python)
if not defined PY (
    echo   Python 3 is not installed.
    echo.
    echo   Get it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
    echo   then double-click this again.
    goto :done
)

%PY% fantasian.py self-test >nul 2>&1
if errorlevel 1 (
    echo   The built-in encryption check failed on this PC.
    echo   Something is wrong with the download. Get a fresh copy.
    goto :done
)

echo   1  Edit my Neo Dimension save ^(money, items, experience^)
echo   2  Bring an Apple Arcade save over from a Mac
echo   3  Show me what is in a save
echo   q  Quit
echo.
set /p choice=  Which one? 
echo.

if /i "%choice%"=="1" goto :edit
if /i "%choice%"=="2" goto :import
if /i "%choice%"=="3" goto :show
if /i "%choice%"=="q" echo   Nothing done. & goto :done
if "%choice%"=="" echo   Nothing done. & goto :done
echo   Not one of the choices.
goto :done

:edit
echo   Press enter to edit the Neo Dimension save on this PC, or drag your
echo   root.json in here first.
echo.
set "target="
set /p target=  Save: 
set target=%target:"=%
echo.
echo   a  Money, and analyze every enemy you have met
echo   b  All of the above, plus every weapon, armour and accessory
echo   c  Everything, including Part 2 items ^(only after you unlock skill points^)
echo.
set /p how=  Which one? 
echo.
set "FLAGS="
if /i "%how%"=="a" set FLAGS=--add-money --analyze-all --add-box-keys --add-recovery-items --add-battle-items
if /i "%how%"=="b" set FLAGS=--add-money --analyze-all --add-box-keys --add-recovery-items --add-battle-items --add-accessories --insert-all-weapons --insert-all-armors --insert-all-accessories
if /i "%how%"=="c" set FLAGS=--add-money --analyze-all --add-box-keys --add-recovery-items --add-battle-items --add-accessories --insert-all-weapons --insert-all-armors --insert-all-accessories --insert-or-add-sp-capsules --insert-all-gate-items --insert-all-upgrade-materials
if not defined FLAGS echo   Not one of the choices. & goto :done
if "%target%"=="" (%PY% fantasian.py edit %FLAGS%) else (%PY% fantasian.py edit "%target%" %FLAGS%)
goto :done

:import
echo   Drag in the FANTASIAN folder or zip copied off the Mac, then press enter.
echo   ^(Copy the whole folder. The save lives in the -wal file beside the .sqlite.^)
echo.
set "src="
set /p src=  Save: 
set src=%src:"=%
echo.
if "%src%"=="" echo   Nothing given, stopping. & goto :done
echo   Writing it into your Neo Dimension save folder, backing up what is there.
echo   Close the game first.
echo.
%PY% fantasian.py to-steam "%src%" --auto-template --install
goto :done

:show
echo   Press enter to read the Neo Dimension save on this PC, or drag a save in.
echo.
set "dropped="
set /p dropped=  Save: 
set dropped=%dropped:"=%
echo.
if "%dropped%"=="" (%PY% fantasian.py slots) else (%PY% fantasian.py slots "%dropped%")
goto :done

:done
echo.
pause
