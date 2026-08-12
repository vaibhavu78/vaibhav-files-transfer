@echo off
chcp 65001 >nul 2>&1
title Vaibhav Files Transfer - One Click Run
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo          VAIBHAV FILES TRANSFER - ONE CLICK RUN
echo        (QR scan karo, kisi bhi network se file bhejo)
echo ============================================================
echo.
echo  Yeh window sab kuch APNE AAP karega:
echo    1. Python check/install (nahi hoga to automatic)
echo    2. Libraries install (pehli baar only)
echo    3. Server + Public Tunnel + QR (automatic)
echo    4. Admin panel browser me khulega (password: radha)
echo.
echo  Bas yeh window open rakho. Band karna ho to X dabao.
echo ============================================================
echo.

REM ---------- STEP 1: Check / install Python ----------
:checkpython
echo [1/4] Python check ho raha hai...
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo       OK - !PYVER!
    goto :gotpython
)
where py >nul 2>&1
if %errorlevel%==0 (
    echo       OK - py launcher mil gaya
    set USEPY=1
    goto :gotpython
)

echo       [!] Python nahi mila. Automatic install kar raha hai...
echo       (10-15 second lagenge, internet chahiye)
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%TEMP%\pyinst.exe' -UseBasicParsing } catch { Write-Host 'Download failed:' $_; exit 1 }"
if not exist "%TEMP%\pyinst.exe" (
    echo       [X] Python download nahi ho paya.
    echo           Manually install karo: https://www.python.org/downloads/
    echo           IMPORTANT: Install me "Add Python to PATH" tick zaroor karo!
    echo.
    pause
    exit /b 1
)
echo       Python install ho raha hai (silent, PATH add ho jayega)...
"%TEMP%\pyinst.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
if %errorlevel% neq 0 (
    echo       [X] Python install me dikkat. Manually karo:
    echo           https://www.python.org/downloads/  (PATH tick zaroor karo)
    pause
    exit /b 1
)
del "%TEMP%\pyinst.exe" >nul 2>&1
REM refresh PATH for this session
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
echo       [OK] Python install ho gaya.
set REFRESHED=1

:gotpython
REM pick the right launcher
set "PYCMD=python"
if defined USEPY set "PYCMD=py"

REM If we just installed, refresh env fully via a fresh call
if defined REFRESHED (
    for /f "tokens=*" %%v in ('!PYCMD! --version 2^>^&1') do set PYVER=%%v
)

echo       Using: !PYCMD! !PYVER!
echo.

REM ---------- STEP 2: Install dependencies (first time) ----------
echo [2/4] Libraries check/install ho rahi hai (pehli baar thodi der lagti hai)...
REM marker file so we don't reinstall every time
if exist "SOURCE\.installed" (
    echo       [OK] Pehle se install hain, skip.
    goto :rundeps_done
)
!PYCMD! -m pip install --upgrade pip >nul 2>&1
!PYCMD! -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo       [!] pip install me dikkat. Dobara koshish...
    !PYCMD! -m pip install -r requirements.txt --user
)
if exist "SOURCE\.installed" del "SOURCE\.installed" >nul 2>&1
echo done> "SOURCE\.installed"
:rundeps_done
echo.

REM ---------- STEP 3 & 4: Run launcher (server + tunnel + QR + browser) ----------
echo [3/4] Server + Public Tunnel + QR banaya ja raha hai...
echo       (Pehli baar cloudflared download hoga - 10-20 sec)
echo.
echo ============================================================
echo  Admin Panel Password: radha
echo  (QR scan karne wale ko password nahi chahiye)
echo ============================================================
!PYCMD! SOURCE\launcher.py
echo.
echo ============================================================
echo  Server band ho gaya. Window band karne ke liye koi bhi key dabao.
pause
