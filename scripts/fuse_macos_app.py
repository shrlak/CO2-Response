#!/usr/bin/env python3
"""Merge an Apple-silicon and an Intel build of the same app into one universal app.

    python scripts/fuse_macos_app.py ARM_APP X86_APP OUT_APP

Both are the PyInstaller output (CO2-Response.app) built on a runner of each kind. The
result is a copy of the Apple-silicon app in which every Mach-O file that exists in both
(the program, Python, Qt, numpy ...) is replaced by the ``lipo`` merge of the two, so it
starts natively on either kind of Mac. Every other file (scripts, data, fonts, the
symbolic links of the Qt frameworks) is taken from the Apple-silicon app as it is.
Run ``codesign --force --deep --sign -`` on the result afterwards: merging changes the
files, which invalidates the signatures PyInstaller put on them.

Also works on single files:  fuse_macos_app.py ARM_FILE X86_FILE OUT_FILE
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# the first four bytes of a Mach-O file (thin, either byte order, 32 or 64 bit) or a fat file
MAGIC = {bytes.fromhex(h) for h in ("feedface", "feedfacf", "cefaedfe", "cffaedfe", "cafebabe", "bebafeca")}


def is_macho(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with open(path, "rb") as f:
            return f.read(4) in MAGIC
    except OSError:
        return False


def archs(path: Path) -> set[str]:
    out = subprocess.run(["lipo", "-archs", str(path)], capture_output=True, text=True)
    return set(out.stdout.split()) if out.returncode == 0 else set()


def fuse_file(arm: Path, x86: Path, out: Path) -> bool:
    """``out`` = the two merged. False if they cannot be (kept as the Apple-silicon file)."""
    have = archs(arm) | archs(x86)
    if {"arm64", "x86_64"} <= have and archs(arm) == archs(x86):
        return False  # both already hold both: a copy of one will do
    tmp = out.with_name(out.name + ".fusing")
    run = subprocess.run(["lipo", "-create", str(arm), str(x86), "-output", str(tmp)], capture_output=True, text=True)
    if run.returncode != 0:
        print(f"  kept the Apple-silicon file for {out.name}: {run.stderr.strip()}", file=sys.stderr)
        return False
    shutil.copymode(arm, tmp)
    os.replace(tmp, out)
    return True


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        print(__doc__)
        return 2
    arm, x86, out = (Path(a) for a in argv[1:])
    if arm.is_file():
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(arm, out)
        fuse_file(arm, x86, out)
        print(f"{out}: {' '.join(sorted(archs(out)))}")
        return 0
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(arm, out, symlinks=True)  # the links of the Qt frameworks stay links
    fused = alone = 0
    for root, _dirs, files in os.walk(out):
        for name in files:
            f = Path(root) / name
            if not is_macho(f):
                continue
            other = x86 / f.relative_to(out)
            if other.is_file() and is_macho(other) and not other.is_symlink():
                fused += fuse_file(f, other, f)
            else:
                alone += 1
                print(f"  only in the Apple-silicon build: {f.relative_to(out)}", file=sys.stderr)
    print(f"{out}: {fused} files merged, {alone} only in the Apple-silicon build")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
