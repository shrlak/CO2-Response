"""Build the GitHub Pages site: the monitor running in the browser under Pyodide.

    python scripts/build_site.py [output folder, default dist/site] [--vendor-pyodide]

The site is static: index.html (loads Pyodide and starts co2response.web.app),
styles.css, sw.js (the service worker that keeps a copy for use without internet),
the fonts, the sample session, and co2response.zip (the Python package without the
desktop window, which needs Qt). .github/workflows/deploy.yml runs this and
publishes the folder.

By default the Python runtime (Pyodide) comes from the jsDelivr CDN, and the service
worker stores it in the browser on the first visit. ``--vendor-pyodide`` downloads it
into the site instead (about 13 MB more), for a server on a network with no internet
at all: nothing is then fetched from outside the site, even on the first visit.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", "ui", "fonts"}  # ui/ is the Qt desktop window; fonts are served as files


def build(out: Path, vendor_pyodide: bool = False) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("index.html", "styles.css", "sw.js"):
        shutil.copy(ROOT / "site" / name, out / name)
    (out / "fonts").mkdir()
    for f in (ROOT / "co2response" / "fonts").iterdir():
        if f.suffix in (".ttf", ".txt"):
            shutil.copy(f, out / "fonts" / f.name)
    (out / "sample-data").mkdir()
    shutil.copy(ROOT / "sample-data" / "synthetic_session.csv", out / "sample-data" / "synthetic_session.csv")
    pkg = ROOT / "co2response"
    with zipfile.ZipFile(out / "co2response.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob("*")):
            rel = p.relative_to(pkg)
            if p.is_dir() or SKIP_DIRS & set(rel.parts) or p.suffix == ".pyc":
                continue
            z.write(p, Path("co2response") / rel)
    (out / ".nojekyll").write_text("")
    if vendor_pyodide:
        vendor(out)
    return out


CDN = re.compile(r"https://cdn\.jsdelivr\.net/pyodide/v[\d.]+/full/")
#: what loadPyodide() fetches before any package
PYODIDE_CORE = ("pyodide.js", "pyodide.asm.wasm", "python_stdlib.zip", "pyodide-lock.json")
#: the runtime's glue code: its name depends on the Pyodide version, so whichever exists
PYODIDE_GLUE = ("pyodide.asm.mjs", "pyodide.asm.js")
PYODIDE_PACKAGES = ("numpy",)


def vendor(out: Path, fetch=None) -> list[str]:
    """Copy the Python runtime into ``out/pyodide`` and point index.html at it.
    ``fetch(url) -> bytes`` can be replaced (the tests do)."""
    fetch = fetch or (lambda url: urllib.request.urlopen(url, timeout=120).read())  # noqa: S310
    index = out / "index.html"
    html = index.read_text(encoding="utf-8")
    m = CDN.search(html)
    if not m:
        raise SystemExit("index.html does not load Pyodide from the CDN; nothing to vendor")
    base = m.group(0)
    dest = out / "pyodide"
    dest.mkdir(exist_ok=True)
    names = list(PYODIDE_CORE)
    for name in PYODIDE_CORE:
        (dest / name).write_bytes(fetch(base + name))
    for name in PYODIDE_GLUE:
        try:
            (dest / name).write_bytes(fetch(base + name))
            names.append(name)
        except OSError:  # 404: this version uses the other name
            continue
    if not set(PYODIDE_GLUE) & set(names):
        raise SystemExit(f"could not download the Pyodide runtime from {base}")
    lock = json.loads((dest / "pyodide-lock.json").read_text(encoding="utf-8"))["packages"]
    todo, seen = list(PYODIDE_PACKAGES), set()
    while todo:  # each package and what it depends on
        pkg = todo.pop()
        if pkg in seen:
            continue
        seen.add(pkg)
        names.append(lock[pkg]["file_name"])
        (dest / lock[pkg]["file_name"]).write_bytes(fetch(base + lock[pkg]["file_name"]))
        todo += lock[pkg].get("depends", [])
    index.write_text(html.replace(base, "pyodide/"), encoding="utf-8")
    return names


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dest = Path(args[0]) if args else ROOT / "dist" / "site"
    build(dest, vendor_pyodide="--vendor-pyodide" in sys.argv)
    size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    print(f"built {dest} ({size / 1e6:.1f} MB)")
