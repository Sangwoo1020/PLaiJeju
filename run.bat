@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem Read and write files as UTF-8 regardless of the Windows code page.
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

where python >nul 2>nul
if errorlevel 1 goto nopython
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) and sys.maxsize > 2**32 else 1)" >nul 2>nul
if errorlevel 1 goto wrongpython

if exist .venv\installed.txt goto start

echo First run: installing packages.
if exist .venv rmdir /s /q .venv
python -m venv .venv
if errorlevel 1 goto failed

if not exist wheels\*.whl goto online
echo Installing from the bundled wheels folder. No internet needed.
.venv\Scripts\python.exe -m pip install --no-index --find-links wheels -r requirements.txt
if not errorlevel 1 goto installed
echo Bundled install failed. Trying PyPI online instead.

:online
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed

:installed
echo installed> .venv\installed.txt

:start
if not exist .env copy .env.example .env >nul
echo Starting. Keep this window open. Close it to quit.
echo If the browser does not open, go to http://localhost:8765
start "" /b cmd /c "timeout /t 12 /nobreak >nul & start http://localhost:8765"
.venv\Scripts\python.exe -m uvicorn golmok.app:app --app-dir src --port 8765
if errorlevel 1 goto runfailed
goto :eof

:nopython
echo Python 3.12 is not installed or not on PATH. See README.md.
pause
exit /b 1

:wrongpython
echo Python 3.12 64-bit is required. The bundled packages in the wheels folder are built for it.
python --version
pause
exit /b 1

:failed
echo Installation failed. Delete the .venv folder and run again. See the troubleshooting section of README.md.
pause
exit /b 1

:runfailed
echo The program stopped with an error. If port 8765 is in use, close the other window and run again.
pause
exit /b 1
