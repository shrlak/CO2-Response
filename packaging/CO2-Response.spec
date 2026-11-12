# PyInstaller recipe for the standalone desktop app (no Python needed on the computer).
#
#     pip install -r requirements.txt pyinstaller
#     pyinstaller --noconfirm packaging/CO2-Response.spec
#
# The result is dist/CO2-Response.exe on Windows (one file), dist/CO2-Response/ (a folder
# with the program) on Linux, and dist/CO2-Response.app on macOS.
# .github/workflows/release.yml builds it for Windows, macOS and Linux and checks that
# the built program starts.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
VERSION = __import__("re").search(r'__version__ = "([^"]+)"', (ROOT / "co2response" / "__init__.py").read_text()).group(1)
PKG = ROOT / "co2response"

a = Analysis(
    [str(ROOT / "packaging" / "co2response_app.py")],
    pathex=[str(ROOT)],
    # files the app reads at run time: the default calibration and the fonts
    datas=[(str(PKG / "config"), "co2response/config"), (str(PKG / "fonts"), "co2response/fonts")],
    # the browser front end needs Pyodide's js module; it is not part of the desktop app
    excludes=["co2response.web", "tkinter", "pytest"],
)
pyz = PYZ(a.pure)
if sys.platform == "win32":
    # Windows: ONE file, CO2-Response.exe, that needs no installation (it unpacks itself to a
    # temporary folder when it starts, which takes a few seconds)
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="CO2-Response", console=False, upx=False)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CO2-Response", console=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="CO2-Response")
    if sys.platform == "darwin":
        # built once on an Apple-silicon and once on an Intel Mac, then merged into one
        # universal app (scripts/fuse_macos_app.py) and packed into a .dmg
        app = BUNDLE(coll, name="CO2-Response.app", bundle_identifier="edu.cmu.bme.co2response", version=VERSION)
