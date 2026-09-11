# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 스펙 — orcad2kicad-cli.exe(콘솔, CLI/MCP) 만 만든다.

GUI(tkinter) 빌드를 건너뛰어 orcad2kicad.spec 보다 빠르다. 테스트(tests/test_packaging.py)와
`packaging/build_exe.cmd --cli-only` 에서 쓴다.

빌드: python -m PyInstaller --noconfirm --clean --distpath dist --workpath build\\pyinstaller packaging\\orcad2kicad-cli.spec
"""
import os

SPEC_DIR = os.path.dirname(os.path.abspath(SPEC))
REPO_ROOT = os.path.dirname(SPEC_DIR)
SRC_DIR = os.path.join(REPO_ROOT, 'src')

_O2K_SUBMODULES = [
    '__main__', 'agents', 'branding', 'cli', 'edif_reader', 'explain', 'geometry', 'gui',
    'kicad_board', 'kicad_netlist', 'kicad_portable', 'kicad_sch_reader',
    'kicad_writer', 'mcp_server', 'model',
    'pads_netlist', 'pipeline', 'sexp', 'verify',
]
HIDDEN_IMPORTS = ['orcad2kicad'] + [f'orcad2kicad.{m}' for m in _O2K_SUBMODULES] + [
    'tkinter', 'tkinter.ttk', 'tkinter.filedialog', 'tkinter.messagebox', 'tkinter.scrolledtext',
]

EXCLUDES = ['numpy', 'pandas', 'matplotlib', 'PyQt5', 'PySide2', 'scipy']

block_cipher = None
# exe 아이콘 + 창 제목줄 아이콘(패키지 데이터로 같이 넣는다). 생성: python packaging/make_icon.py
ICON_PATH = os.path.join(SRC_DIR, 'orcad2kicad', 'assets', 'orcad2kicad.ico')
ICON_DATAS = [(ICON_PATH, os.path.join('orcad2kicad', 'assets'))] if os.path.isfile(ICON_PATH) else []
ICON_ARG = ICON_PATH if os.path.isfile(ICON_PATH) else None

a_cli = Analysis(
    [os.path.join(SPEC_DIR, 'entry_cli.py')],
    pathex=[SRC_DIR],
    binaries=[],
    datas=ICON_DATAS,
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    cipher=block_cipher,
)
pyz_cli = PYZ(a_cli.pure, a_cli.zipped_data, cipher=block_cipher)
exe_cli = EXE(
    pyz_cli,
    a_cli.scripts,
    a_cli.binaries,
    a_cli.datas,
    [],
    name='orcad2kicad-cli',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=ICON_ARG,
    codesign_identity=None,
    entitlements_file=None,
)
