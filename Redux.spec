# -*- mode: python ; coding: utf-8 -*-
# The game database ships its catalogs (small) and only Sega CD's art; the other
# systems' art is downloaded from the GitHub release by System -> Database.
import os
def database_datas():
    datas=[]
    for root,dirs,files in os.walk('redux/database'):
        parts=root.replace(os.sep,'/').split('/')
        if 'art' in parts[2:] and parts[2]!='sega_cd':
            dirs[:]=[];continue
        for name in files:
            source=os.path.join(root,name)
            datas.append((source,os.path.dirname(source).replace('redux/database','database',1)))
    return datas



a = Analysis(
    ['run_app.py'],
    pathex=['.'],
    binaries=[],
    datas=[('redux/assets', 'assets')] + database_datas(),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='Redux',
    icon='redux/assets/redux.ico',
    manifest='redux/assets/windows.manifest',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='Redux',
)
