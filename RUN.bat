@echo off
chcp 65001 >nul 2>&1
title Vaibhav Files Transfer - ONE CLICK (Git + Install + Run)
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo.
echo ============================================================
echo        VAIBHAV FILES TRANSFER - ONE CLICK SCRIPT
echo     (Git upload + Install + Run + QR - sab automatic)
echo ============================================================
echo.
echo  Yeh script sab kuch APNE AAP karega:
echo    1. Python check/install (nahi hoga to automatic)
echo    2. Git check/install (nahi hoga to automatic)
echo    3. Libraries install (pehli baar only)
echo    4. GitHub pe upload (agar repo URL diya)
echo    5. Server + Public Tunnel + QR (automatic)
echo    6. Admin panel browser me khulega (password: radha)
echo.
echo  ============================================================
echo.

REM ---------- STEP 1: Check / install Python ----------
echo [1/6] Python check ho raha hai...
where python >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('python --version 2^>^&1') do set PYVER=%%v
    echo       OK - !PYVER!
    set "PYCMD=python"
    goto :gotpython
)
where py >nul 2>&1
if %errorlevel%==0 (
    echo       OK - py launcher mil gaya
    set "PYCMD=py"
    set USEPY=1
    goto :gotpython
)

echo       [!] Python nahi mila. Automatic install kar raha hai...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%TEMP%\pyinst.exe' -UseBasicParsing } catch { exit 1 }"
if not exist "%TEMP%\pyinst.exe" (
    echo       [X] Python download nahi ho paya. Manually install karo:
    echo           https://www.python.org/downloads/  (PATH tick zaroor karo)
    pause
    exit /b 1
)
"%TEMP%\pyinst.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1
del "%TEMP%\pyinst.exe" >nul 2>&1
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
set "PYCMD=python"
echo       [OK] Python install ho gaya.
set REFRESHED=1

:gotpython
if defined REFRESHED (
    for /f "tokens=*" %%v in ('!PYCMD! --version 2^>^&1') do set PYVER=%%v
)
echo       Using: !PYCMD! !PYVER!
echo.

REM ---------- STEP 2: Check / install Git ----------
echo [2/6] Git check ho raha hai...
where git >nul 2>&1
if %errorlevel%==0 (
    for /f "tokens=*" %%v in ('git --version 2^>^&1') do set GITVER=%%v
    echo       OK - !GITVER!
    goto :gotgit
)

echo       [!] Git nahi mila. Automatic install kar raha hai...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -Uri 'https://github.com/git-for-windows/git/releases/download/v2.46.0.windows.1/Git-2.46.0-64-bit.exe' -OutFile '%TEMP%\gitinst.exe' -UseBasicParsing } catch { exit 1 }"
if not exist "%TEMP%\gitinst.exe" (
    echo       [!] Git download nahi ho paya. Bina git ke bhi chalega.
    echo           Local mode me server chal jayega, GitHub upload skip hoga.
    goto :gotgit
)
"%TEMP%\gitinst.exe" /quiet /norestart
del "%TEMP%\gitinst.exe" >nul 2>&1
set "PATH=%ProgramFiles%\Git\cmd;%ProgramFiles%\Git\bin;%PATH%"
echo       [OK] Git install ho gaya.

:gotgit
echo.

REM ---------- STEP 3: Install dependencies ----------
echo [3/6] Libraries install ho rahi hai (pehli baar thodi der lagti hai)...
if exist "SOURCE\.installed" (
    echo       [OK] Pehle se install hain, skip.
    goto :rundeps_done
)
!PYCMD! -m pip install --upgrade pip >nul 2>&1
echo       pip install chal raha hai... ruko...
!PYCMD! -m pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo       [!] pip install me dikkat. Dobara koshish (user mode)...
    !PYCMD! -m pip install -r requirements.txt --user
)
if %errorlevel% neq 0 (
    echo       [X] Libraries install nahi hui. Error check karo.
    echo           Problem ho to manually chalao: pip install flask flask-socketio eventlet qrcode pillow requests
    pause
    exit /b 1
)
if exist "SOURCE\.installed" del "SOURCE\.installed" >nul 2>&1
echo done> "SOURCE\.installed"
:rundeps_done
echo.

REM ---------- STEP 4: GitHub upload (optional) ----------
echo [4/6] GitHub upload (optional - skip kar sakte ho)...
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo       Git nahi hai - GitHub upload skip. Local mode chalega.
    goto :skipgit
)

if not exist ".git" (
    git init >nul 2>&1
    git branch -M main >nul 2>&1
)

REM Check if remote already set
git remote get-url origin >nul 2>&1
if %errorlevel%==0 (
    echo       GitHub remote pehle se set hai.
    set DO_GIT=1
    goto :dogit
)

echo.
echo  -------------------------------------------------------
echo  GitHub pe upload karna hai? (Render deploy ke liye)
echo  -------------------------------------------------------
echo  Apne GitHub repo ka URL daalo (jaise naya repo banake):
echo    https://github.com/USERNAME/vaibhav-files-transfer.git
echo.
echo  Skip karne ke liye ENTER dabao (local mode chalega).
echo  -------------------------------------------------------
set /p REPO_URL="  GitHub repo URL (ya ENTER for skip): "

if "!REPO_URL!"=="" (
    echo       GitHub upload skip. Local mode chalega.
    goto :skipgit
)

git remote add origin "!REPO_URL!"
set DO_GIT=1

:dogit
if defined DO_GIT (
    echo       Files add + commit + push ho rahi hai...
    git add -A >nul 2>&1
    git commit -m "Vaibhav Files Transfer - auto deploy" >nul 2>&1
    git push -u origin main 2>&1
    if %errorlevel% neq 0 (
        echo       [!] Push me dikkat. Login chahiye hoga ya repo URL galat hai.
        echo           Manual try karo: git push -u origin main
    ) else (
        echo       [OK] GitHub pe upload ho gaya!
    )
)

:skipgit
echo.

REM ---------- STEP 5 & 6: Run server + tunnel + QR + browser ----------
echo [5/6] Server + Public Tunnel + QR banaya ja raha hai...
echo       (Pehli baar cloudflared download hoga - 10-20 sec)
echo.
echo ============================================================
echo  Admin Panel Password: radha
echo  (QR scan karne wale ko password nahi chahiye)
echo ============================================================
echo.
echo [6/6] Server start ho raha hai... browser khulega...
echo.
!PYCMD! SOURCE\launcher.py
echo.
echo ============================================================
echo  Server band ho gaya. Koi bhi key dabao band karne ke liye.
pause
