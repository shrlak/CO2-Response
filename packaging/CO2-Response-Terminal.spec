# PyInstaller recipe for the standalone TERMINAL monitor: one program file, no Python
# and no Qt needed. It runs in any terminal (Windows Terminal, macOS Terminal, the VS
# Code terminal, over SSH).
#
#     pip install -e . pyinstaller
#     pyinstaller --noconfirm packaging/CO2-Response-Terminal.spec
#
# The result is dist/co2response-terminal (co2response-terminal.exe on Windows).
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
PKG = ROOT / "co2response"

a = Analysis(
    [str(ROOT / "packaging" / "co2response_terminal.py")],
    pathex=[str(ROOT)],
    datas=[(str(PKG / "config"), "co2response/config"), (str(ROOT / "sample-data"), "sample-data")] + collect_data_files("textual"),
    # Textual and rich import some of their modules lazily, by name
    hiddenimports=collect_submodules("textual.widgets") + collect_submodules("rich._unicode_data") + ["co2response.tui.app", "co2response.headless"],
    # the desktop window and the browser front end are not part of the terminal program
    excludes=["co2response.ui", "co2response.web", "PySide6", "pyqtgraph", "tkinter", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="co2response-terminal", console=True)
