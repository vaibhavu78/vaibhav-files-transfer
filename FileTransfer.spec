# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for FileTransfer.exe
-------------------------------------
Build with:
    pip install pyinstaller
    pyinstaller FileTransfer.spec

The launcher.py is the entry point. Templates are bundled as data files so
render_template() works inside the frozen EXE. Runtime data (received_files,
qr_cache, config) is written NEXT TO the EXE (handled in app.py via EXE_DIR).
"""

import os

block_cipher = None

a = Analysis(
    ['SOURCE/launcher.py'],
    pathex=['SOURCE'],
    binaries=[],
    datas=[
        ('SOURCE/templates', 'templates'),
    ],
    hiddenimports=[
        'flask_socketio',
        'engineio',
        'socketio',
        'eventlet',
        'eventlet.hubs',
        'eventlet.hubs.hub',
        'qrcode',
        'PIL',
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='FileTransfer',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # console mode keeps stdout/stderr alive (avoids NoneType.flush)
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
