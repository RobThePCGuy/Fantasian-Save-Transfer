@echo off
REM Double-click me on Windows. Nothing to install.
setlocal
cd /d "%~dp0"

echo.
echo   FANTASIAN save tool
echo   ----------------------------------------
echo.

if not exist "%~dp0fantasian.py" goto :not_extracted

REM Find a Python that actually RUNS and is 3.8 or newer, and remember the real
REM python.exe it reports. Finding the name is not enough: Windows ships a python
REM that only points at the Microsoft Store, and tools like pyenv-win put python in
REM front as a batch file, which would end this script the first time it ran.
> "%TEMP%\fantasian-which.py" echo import sys; print(sys.executable if sys.version_info[:2] ^>= (3, 8) else "TOO-OLD")
set "PY="
set "OLDPY="
call :try py -3
if not defined PY call :try python
if not defined PY call :try python3
del "%TEMP%\fantasian-which.py" >nul 2>&1
if not defined PY goto :no_python

%PY% fantasian.py self-test > "%TEMP%\fantasian-selftest.txt" 2>&1
if errorlevel 1 goto :selftest_failed
del "%TEMP%\fantasian-selftest.txt" >nul 2>&1

set "choice="
echo   1  Edit my Neo Dimension save (money, items, experience)
echo   2  Bring an Apple Arcade save over from a Mac
echo   3  Show me what is in a save
echo   q  Quit
echo.
set /p "choice=  Which one? "
echo.
if /i "%choice%"=="1" goto :edit
if /i "%choice%"=="2" goto :import
if /i "%choice%"=="3" goto :show
if /i "%choice%"=="q" goto :nothing
if "%choice%"=="" goto :nothing
echo   Not one of the choices.
goto :done

:edit
echo   Press enter to edit the Neo Dimension save on this PC, or drag your
echo   root.json in here first.
echo.
set "target="
set /p "target=  Save: "
if defined target set "target=%target:"=%"
echo.
echo   a  Money, and analyze every enemy you have met
echo   b  All of that, plus every weapon, armour and accessory
echo   c  Everything, including Part 2 items. Only once you have skill points.
echo.
set "how="
set /p "how=  Which one? "
echo.
set "FLAGS="
if /i "%how%"=="a" set "FLAGS=--add-money --analyze-all --add-box-keys --add-recovery-items --add-battle-items"
if /i "%how%"=="b" set "FLAGS=--add-money --analyze-all --add-box-keys --add-recovery-items --add-battle-items --add-accessories --insert-all-weapons --insert-all-armors --insert-all-accessories"
if /i "%how%"=="c" set "FLAGS=--add-money --analyze-all --add-box-keys --add-recovery-items --add-battle-items --add-accessories --insert-all-weapons --insert-all-armors --insert-all-accessories --insert-or-add-sp-capsules --insert-all-gate-items --insert-all-upgrade-materials"
if not defined FLAGS goto :bad_choice
if not defined target goto :edit_here
%PY% fantasian.py edit "%target%" %FLAGS%
goto :done
:edit_here
%PY% fantasian.py edit %FLAGS%
goto :done

:import
echo   Drag in the FANTASIAN folder or zip copied off the Mac, then press enter.
echo   Copy the whole folder: the save lives in the -wal file beside the .sqlite.
echo.
set "src="
set /p "src=  Save: "
if defined src set "src=%src:"=%"
echo.
if not defined src goto :nothing_given
echo   Writing it into your Neo Dimension save folder, backing up what is there.
echo   Close the game first.
echo.
%PY% fantasian.py to-steam "%src%" --auto-template --install
goto :done

:show
echo   Press enter to read the Neo Dimension save on this PC, or drag a save in.
echo.
set "dropped="
set /p "dropped=  Save: "
if defined dropped set "dropped=%dropped:"=%"
echo.
if not defined dropped goto :show_here
%PY% fantasian.py slots "%dropped%"
goto :done
:show_here
%PY% fantasian.py slots
goto :done

:try
REM for /f runs the candidate in its own cmd, so a batch-file python returns here.
REM Anything it prints that is not a real file, like the Store stub's advice, is ignored.
set "FOUND="
for /f "usebackq delims=" %%i in (`%* "%TEMP%\fantasian-which.py" 2^>nul`) do set "FOUND=%%i"
if not defined FOUND exit /b
if "%FOUND%"=="TOO-OLD" goto :try_old
if not exist "%FOUND%" exit /b
set PY="%FOUND%"
exit /b
:try_old
set "OLDPY=%*"
exit /b

:not_extracted
echo   This needs the rest of its folder, and it cannot see it.
echo.
echo   That almost always means it was opened from inside the zip. Windows only
echo   takes out the one file you double-click, so the tool itself is not here.
echo.
echo   Right-click the zip, pick Extract All, open the folder that makes, and
echo   double-click this file from in there.
echo.
echo   Running from: %~dp0
goto :done

:no_python
if defined OLDPY goto :old_python
echo   Python 3 is not installed on this PC.
echo.
echo   If you are sure it is: Windows ships a "python" shortcut that only opens
echo   the Microsoft Store, and that is all that was found here.
goto :get_python
:old_python
echo   Python is installed, but it is older than 3.8, which this needs.
:get_python
echo.
echo   Get Python from https://www.python.org/downloads/
echo   On the first screen of the installer, tick "Add python.exe to PATH".
echo   Then double-click this again.
goto :done

:selftest_failed
echo   Python ran, but the tool's own check did not pass. This is what it said:
echo.
type "%TEMP%\fantasian-selftest.txt"
echo.
echo   Python used: %PY%
goto :done

:bad_choice
echo   Not one of the choices.
goto :done

:nothing_given
echo   Nothing given, stopping.
goto :done

:nothing
echo   Nothing done.

:done
echo.
pause
