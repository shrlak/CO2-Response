"""Self-update for the standalone desktop app (the PyInstaller build).

An installed standalone app cannot ``pip install``, so it updates itself from the GitHub
Releases of this project, the same files that are offered for download:

    Windows   CO2-Response.exe            one file
    macOS     CO2-Response.dmg            holds CO2-Response.app (a universal app)
    Linux     CO2-Response-Linux.tar.gz   holds the folder CO2-Response/

Two steps, kept apart so that an update can never change the program in the middle of
a study:

1. **Check and download** (in the background, while the app runs, at most once a day):
   ask GitHub for the latest release; if it is newer than this version, download the
   file for this computer, check its size and SHA-256 against what GitHub lists, and keep
   it in the app's data folder (``updates/``). Nothing about the subject or the study is
   sent; the request is a plain GET of the release list.
2. **Install** (only when the app *starts*, before any window or recording exists): swap
   the downloaded version into place, keep the previous one beside it (``.old``) so it
   can be put back, and start the new one. If anything goes wrong the previous version
   stays in place and runs, and the reason is kept for the next check.

The pip/source installs are not touched: they update with ``co2response --update``. The
terminal program (``co2response-terminal``) is a separate download and is not updated here.

**Not tried on a real Windows or macOS computer.** The logic is tested here (Linux) with
fake releases, fake disk images and files swapped in a temporary folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

from . import __version__
from .paths import data_dir

REPO = "shrlak/CO2-Response"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
#: the release file for each kind of computer (see .github/workflows/release.yml)
ASSETS = {"win32": "CO2-Response.exe", "darwin": "CO2-Response.dmg", "linux": "CO2-Response-Linux.tar.gz"}
#: ask GitHub at most this often (s)
CHECK_EVERY_S = 24 * 3600
USER_AGENT = "CO2-Response-updater"


class UpdateError(Exception):
    """An update step failed; the message says why, in words for the operator."""


class Stream(Protocol):
    def read(self, n: int = -1) -> bytes: ...


GetJson = Callable[[str], dict]
OpenUrl = Callable[[str], Stream]


def is_frozen() -> bool:
    """True in the standalone (PyInstaller) build."""
    return bool(getattr(sys, "frozen", False))


def updates_dir() -> Path:
    return data_dir() / "updates"


# ------------------------------------------------------------------ versions and releases


def parse_version(text: str) -> tuple[int, ...] | None:
    """``"v0.7.1"`` -> ``(0, 7, 1)``; None when it is not a plain dotted number."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+){0,3})", text.strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else None


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    if a is None or b is None:
        return False
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


@dataclass
class ReleaseInfo:
    tag: str
    asset: str
    url: str
    size: int = 0
    #: lower-case hex SHA-256 as GitHub lists it for the asset ("" if it does not)
    sha256: str = ""


def release_from_json(data: dict, platform: str = sys.platform) -> ReleaseInfo | None:
    """The file for ``platform`` in a GitHub release (the API's JSON); None for a draft or
    pre-release, or one that has no file for this kind of computer."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    want = ASSETS.get("linux" if platform.startswith("linux") else platform)
    for a in data.get("assets") or []:
        if isinstance(a, dict) and a.get("name") == want and isinstance(a.get("browser_download_url"), str):
            digest = a.get("digest") if isinstance(a.get("digest"), str) else ""
            sha = digest.split(":", 1)[1].lower() if digest.lower().startswith("sha256:") else ""
            size = a.get("size") if isinstance(a.get("size"), int) else 0
            return ReleaseInfo(str(data.get("tag_name", "")), want, a["browser_download_url"], size, sha)
    return None


def _ssl_context():
    import ssl

    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def default_get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=15, context=_ssl_context()) as r:
        return json.loads(r.read().decode("utf-8"))


def default_open_url(url: str) -> Stream:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/octet-stream"})
    return urllib.request.urlopen(req, timeout=30, context=_ssl_context())


# ------------------------------------------------------------------ state


def _read_state(folder: Path) -> dict:
    try:
        v = json.loads((folder / "state.json").read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_state(folder: Path, state: dict) -> None:
    try:
        folder.mkdir(parents=True, exist_ok=True)
        tmp = folder / "state.json.tmp"
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        os.replace(tmp, folder / "state.json")
    except OSError:
        pass  # the update then simply runs again at the next check


def read_state(folder: Path | None = None) -> dict:
    """What the updater last did: ``last_check``, ``staged``, ``last_update``, ``last_error``."""
    return _read_state(folder or updates_dir())


def staged_update(folder: Path | None = None) -> dict | None:
    """The downloaded update waiting to be installed ({tag, file, sha256}), if its file is there."""
    folder = folder or updates_dir()
    s = _read_state(folder).get("staged")
    return s if isinstance(s, dict) and Path(str(s.get("file", ""))).is_file() else None


# ------------------------------------------------------------------ check and download


def download(info: ReleaseInfo, dest: Path, open_url: OpenUrl = default_open_url, progress: Callable[[int, int], None] | None = None) -> Path:
    """Download the release file to ``dest`` and check it (size, and SHA-256 when GitHub
    lists one). Raises UpdateError, leaving nothing at ``dest``, if it is not what was listed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    done = 0
    try:
        with open_url(info.url) as src, open(part, "wb") as out:  # type: ignore[attr-defined]
            while chunk := src.read(1 << 20):
                out.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, info.size)
        if info.size and done != info.size:
            raise UpdateError(f"The download is {done} bytes, not the {info.size} the release lists.")
        if info.sha256 and h.hexdigest() != info.sha256:
            raise UpdateError("The download does not match the checksum the release lists, so it was not used.")
        os.replace(part, dest)
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except OSError as e:
        part.unlink(missing_ok=True)
        raise UpdateError(f"Could not download the update: {e}") from e
    return dest


