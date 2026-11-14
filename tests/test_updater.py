"""Self-update of the standalone app: check, download, verify, install, roll back.

No network and no real Windows or Mac: releases are fake JSON, downloads are bytes, the
disk image is a fake ``hdiutil``/``ditto``, and the swaps happen on real files in a
temporary folder.
"""

import hashlib
import io
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest

from co2response import updater
from co2response.updater import ReleaseInfo, UpdateError


def release(tag="v0.8.0", asset="CO2-Response-Linux.tar.gz", payload=b"x", digest=True, **extra):
    a = {"name": asset, "browser_download_url": f"https://example.invalid/{tag}/{asset}", "size": len(payload)}
    if digest:
        a["digest"] = "sha256:" + hashlib.sha256(payload).hexdigest()
    return {"tag_name": tag, "assets": [a, {"name": "other.zip", "browser_download_url": "https://example.invalid/o"}], **extra}


class Net:
    """A fake GitHub: the release list, and the bytes of each download."""

    def __init__(self, rel, payload=b"x"):
        self.rel, self.payload, self.calls = rel, payload, []

    def get_json(self, url):
        self.calls.append(url)
        if isinstance(self.rel, Exception):
            raise self.rel
        return self.rel

    def open_url(self, url):
        self.calls.append(url)
        return io.BytesIO(self.payload)


