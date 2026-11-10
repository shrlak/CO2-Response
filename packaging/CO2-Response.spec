# PyInstaller recipe for the standalone desktop app (no Python needed on the computer).
#
#     pip install -r requirements.txt pyinstaller
#     pyinstaller --noconfirm packaging/CO2-Response.spec
#
# The result is dist/CO2-Response/ (a folder with the program; on macOS also
# dist/CO2-Response.app). .github/workflows/release.yml builds it for Windows, macOS
# and Linux and checks that the built program starts.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
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
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CO2-Response", console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="CO2-Response")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="CO2-Response.app", bundle_identifier="edu.cmu.bme.co2response")