def check_and_stage(
    current: str = __version__,
    *,
    folder: Path | None = None,
    force: bool = False,
    platform: str = sys.platform,
    get_json: GetJson = default_get_json,
    open_url: OpenUrl = default_open_url,
    now: Callable[[], float] = time.time,
) -> str:
    """Look for a newer release and, if there is one, download it ready to install.

    Returns one line saying what happened (never raises: no internet is an ordinary
    outcome in a lab). Asks GitHub at most once a day unless ``force``."""
    folder = folder or updates_dir()
    state = _read_state(folder)
    staged = staged_update(folder)
    if staged and not is_newer(str(staged.get("tag", "")), current):
        Path(str(staged["file"])).unlink(missing_ok=True)  # already installed or older: drop it
        state.pop("staged", None)
        staged = None
    if not force and now() - float(state.get("last_check") or 0) < CHECK_EVERY_S:
        return f"Update {staged['tag']} is downloaded and installs the next time the app starts." if staged else ""
    try:
        info = release_from_json(get_json(API_LATEST), platform)
    except Exception as e:  # noqa: BLE001  (offline, rate limited, bad JSON: all just "could not check")
        state["last_error"] = f"Could not check for updates: {e}"
        _write_state(folder, state)
        return state["last_error"]
    state["last_check"] = now()
    state.pop("last_error", None)
    if info is None or not is_newer(info.tag, current):
        _write_state(folder, state)
        return f"CO2-Response {current} is up to date." if force else ""
    if staged and staged.get("tag") == info.tag:
        _write_state(folder, state)
        return f"Update {info.tag} is downloaded and installs the next time the app starts."
    try:
        dest = folder / info.tag / info.asset
        download(info, dest, open_url)
    except UpdateError as e:
        state["last_error"] = f"Update {info.tag} was not installed: {e}"
        _write_state(folder, state)
        return state["last_error"]
    if staged and staged.get("file") != str(dest):
        Path(str(staged["file"])).unlink(missing_ok=True)
    state["staged"] = {"tag": info.tag, "file": str(dest), "sha256": info.sha256}
    _write_state(folder, state)
    return f"Update {info.tag} is downloaded. It installs the next time CO2-Response starts, never during a recording."


# ------------------------------------------------------------------ install


def install_target(platform: str = sys.platform, executable: str | None = None) -> Path | None:
    """What gets replaced: the .exe (Windows), the .app (macOS) or the app's folder (Linux)."""
    exe = Path(executable or sys.executable)
    if platform == "win32":
        return exe
    if platform == "darwin":
        app = exe.parents[2] if len(exe.parents) > 2 else None
        return app if app is not None and app.suffix == ".app" else None
    return exe.parent


def launch_path(target: Path, platform: str = sys.platform) -> Path:
    """The program to start inside ``target``."""
    if platform == "win32":
        return target
    if platform == "darwin":
        return target / "Contents" / "MacOS" / "CO2-Response"
    return target / "CO2-Response"


def _backup_of(target: Path) -> Path:
    return target.with_name(target.name + ".old")


def _remove(p: Path) -> None:
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        p.unlink(missing_ok=True)


def _swap_in(target: Path, new: Path) -> Path:
    """Put ``new`` where ``target`` is, keeping the old one as ``<name>.old``. ``new`` must
    be beside ``target`` (the same disk) so that each step is a rename. On any failure the
    old one is put back. Returns the backup's path. A running program can be renamed on
    every system (not deleted on Windows), which is why it is renamed away, not overwritten."""
    backup = _backup_of(target)
    _remove(backup)
    os.replace(target, backup)
    try:
        os.replace(new, target)
    except OSError:
        os.replace(backup, target)
        raise
    return backup


