# -*- mode: python ; coding: utf-8 -*-
# Installer build (one-folder = fast start). pyinstaller --distpath dist_installer --workpath build_installer KoinfraPDFEdit_installer.spec
a = Analysis(
    ['Koinfra.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.svg', '.'), ('icon2.ico', '.'), ('icon.ico', '.'), ('koinfra_pdf_doc.ico', '.'), ('icons', 'icons')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'numpy'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='KoinfraPDFEdit',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['icon2.ico'],
    version='version_info.txt',
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, upx_exclude=[], name='KoinfraPDFEdit')

