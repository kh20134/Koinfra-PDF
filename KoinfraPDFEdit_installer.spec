# -*- mode: python ; coding: utf-8 -*-
import os
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
    excludes=['tkinter', 'numpy', 'PIL._avif', 'PIL.AvifImagePlugin', 'PIL._webp', 'PIL.WebPImagePlugin'],
    noarchive=False,
    optimize=0,
)
# Not used by a QtWidgets app (no OpenGL/QtQuick): software OpenGL + ANGLE, and the WebGL/minimal/offscreen
# platform plugins that pull in QtQuick/Qml/Network. Saves ~40 MB on disk.
_UNUSED_QT = ('opengl32sw.dll', 'd3dcompiler_47.dll', 'libglesv2.dll', 'libegl.dll', 'qt5quick.dll', 'qt5qml.dll',
              'qt5qmlmodels.dll', 'qt5websockets.dll', 'qt5network.dll', 'qt5dbus.dll', 'qwebgl.dll', 'qminimal.dll', 'qoffscreen.dll')
a.binaries = [b for b in a.binaries if os.path.basename(b[0]).lower() not in _UNUSED_QT]
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