def _safe_extract(tar_path: Path, into: Path) -> None:
    with tarfile.open(tar_path, "r:gz") as tar:
        root = into.resolve()
        for m in tar.getmembers():
            dest = (into / m.name).resolve()
            if not (dest == root or root in dest.parents) or m.issym() or m.islnk() or m.isdev():
                raise UpdateError(f"The update file holds an unsafe entry ({m.name}), so it was not used.")
        if hasattr(tarfile, "data_filter"):
            tar.extractall(into, filter="data")  # noqa: S202  (every member was checked above, too)
        else:
            tar.extractall(into)  # noqa: S202


def _install_windows(target: Path, staged: Path) -> Path:
    new = target.with_name(target.name + ".new")
    _remove(new)
    shutil.copy2(staged, new)
    return _swap_in(target, new)


def _install_linux(target: Path, staged: Path) -> Path:
    work = target.with_name(target.name + ".new")
    _remove(work)
    work.mkdir(parents=True)
    try:
        _safe_extract(staged, work)
        new = work / target.name
        exe = new / "CO2-Response"
        if not exe.is_file():
            raise UpdateError("The update file does not hold the app (no CO2-Response program in it).")
        exe.chmod(exe.stat().st_mode | 0o111)
        final = target.with_name(target.name + ".next")
        _remove(final)
        os.replace(new, final)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return _swap_in(target, final)


def _install_mac(target: Path, staged: Path, run: Callable = subprocess.run) -> Path:
    mount = Path(tempfile.mkdtemp(prefix="co2r-update-"))
    new = target.with_name(target.name + ".new")
    _remove(new)
    attached = False
    try:
        run(["hdiutil", "attach", str(staged), "-nobrowse", "-readonly", "-quiet", "-mountpoint", str(mount)], check=True)
        attached = True
        app = mount / "CO2-Response.app"
        if not app.is_dir():
            raise UpdateError("The update file does not hold CO2-Response.app.")
        run(["ditto", str(app), str(new)], check=True)
    except (OSError, subprocess.CalledProcessError) as e:
        _remove(new)
        raise UpdateError(f"Could not open the update: {e}") from e
    finally:
        if attached:
            run(["hdiutil", "detach", str(mount), "-quiet"], check=False)
        shutil.rmtree(mount, ignore_errors=True)
    return _swap_in(target, new)


def install_staged(
    *,
    folder: Path | None = None,
    platform: str = sys.platform,
    executable: str | None = None,
    current: str = __version__,
    run: Callable = subprocess.run,
) -> str | None:
    """Install the downloaded update, if there is one and it is newer. Returns its tag, or
    None when nothing was installed (nothing staged, or it failed: the previous version is
    untouched and the reason is kept in the state file). Never raises."""
    folder = folder or updates_dir()
    staged = staged_update(folder)
    if staged is None or not is_newer(str(staged.get("tag", "")), current):
        return None
    target = install_target(platform, executable)
    state = _read_state(folder)
    try:
        if target is None or not target.exists():
            raise UpdateError("Could not tell where this app is installed.")
        path = Path(str(staged["file"]))
        if platform == "win32":
            backup = _install_windows(target, path)
        elif platform == "darwin":
            backup = _install_mac(target, path, run)
        else:
            backup = _install_linux(target, path)
    except (UpdateError, OSError, tarfile.TarError, shutil.Error) as e:
        state["last_error"] = f"Update {staged.get('tag')} could not be installed ({e}); {current} stays."
        _write_state(folder, state)
        return None
    Path(str(staged["file"])).unlink(missing_ok=True)
    state.pop("staged", None)
    state.pop("last_error", None)
    state["last_update"] = {"from": current, "to": staged["tag"], "at": time.time(), "backup": str(backup)}
    _write_state(folder, state)
    return str(staged["tag"])


def relaunch(target: Path, args: list[str], platform: str = sys.platform) -> int:
    """Start the app that was just installed with the same arguments, and end this one."""
    exe = str(launch_path(target, platform))
    if platform == "win32":
        subprocess.Popen([exe, *args])  # noqa: S603
        return 0
    os.execv(exe, [exe, *args])  # does not return
    return 0


# ------------------------------------------------------------------ in the running app


def enabled_by_environment() -> bool:
    """CO2R_NO_UPDATE=1 turns self-update off (for a computer that must never change)."""
    return not os.environ.get("CO2R_NO_UPDATE")


class BackgroundUpdater:
    """Checks for an update on a background thread and passes what it found, in words, to
    the window through ``messages`` (a thread-safe queue the window polls)."""

    def __init__(self) -> None:
        self.messages: queue.SimpleQueue[str] = queue.SimpleQueue()
        self._thread: threading.Thread | None = None

    def start(self, force: bool = False, **kwargs) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False

        def work() -> None:
            msg = check_and_stage(force=force, **kwargs)
            if msg:
                self.messages.put(msg)

        self._thread = threading.Thread(target=work, name="update check", daemon=True)
        self._thread.start()
        return True


background = BackgroundUpdater()
