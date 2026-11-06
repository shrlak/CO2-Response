"""Build the GitHub Pages site: the monitor running in the browser under Pyodide.

    python scripts/build_site.py [output folder, default dist/site]

The site is static: index.html (loads Pyodide and starts co2response.web.app),
styles.css, the fonts, the sample session, and co2response.zip (the Python package
without the desktop window, which needs Qt). .github/workflows/deploy.yml runs this
and publishes the folder.
"""

from __future__ import annotations

import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {"__pycache__", "ui", "fonts"}  # ui/ is the Qt desktop window; fonts are served as files


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for name in ("index.html", "styles.css"):
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
    return out


if __name__ == "__main__":
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "site"
    build(dest)
    size = sum(p.stat().st_size for p in dest.rglob("*") if p.is_file())
    print(f"built {dest} ({size / 1e6:.1f} MB)")
