# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


ROOT = Path(SPECPATH)

block_cipher = None

datas = [
    (str(ROOT / "assets" / "icons" / "app.png"), "assets/icons"),
    (str(ROOT / "assets" / "icons" / "app.ico"), "assets/icons"),
    (
        str(ROOT / "app" / "data" / "database" / "migrations" / "v001_initial.sql"),
        "app/data/database/migrations",
    ),
    (
        str(ROOT / "app" / "data" / "database" / "migrations" / "v002_app_config.sql"),
        "app/data/database/migrations",
    ),
    (
        str(ROOT / "app" / "data" / "database" / "migrations" / "v003_core_entities.sql"),
        "app/data/database/migrations",
    ),
]

excludes = [
    "pytest",
    "pytest_qt",
    "unittest",
    "doctest",
    "pdb",
    "tkinter",
    "IPython",
    "jupyter",
    "notebook",
    "numpy",
    "pandas",
    "lxml",
    "matplotlib",
    "scipy",
    "psutil",
    "qtpy",
    "traitlets",
    "pygments",
    "yaml",
    "click",
    "setuptools",
    "wheel",
    "pip",
]

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=1,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="GeradorImagensProduto",
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
    icon=str(ROOT / "assets" / "icons" / "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="GeradorImagensProduto",
)
