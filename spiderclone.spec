# -*- mode: python ; coding: utf-8 -*-
# SpiderClone — spec PyInstaller (onefile)
# by xyrek from ar3s — 2026-09-22
"""
SpiderClone — specification PyInstaller (onefile).

Build (depuis la racine du projet) :
    python -m PyInstaller spiderclone.spec --noconfirm

Resultat : dist/spiderclone.exe  (executable autonome, un seul fichier)

Notes :
  - L'entree est spiderclone.py ; les modules cs_*.py sont decouverts
    automatiquement depuis le repertoire courant, mais on les liste aussi en
    hiddenimports pour rester robuste.
  - Les dependances optionnelles (playwright pour --render, colorama pour
    les couleurs, pytest/tests) sont exclues : l'outil fonctionne sans elles.
"""

HIDDEN = [
    "cs_config", "cs_utils", "cs_fetch",
    "cs_render", "cs_rewrite", "cs_crawl", "cs_ui",
]

EXCLUDES = [
    "playwright",
    "tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6",
    "matplotlib", "numpy", "pandas", "scipy",
    "pytest", "tests",
]


a = Analysis(
    ['spiderclone.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='spiderclone',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