def tarball(files: dict[str, bytes], extra=None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            ti.mode = 0o644
            t.addfile(ti, io.BytesIO(data))
        if extra:
            extra(t)
    return buf.getvalue()


# ------------------------------------------------------------------ versions and releases


def test_versions():
    assert updater.parse_version("v0.7.1") == (0, 7, 1) and updater.parse_version("0.7") == (0, 7)
    assert updater.parse_version("nightly") is None and updater.parse_version("v1.2.3-beta") is None
    assert updater.is_newer("v0.8.0", "0.7.0") and updater.is_newer("0.7.1", "0.7.0") and updater.is_newer("v1.0", "0.9.9")
    assert not updater.is_newer("v0.7.0", "0.7.0") and not updater.is_newer("v0.6.9", "0.7.0") and not updater.is_newer("v0.7", "0.7.0")
    assert not updater.is_newer("latest", "0.7.0")  # not a version: never an update


def test_the_right_file_for_each_computer_and_the_checksum():
    payload = b"hello"
    for plat, name in (("win32", "CO2-Response.exe"), ("darwin", "CO2-Response.dmg"), ("linux", "CO2-Response-Linux.tar.gz")):
        info = updater.release_from_json(release(asset=name, payload=payload), plat)
        assert info and info.asset == name and info.tag == "v0.8.0" and info.size == 5
        assert info.sha256 == hashlib.sha256(payload).hexdigest()
    assert updater.release_from_json(release(asset="CO2-Response.exe"), "linux") is None  # no file for this computer
    assert updater.release_from_json(release(draft=True), "linux") is None and updater.release_from_json(release(prerelease=True), "linux") is None
    assert updater.release_from_json(release(digest=False), "linux").sha256 == ""
    assert updater.release_from_json({"nonsense": 1}, "linux") is None and updater.release_from_json([], "linux") is None


# ------------------------------------------------------------------ check and download


def test_a_newer_release_is_downloaded_checked_and_staged(tmp_path):
    payload = tarball({"CO2-Response/CO2-Response": b"new"})
    net = Net(release(payload=payload), payload)
    msg = updater.check_and_stage("0.7.0", folder=tmp_path, platform="linux", get_json=net.get_json, open_url=net.open_url)
    assert "v0.8.0 is downloaded" in msg and "never during a recording" in msg
    staged = updater.staged_update(tmp_path)
    assert staged and staged["tag"] == "v0.8.0" and Path(staged["file"]).read_bytes() == payload
    assert not list(tmp_path.rglob("*.part"))


def test_nothing_newer_means_no_download_and_a_day_between_checks(tmp_path):
    net = Net(release(tag="v0.7.0"))
    assert updater.check_and_stage("0.7.0", folder=tmp_path, platform="linux", get_json=net.get_json, open_url=net.open_url) == ""
    assert net.calls == [updater.API_LATEST] and updater.staged_update(tmp_path) is None
    clock = [updater.read_state(tmp_path)["last_check"]]
    updater.check_and_stage("0.7.0", folder=tmp_path, platform="linux", get_json=net.get_json, open_url=net.open_url, now=lambda: clock[0] + 3600)
    assert len(net.calls) == 1  # within a day: GitHub is not asked again
    updater.check_and_stage("0.7.0", folder=tmp_path, platform="linux", get_json=net.get_json, open_url=net.open_url, now=lambda: clock[0] + 90000)
    assert len(net.calls) == 2
    assert "up to date" in updater.check_and_stage("0.7.0", folder=tmp_path, force=True, platform="linux", get_json=net.get_json, open_url=net.open_url)


@pytest.mark.parametrize("tamper", ["bytes", "size"])
def test_a_download_that_is_not_what_was_listed_is_refused(tmp_path, tamper):
    payload = b"the real file"
    rel = release(payload=payload)
    bad = b"a different file" if tamper == "bytes" else payload + b"!"
    if tamper == "bytes":
        bad = bad[: len(payload)].ljust(len(payload), b"?")  # same size, wrong content: only the checksum catches it
    net = Net(rel, bad)
    msg = updater.check_and_stage("0.7.0", folder=tmp_path, platform="linux", get_json=net.get_json, open_url=net.open_url)
    assert "was not installed" in msg and updater.staged_update(tmp_path) is None
    assert not [p for p in tmp_path.rglob("*") if p.is_file() and p.name != "state.json"]  # nothing left behind
    assert "Update v0.8.0 was not installed" in updater.read_state(tmp_path)["last_error"]


def test_offline_is_an_ordinary_outcome(tmp_path):
    net = Net(OSError("no route to host"))
    msg = updater.check_and_stage("0.7.0", folder=tmp_path, platform="linux", get_json=net.get_json, open_url=net.open_url)
    assert msg.startswith("Could not check for updates") and updater.staged_update(tmp_path) is None


def test_a_staged_update_that_is_no_longer_newer_is_dropped(tmp_path):
    payload = b"x"
    net = Net(release(payload=payload), payload)
    updater.check_and_stage("0.7.0", folder=tmp_path, platform="linux", get_json=net.get_json, open_url=net.open_url)
    f = Path(updater.staged_update(tmp_path)["file"])
    assert f.exists()
    net2 = Net(release(tag="v0.8.0"))
    updater.check_and_stage("0.8.0", folder=tmp_path, force=True, platform="linux", get_json=net2.get_json, open_url=net2.open_url)  # now running 0.8.0
    assert not f.exists() and updater.staged_update(tmp_path) is None


# ------------------------------------------------------------------ install


def stage(tmp_path, tag, data: bytes, name="payload.bin") -> Path:
    f = tmp_path / "updates" / tag / name
    f.parent.mkdir(parents=True)
    f.write_bytes(data)
    updater._write_state(tmp_path / "updates", {"staged": {"tag": tag, "file": str(f), "sha256": ""}})
    return f


def test_windows_swaps_the_one_file_and_keeps_the_old_one(tmp_path):
    exe = tmp_path / "app" / "CO2-Response.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"old")
    stage(tmp_path, "v0.8.0", b"new")
    tag = updater.install_staged(folder=tmp_path / "updates", platform="win32", executable=str(exe), current="0.7.0")
    assert tag == "v0.8.0" and exe.read_bytes() == b"new" and (exe.parent / "CO2-Response.exe.old").read_bytes() == b"old"
    st = updater.read_state(tmp_path / "updates")
    assert "staged" not in st and st["last_update"]["from"] == "0.7.0" and st["last_update"]["to"] == "v0.8.0"
    assert not (exe.parent / "CO2-Response.exe.new").exists()
    # the next update replaces the backup of the one before
    stage(tmp_path, "v0.9.0", b"newer")
    assert updater.install_staged(folder=tmp_path / "updates", platform="win32", executable=str(exe), current="0.8.0") == "v0.9.0"
    assert exe.read_bytes() == b"newer" and (exe.parent / "CO2-Response.exe.old").read_bytes() == b"new"


def test_a_failed_swap_puts_the_old_version_back(tmp_path, monkeypatch):
    exe = tmp_path / "CO2-Response.exe"
    exe.write_bytes(b"old")
    stage(tmp_path, "v0.8.0", b"new")
    real = os.replace
    calls = []

    def flaky(a, b):
        calls.append((a, b))
        if str(a).endswith(".new"):  # the move of the new file into place fails (the restore after it must work)
            raise PermissionError("locked")
        return real(a, b)

    monkeypatch.setattr(os, "replace", flaky)
    assert updater.install_staged(folder=tmp_path / "updates", platform="win32", executable=str(exe), current="0.7.0") is None
    monkeypatch.setattr(os, "replace", real)
    assert exe.read_bytes() == b"old"  # still the old version
    st = updater.read_state(tmp_path / "updates")
    assert "could not be installed" in st["last_error"] and "0.7.0 stays" in st["last_error"] and "staged" in st  # tried again next start


def test_linux_swaps_the_folder(tmp_path):
    app = tmp_path / "opt" / "CO2-Response"
    app.mkdir(parents=True)
    (app / "CO2-Response").write_bytes(b"old program")
    (app / "lib.so").write_bytes(b"old lib")
    data = tarball({"CO2-Response/CO2-Response": b"new program", "CO2-Response/lib.so": b"new lib", "CO2-Response/data/x.txt": b"x"})
    stage(tmp_path, "v0.8.0", data, "CO2-Response-Linux.tar.gz")
    tag = updater.install_staged(folder=tmp_path / "updates", platform="linux", executable=str(app / "CO2-Response"), current="0.7.0")
    assert tag == "v0.8.0"
    assert (app / "CO2-Response").read_bytes() == b"new program" and (app / "data" / "x.txt").read_bytes() == b"x"
    assert os.access(app / "CO2-Response", os.X_OK)
    old = tmp_path / "opt" / "CO2-Response.old"
    assert (old / "CO2-Response").read_bytes() == b"old program" and (old / "lib.so").read_bytes() == b"old lib"
    assert sorted(p.name for p in (tmp_path / "opt").iterdir()) == ["CO2-Response", "CO2-Response.old"]  # no work folders left


@pytest.mark.parametrize("bad", ["traversal", "symlink", "no-program"])
def test_a_bad_archive_is_refused_and_nothing_changes(tmp_path, bad):
    app = tmp_path / "opt" / "CO2-Response"
    app.mkdir(parents=True)
    (app / "CO2-Response").write_bytes(b"old program")
    if bad == "traversal":
        data = tarball({"CO2-Response/CO2-Response": b"x", "../evil.txt": b"evil"})
    elif bad == "symlink":

        def link(t):
            ti = tarfile.TarInfo("CO2-Response/link")
            ti.type = tarfile.SYMTYPE
            ti.linkname = "/etc/passwd"
            t.addfile(ti)

        data = tarball({"CO2-Response/CO2-Response": b"x"}, link)
    else:
        data = tarball({"CO2-Response/readme.txt": b"x"})
    stage(tmp_path, "v0.8.0", data, "CO2-Response-Linux.tar.gz")
    assert updater.install_staged(folder=tmp_path / "updates", platform="linux", executable=str(app / "CO2-Response"), current="0.7.0") is None
    assert (app / "CO2-Response").read_bytes() == b"old program" and not (tmp_path / "opt" / "evil.txt").exists() and not (tmp_path / "evil.txt").exists()
    assert sorted(p.name for p in (tmp_path / "opt").iterdir()) == ["CO2-Response"]
    assert "last_error" in updater.read_state(tmp_path / "updates")


class FakeMac:
    """hdiutil attach makes the mount point hold CO2-Response.app; ditto copies a folder."""

    def __init__(self, fail_ditto=False, empty=False):
        self.cmds, self.fail_ditto, self.empty = [], fail_ditto, empty

    def __call__(self, cmd, check=False):
        self.cmds.append(cmd[:2])
        if cmd[0] == "hdiutil" and cmd[1] == "attach":
            mount = Path(cmd[cmd.index("-mountpoint") + 1])
            if not self.empty:
                (mount / "CO2-Response.app" / "Contents" / "MacOS").mkdir(parents=True)
                (mount / "CO2-Response.app" / "Contents" / "MacOS" / "CO2-Response").write_bytes(b"new mac program")
        elif cmd[0] == "ditto":
            if self.fail_ditto:
                raise subprocess.CalledProcessError(1, cmd)
            shutil.copytree(cmd[1], cmd[2])
        return subprocess.CompletedProcess(cmd, 0)


def mac_app(tmp_path) -> Path:
    app = tmp_path / "Applications" / "CO2-Response.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "CO2-Response").write_bytes(b"old mac program")
    return app


def test_macos_copies_the_app_out_of_the_disk_image_and_swaps_it(tmp_path):
    app = mac_app(tmp_path)
    stage(tmp_path, "v0.8.0", b"dmg bytes", "CO2-Response.dmg")
    fake = FakeMac()
    tag = updater.install_staged(folder=tmp_path / "updates", platform="darwin", executable=str(app / "Contents" / "MacOS" / "CO2-Response"), current="0.7.0", run=fake)
    assert tag == "v0.8.0" and (app / "Contents" / "MacOS" / "CO2-Response").read_bytes() == b"new mac program"
    assert (tmp_path / "Applications" / "CO2-Response.app.old" / "Contents" / "MacOS" / "CO2-Response").read_bytes() == b"old mac program"
    assert fake.cmds == [["hdiutil", "attach"], ["ditto", str(fake.cmds[1][1])], ["hdiutil", "detach"]]  # attached, copied, and always detached


@pytest.mark.parametrize("kw", [{"fail_ditto": True}, {"empty": True}], ids=str)
def test_macos_failures_detach_the_image_and_keep_the_old_app(tmp_path, kw):
    app = mac_app(tmp_path)
    stage(tmp_path, "v0.8.0", b"dmg bytes", "CO2-Response.dmg")
    fake = FakeMac(**kw)
    assert updater.install_staged(folder=tmp_path / "updates", platform="darwin", executable=str(app / "Contents" / "MacOS" / "CO2-Response"), current="0.7.0", run=fake) is None
    assert (app / "Contents" / "MacOS" / "CO2-Response").read_bytes() == b"old mac program"
    assert ["hdiutil", "detach"] in fake.cmds and not (tmp_path / "Applications" / "CO2-Response.app.new").exists()


def test_where_the_app_is_installed():
    assert updater.install_target("win32", r"C:\Apps\CO2-Response.exe") == Path(r"C:\Apps\CO2-Response.exe")
    assert updater.install_target("darwin", "/Applications/CO2-Response.app/Contents/MacOS/CO2-Response") == Path("/Applications/CO2-Response.app")
    assert updater.install_target("darwin", "/usr/bin/python3") is None  # not inside an app: nothing to replace
    assert updater.install_target("linux", "/opt/CO2-Response/CO2-Response") == Path("/opt/CO2-Response")
    assert updater.launch_path(Path("/Applications/CO2-Response.app"), "darwin") == Path("/Applications/CO2-Response.app/Contents/MacOS/CO2-Response")
    assert updater.launch_path(Path("/opt/CO2-Response"), "linux") == Path("/opt/CO2-Response/CO2-Response")


def test_nothing_is_installed_without_a_newer_staged_update(tmp_path):
    exe = tmp_path / "CO2-Response.exe"
    exe.write_bytes(b"old")
    assert updater.install_staged(folder=tmp_path / "updates", platform="win32", executable=str(exe), current="0.7.0") is None
    stage(tmp_path, "v0.7.0", b"same")
    assert updater.install_staged(folder=tmp_path / "updates", platform="win32", executable=str(exe), current="0.7.0") is None
    assert exe.read_bytes() == b"old"


# ------------------------------------------------------------------ the app


def test_download_checks_what_it_got(tmp_path):
    info = ReleaseInfo("v0.8.0", "a.bin", "https://example.invalid/a", 3, hashlib.sha256(b"abc").hexdigest())
    got = updater.download(info, tmp_path / "d" / "a.bin", lambda url: io.BytesIO(b"abc"))
    assert got.read_bytes() == b"abc"
    with pytest.raises(UpdateError, match="checksum"):
        updater.download(info, tmp_path / "e" / "a.bin", lambda url: io.BytesIO(b"abd"))
    assert not list((tmp_path / "e").iterdir())


def test_the_background_check_reports_through_a_queue(tmp_path, monkeypatch):
    payload = b"x"
    net = Net(release(payload=payload), payload)
    monkeypatch.setattr(updater, "updates_dir", lambda: tmp_path)
    bg = updater.BackgroundUpdater()
    assert bg.start(platform="linux", get_json=net.get_json, open_url=net.open_url, current="0.7.0")
    bg._thread.join(10)
    assert "v0.8.0 is downloaded" in bg.messages.get(timeout=1)


def test_a_source_install_never_updates_itself_and_the_setting_defaults_on(tmp_path, monkeypatch):
    from co2response import __main__ as m
    from co2response.settings import SettingsStore

    assert not updater.is_frozen()
    assert m.apply_pending_update([]) is None  # not the standalone app: nothing happens
    assert SettingsStore(tmp_path).load_settings().auto_update is True
    s = SettingsStore(tmp_path)
    cur = s.load_settings()
    cur.auto_update = False
    s.save_settings(cur)
    assert SettingsStore(tmp_path).load_settings().auto_update is False


def test_start_up_installs_and_relaunches_only_when_allowed(tmp_path, monkeypatch):
    from co2response import __main__ as m

    monkeypatch.setenv("CO2R_HOME", str(tmp_path))
    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    exe = tmp_path / "CO2-Response.exe"
    exe.write_bytes(b"old")
    monkeypatch.setattr(updater, "install_target", lambda *a, **k: exe)
    monkeypatch.setattr(updater, "relaunch", lambda target, args, platform=None: 42 if args == ["--sim"] else -1)
    installed = []
    monkeypatch.setattr(updater, "install_staged", lambda **k: installed.append(1) or "v0.8.0")
    assert m.apply_pending_update(["--sim"]) == 42 and installed == [1]  # installed, and the new version is started with the same arguments
    monkeypatch.setenv("CO2R_NO_UPDATE", "1")
    assert m.apply_pending_update(["--sim"]) is None and installed == [1]  # the environment turns it off
    monkeypatch.delenv("CO2R_NO_UPDATE")
    from co2response.settings import SettingsStore

    s = SettingsStore()
    cur = s.load_settings()
    cur.auto_update = False
    s.save_settings(cur)
    assert m.apply_pending_update(["--sim"]) is None and installed == [1]  # the setting turns it off
    monkeypatch.setattr(updater, "install_staged", lambda **k: None)
    cur.auto_update = True
    s.save_settings(cur)
    assert m.apply_pending_update(["--sim"]) is None  # nothing waiting (or it failed): carry on with this version


def test_update_in_the_standalone_app_downloads_and_installs_now(tmp_path, monkeypatch, capsys):
    from co2response import __main__ as m

    monkeypatch.setattr(updater, "is_frozen", lambda: True)
    monkeypatch.setattr(updater, "check_and_stage", lambda **k: "Update v0.8.0 is downloaded.")
    monkeypatch.setattr(updater, "staged_update", lambda folder=None: {"tag": "v0.8.0"})
    monkeypatch.setattr(updater, "install_staged", lambda **k: "v0.8.0")
    assert m.update() == 0
    out = capsys.readouterr().out
    assert "Updated to v0.8.0" in out and ".old" in out
    monkeypatch.setattr(updater, "install_staged", lambda **k: None)
    monkeypatch.setattr(updater, "read_state", lambda folder=None: {"last_error": "boom"})
    assert m.update() == 1 and "boom" in capsys.readouterr().err
