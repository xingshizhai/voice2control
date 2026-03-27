@echo off
setlocal

set TARGET=%1
if "%TARGET%"=="" set TARGET=all

set ONEFILE=
if /I "%2"=="--onefile" set ONEFILE=--onefile

if /I "%TARGET%"=="all" goto build_all
if /I "%TARGET%"=="gui" goto build_gui
if /I "%TARGET%"=="cli" goto build_cli

echo Invalid target: %TARGET%
echo Usage: scripts\build.bat [all^|gui^|cli] [--onefile]
exit /b 1

:build_all
call :build_gui
if errorlevel 1 exit /b 1
call :build_cli
if errorlevel 1 exit /b 1
goto done

:build_gui
echo Building GUI package...
python -m PyInstaller --noconfirm --clean --name voice2control --hidden-import vc --hidden-import vc.__main__ --hidden-import dashscope --hidden-import dashscope.audio.asr --collect-submodules PySide6 --add-data "config.example.yaml;." %ONEFILE% --windowed main.py
if errorlevel 1 exit /b 1
exit /b 0

:build_cli
echo Building CLI package...
python -m PyInstaller --noconfirm --clean --name voice2control-cli --hidden-import vc --hidden-import vc.__main__ --hidden-import dashscope --hidden-import dashscope.audio.asr --collect-submodules PySide6 --add-data "config.example.yaml;." %ONEFILE% --console main.py
if errorlevel 1 exit /b 1
exit /b 0

:done
echo.
echo Build complete.
echo Artifacts:
echo   dist\voice2control
echo   dist\voice2control-cli
exit /b 0
