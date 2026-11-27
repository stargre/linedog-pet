@echo off
rem LineDogPet launcher - conda env "linedog" (Python 3.12 + PyQt6)
setlocal
set "PY=%USERPROFILE%\.conda\envs\linedog\pythonw.exe"
set "APP=%~dp0LineDogPetv2.8.3\main.py"
if not exist "%PY%" goto :nopy
if not exist "%APP%" goto :noapp
start "" "%PY%" "%APP%"
exit /b 0
:nopy
echo [ERROR] interpreter not found: %PY%
echo Create it first: conda create -n linedog python=3.12 ^&^& pip install PyQt6 requests
pause
exit /b 1
:noapp
echo [ERROR] entry not found: %APP%
pause
exit /b 1
