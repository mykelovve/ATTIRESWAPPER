"""Pinned GitHub release updates, verified staging and a rollback installer.

Release notes are display data, never commands. The copied frozen application
runs the installer so users do not need Python, PowerShell, or administrator mode.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

REPOSITORY = "mykelovve/ATTIRESWAPPER"
LATEST_URL = "https://api.github.com/repos/" + REPOSITORY + "/releases/latest"
MAX_PACKAGE = 200 * 1024 * 1024
MAX_EXE = 180 * 1024 * 1024
MAX_EXPANDED = 400 * 1024 * 1024
_REDIRECT_HOSTS = frozenset(("github.com", "api.github.com", "release-assets.githubusercontent.com",
                             "objects.githubusercontent.com", "github-releases.githubusercontent.com"))
_VERSION = re.compile(r"v?(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})(?:\.(0|[1-9][0-9]{0,3}))?\Z")
_ASSET = re.compile(r"Mykelovve_Attire_Swapper_v([0-9]+\.[0-9]+(?:\.[0-9]+)?)_Portable\.zip\Z")


class UpdateError(RuntimeError):
    pass


def version_tuple(value):
    match = _VERSION.fullmatch(str(value))
    if not match:
        raise UpdateError("The release has an unsupported version number.")
    return tuple(int(part or 0) for part in match.groups())


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _https(url):
    parsed = urllib.parse.urlsplit(str(url))
    if (parsed.scheme != "https" or parsed.username or parsed.password or parsed.port
            or parsed.fragment or parsed.hostname not in _REDIRECT_HOSTS):
        raise UpdateError("The update server returned an unexpected download address.")
    return parsed


class _PinnedRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        _https(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def open_url(url):
    _https(url)
    request = urllib.request.Request(url, headers={"User-Agent": "Mykelovve-Attire-Swapper-Updater",
                                                  "Accept": "application/vnd.github+json"})
    try:
        return urllib.request.build_opener(_PinnedRedirect()).open(request, timeout=30)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError("No app release is available yet. Try Update app again later.") from error
        if error.code in (403, 429):
            raise UpdateError("GitHub temporarily limited update checks. Try again later.") from error
        raise UpdateError("GitHub could not provide the update (HTTP %s)." % error.code) from error
    except (OSError, urllib.error.URLError) as error:
        raise UpdateError("Could not connect to GitHub. Check your connection and try Update app again.") from error


def _read_small(url, limit, opener):
    with opener(url) as response:
        result = response.read(limit + 1)
    if len(result) > limit:
        raise UpdateError("The release response is larger than expected.")
    return result


def _asset_url(asset, tag):
    name = asset.get("name")
    if not isinstance(name, str) or not name or len(name) > 160 or "/" in name or "\\" in name:
        raise UpdateError("The release has an invalid download filename.")
    url = asset.get("browser_download_url", "")
    _https(url)
    expected = "https://github.com/" + REPOSITORY + "/releases/download/"
    expected += urllib.parse.quote(tag, safe="") + "/" + urllib.parse.quote(name, safe="")
    if url != expected:
        raise UpdateError("The update does not belong to the app's release repository.")
    return url


@dataclass(frozen=True)
class Release:
    version: str
    filename: str
    url: str
    size: int
    sha256: str


def check_release(current_version, *, opener=open_url):
    """Return a verified-metadata newer stable release, or None when current."""
    try:
        metadata = json.loads(_read_small(LATEST_URL, 2 * 1024 * 1024, opener))
    except (ValueError, UnicodeError) as error:
        raise UpdateError("GitHub returned unreadable release information.") from error
    if not isinstance(metadata, dict) or metadata.get("draft") or metadata.get("prerelease"):
        raise UpdateError("The latest app release is not a stable published release.")
    tag = metadata.get("tag_name", "")
    latest = version_tuple(tag)
    if latest <= version_tuple(current_version):
        return None
    assets = metadata.get("assets")
    if not isinstance(assets, list) or len(assets) > 100:
        raise UpdateError("The release has an invalid asset list.")
    matching = []
    for asset in assets:
        if not isinstance(asset, dict):
            continue
        match = _ASSET.fullmatch(str(asset.get("name", "")))
        if match and version_tuple(match[1]) == latest:
            matching.append((asset, match[1]))
    if len(matching) != 1:
        raise UpdateError("The release needs exactly one portable Attire Swapper ZIP for this version.")
    asset, version = matching[0]
    url = _asset_url(asset, tag)
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= MAX_PACKAGE:
        raise UpdateError("The portable update has an unexpected size.")
    digests = []
    digest = asset.get("digest")
    if digest is not None:
        if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            raise UpdateError("GitHub returned an unsupported package checksum.")
        digests.append(digest[7:].lower())
    sums = [a for a in assets if isinstance(a, dict) and a.get("name") in ("SHA256SUMS", "SHA256SUMS.txt")]
    if len(sums) > 1:
        raise UpdateError("The release has conflicting checksum files.")
    if sums:
        try:
            checksums = _read_small(_asset_url(sums[0], tag), 65536, opener).decode("utf-8-sig")
        except UnicodeError as error:
            raise UpdateError("The release checksum file is unreadable.") from error
        values = []
        for line in checksums.splitlines():
            match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
            if match and match[2] == asset["name"]:
                values.append(match[1].lower())
        if len(values) != 1:
            raise UpdateError("The checksum file does not identify this portable ZIP exactly once.")
        digests.extend(values)
    if not digests or len(set(digests)) != 1:
        raise UpdateError("The release package has no consistent SHA256 checksum. Nothing was installed.")
    return Release(version, asset["name"], url, size, digests[0])


def _validate_pe(path):
    with Path(path).open("rb") as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b"MZ":
            raise UpdateError("The update does not contain a Windows application.")
        offset = struct.unpack_from("<I", header, 60)[0]
        if not 64 <= offset <= min(Path(path).stat().st_size - 4, 1024 * 1024):
            raise UpdateError("The Windows application header is invalid.")
        stream.seek(offset)
        if stream.read(4) != b"PE\0\0":
            raise UpdateError("The Windows application header is invalid.")


def extract_executable(package, stage, version):
    expected = "Mykelovve_Attire_Swapper_v" + version + ".exe"
    destination = Path(stage) / "application.exe"
    try:
        with zipfile.ZipFile(package) as archive:
            seen, executables, total = set(), [], 0
            entries = archive.infolist()
            if len(entries) > 5000:
                raise UpdateError("The portable ZIP contains too many files.")
            for entry in entries:
                name = entry.filename
                parts = PurePosixPath(name).parts
                mode = (entry.external_attr >> 16) & 0o170000
                folded = name.casefold().rstrip("/")
                if (not name or len(name) > 512 or "\\" in name or ":" in name or "\x00" in name
                        or name.startswith("/") or any(part in ("..", ".") for part in name.split("/"))
                        or folded in seen or mode == stat.S_IFLNK):
                    raise UpdateError("The portable ZIP contains an unsafe path.")
                seen.add(folded)
                total += entry.file_size
                if total > MAX_EXPANDED or entry.flag_bits & 1:
                    raise UpdateError("The portable ZIP exceeds the supported extraction limits.")
                if name.lower().endswith(".exe"):
                    executables.append(entry)
            if len(executables) != 1 or PurePosixPath(executables[0].filename).name != expected:
                raise UpdateError("The portable ZIP must contain exactly the expected app executable.")
            item = executables[0]
            if not 64 < item.file_size <= MAX_EXE:
                raise UpdateError("The app executable has an unexpected size.")
            copied = 0
            with archive.open(item) as source, destination.open("xb") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    copied += len(chunk)
                    if copied > item.file_size or copied > MAX_EXE:
                        raise UpdateError("The app executable exceeds its expected size.")
                    target.write(chunk)
            if copied != item.file_size:
                raise UpdateError("The app executable is incomplete.")
        _validate_pe(destination)
        return destination
    except (zipfile.BadZipFile, OSError, RuntimeError) as error:
        destination.unlink(missing_ok=True)
        if isinstance(error, UpdateError):
            raise
        raise UpdateError("The downloaded portable ZIP could not be safely read.") from error


def stage_release(release, updates_dir, *, progress=lambda received, total: None, opener=open_url):
    _https(release.url)
    folder = Path(updates_dir)
    folder.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="update-", dir=folder))
    package = stage / "package.zip"
    try:
        received, digest = 0, hashlib.sha256()
        with opener(release.url) as response, package.open("xb") as target:
            while True:
                chunk = response.read(1024 * 256)
                if not chunk:
                    break
                received += len(chunk)
                if received > release.size or received > MAX_PACKAGE:
                    raise UpdateError("The downloaded ZIP is larger than the published package.")
                digest.update(chunk)
                target.write(chunk)
                progress(received, release.size)
        if received != release.size or digest.hexdigest() != release.sha256:
            raise UpdateError("The update checksum did not match. Nothing was installed; try again.")
        executable = extract_executable(package, stage, release.version)
        result = {"version": release.version, "stage": str(stage.resolve()),
                  "executable": str(executable.resolve()), "sha256": sha256(executable),
                  "package_sha256": release.sha256, "package_bytes": received}
        (stage / "verified.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        package.unlink()  # The verified executable is sufficient; keep disk use small.
        return result
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def cleanup_completed_stages(updates_dir, current_version, *, now=None):
    """Reclaim our successful staging copies after a day; retain EXE backups."""
    cutoff = (time.time() if now is None else now) - 86400
    removed = []
    for stage in Path(updates_dir).glob("update-*"):
        if not stage.is_dir() or stage.is_symlink():
            continue
        try:
            status = stage / "install-status.json"
            if not status.is_file() or status.stat().st_mtime > cutoff or status.stat().st_size > 16384:
                continue
            value = json.loads(status.read_text(encoding="utf-8"))
            if value.get("success") is True and version_tuple(value.get("version", "")) <= version_tuple(current_version):
                shutil.rmtree(stage)
                removed.append(str(stage))
        except (OSError, ValueError, UpdateError):
            continue
    return removed


def prepare_install(staged, current_exe, current_version, *, pid=None):
    current = Path(current_exe).resolve()
    stage = Path(staged["stage"]).resolve()
    incoming = Path(staged["executable"]).resolve()
    if incoming.parent != stage or incoming.name != "application.exe" or sha256(incoming) != staged["sha256"]:
        raise UpdateError("The staged app changed before installation. Download the update again.")
    if current.suffix.lower() != ".exe" or not current.is_file() or current == incoming:
        raise UpdateError("In-app installation requires the portable Windows EXE.")
    version_tuple(staged["version"])
    version_tuple(current_version)
    try:
        probe = current.parent / (".swapper-write-" + secrets.token_hex(8))
        with probe.open("xb") as stream:
            stream.write(b"update")
        probe.unlink()
    except OSError as error:
        raise UpdateError("The app folder is read-only. Move the portable EXE to a writable folder, then click Update app.") from error
    token = secrets.token_hex(24)
    helper = stage / "installer.exe"
    # A copy is necessary: a hardlink would keep the running Windows image locked.
    shutil.copy2(current, helper)
    config = {"schema": 1, "stage": str(stage), "current": str(current), "incoming": str(incoming),
              "helper": str(helper), "incoming_sha256": staged["sha256"], "current_sha256": sha256(current),
              "old_version": current_version, "version": staged["version"],
              "old_pid": int(os.getpid() if pid is None else pid), "token": token,
              "health": str(stage / "launch-health.json"), "status": str(stage / "install-status.json"),
              "backup": str(current.parent / (current.stem + ".previous-" + current_version + "-" + token[:8] + ".exe"))}
    path = stage / "install.json"
    for name in ("installer-ready.json", "launch-health.json", "install-status.json"):
        (stage / name).unlink(missing_ok=True)
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return path


def launch_installer(config, *, popen=subprocess.Popen):
    path = Path(config).resolve()
    value = json.loads(path.read_text(encoding="utf-8"))
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    environment = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    return popen([value["helper"], "--apply-update", str(path)], cwd=value["stage"], env=environment,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 creationflags=flags, close_fds=True)


class ProcessOperations:
    def wait_for_exit(self, pid, timeout=300):
        if os.name != "nt":
            raise UpdateError("The installer can only replace a running Windows application.")
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x00100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:  # Already exited.
                return
            raise UpdateError("Could not wait for the old app to close.")
        try:
            if kernel.WaitForSingleObject(handle, timeout * 1000) != 0:
                raise UpdateError("The old app did not close. No files were replaced.")
        finally:
            kernel.CloseHandle(handle)

    def launch(self, arguments, cwd):
        return subprocess.Popen(arguments, cwd=cwd, close_fds=True,
                                env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1"))

    def await_health(self, process, path, token, version, current, timeout=40):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return False
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if (value.get("token") == token and version_tuple(value.get("version", "")) == version_tuple(version)
                        and Path(value.get("executable", "")).resolve() == current):
                    return True
            except (OSError, ValueError, UpdateError):
                pass
            time.sleep(0.2)
        return False

    def stop(self, process):
        if process is not None and process.poll() is None:
            if os.name == "nt":
                # A frozen one-file app has a bootloader parent AND a Python
                # child. Stop the tree so the child cannot keep the EXE locked.
                import ctypes
                buffer = ctypes.create_unicode_buffer(32768)
                length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
                if 0 < length < len(buffer):
                    tool = Path(buffer.value) / "taskkill.exe"
                    try:
                        stopped = subprocess.run([str(tool), "/PID", str(int(process.pid)), "/T", "/F"],
                                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                                 stderr=subprocess.DEVNULL, timeout=15,
                                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                        if stopped.returncode == 0:
                            process.wait(timeout=10)
                            return
                    except (OSError, subprocess.TimeoutExpired):
                        pass
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def wait_installer_ready(config_path, process, timeout=60):
    """Keep the existing GUI alive until the copied helper has loaded safely."""
    config = Path(config_path).resolve()
    value = json.loads(config.read_text(encoding="utf-8"))
    ready = config.parent / "installer-ready.json"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise UpdateError("The update installer could not start. The current app is still running.")
        try:
            signal = json.loads(ready.read_text(encoding="utf-8"))
            if signal.get("token") == value["token"] and signal.get("ready") is True:
                return
        except (OSError, ValueError):
            pass
        time.sleep(0.1)
    ProcessOperations().stop(process)
    raise UpdateError("The update installer did not become ready. The current app is still running; try again.")


def _atomic_replace(source, destination, timeout=30):
    """Allow the old PyInstaller bootloader to release its Windows image lock."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            sharing = os.name == "nt" and getattr(error, "winerror", None) in (None, 5, 32, 33)
            if not sharing or time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _load_config(path):
    path = Path(path).resolve()
    if path.stat().st_size > 16384:
        raise UpdateError("Installer configuration is too large.")
    value = json.loads(path.read_text(encoding="utf-8"))
    stage = Path(value["stage"]).resolve()
    current = Path(value["current"]).resolve()
    incoming = Path(value["incoming"]).resolve()
    helper = Path(value["helper"]).resolve()
    backup = Path(value["backup"]).resolve()
    if (value.get("schema") != 1 or path.parent != stage or path.name != "install.json"
            or incoming != stage / "application.exe" or helper != stage / "installer.exe"
            or Path(value["health"]).resolve() != stage / "launch-health.json"
            or Path(value["status"]).resolve() != stage / "install-status.json"
            or current.suffix.lower() != ".exe" or current.parent == stage
            or backup.parent != current.parent or backup == current or backup.exists()
            or not re.fullmatch(r"[a-f0-9]{48}", value.get("token", ""))
            or type(value.get("old_pid")) is not int or value["old_pid"] <= 0):
        raise UpdateError("Installer paths or settings are invalid.")
    if version_tuple(value["version"]) <= version_tuple(value["old_version"]):
        raise UpdateError("The installer refuses a downgrade.")
    for key, filename in (("incoming_sha256", incoming), ("current_sha256", current), ("current_sha256", helper)):
        if not re.fullmatch(r"[a-f0-9]{64}", value.get(key, "")) or sha256(filename) != value[key]:
            raise UpdateError("An installer file changed after verification.")
    _validate_pe(incoming)
    return value, stage, current, incoming, backup


def apply_update(config_path, *, operations=None):
    """Installer entry; processes/files are injectable for real transaction tests."""
    ops = operations or ProcessOperations()
    value, stage, current, incoming, backup = _load_config(config_path)
    (stage / "installer-ready.json").write_text(json.dumps({"ready": True, "token": value["token"],
                                                          "pid": os.getpid()}), encoding="utf-8")
    status = stage / "install-status.json"
    new_process, replaced, old_closed = None, False, False
    temporary = current.parent / (".swapper-new-" + value["token"][:16] + ".exe")
    result = {"success": False, "version": value["version"], "backup": str(backup)}
    try:
        ops.wait_for_exit(value["old_pid"])
        old_closed = True
        if sha256(current) != value["current_sha256"] or sha256(incoming) != value["incoming_sha256"]:
            raise UpdateError("The app changed while waiting to install. Nothing was replaced.")
        with backup.open("xb") as target, current.open("rb") as source:
            shutil.copyfileobj(source, target, 1024 * 1024)
        if sha256(backup) != value["current_sha256"]:
            raise UpdateError("The app backup could not be verified.")
        with temporary.open("xb") as target, incoming.open("rb") as source:
            shutil.copyfileobj(source, target, 1024 * 1024)
        if sha256(temporary) != value["incoming_sha256"]:
            raise UpdateError("The replacement application could not be verified.")
        _atomic_replace(temporary, current)
        replaced = True
        health = stage / "launch-health.json"
        health.unlink(missing_ok=True)
        new_process = ops.launch([str(current), "--updated-launch", str(health), value["token"]], str(current.parent))
        if not ops.await_health(new_process, health, value["token"], value["version"], current):
            raise UpdateError("The new app did not finish starting; restoring the previous app.")
        result.update(success=True, installed_sha256=sha256(current), rollback=False)
    except BaseException as error:
        result["error"] = str(error)
        if replaced:
            try:
                ops.stop(new_process)
                # Keep the verified backup while rolling back atomically.
                shutil.copy2(backup, temporary)
                if sha256(temporary) != value["current_sha256"]:
                    raise UpdateError("The previous app backup changed.")
                _atomic_replace(temporary, current)
                ops.launch([str(current)], str(current.parent))
                result["rollback"] = True
            except BaseException as rollback_error:
                result.update(rollback=False, rollback_error=str(rollback_error))
        else:
            result["rollback"] = False
            if old_closed:
                try:
                    ops.launch([str(current)], str(current.parent))
                    result["previous_app_restarted"] = True
                except BaseException as restart_error:
                    result["restart_error"] = str(restart_error)
    finally:
        temporary.unlink(missing_ok=True)
        status.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def write_launch_health(path, token, version):
    path = Path(path).resolve()
    if path.name != "launch-health.json" or not re.fullmatch(r"[a-f0-9]{48}", token):
        raise UpdateError("The update startup check is invalid.")
    config = path.parent / "install.json"
    value = json.loads(config.read_text(encoding="utf-8"))
    if (value.get("token") != token or Path(value.get("current", "")).resolve() != Path(sys.executable).resolve()
            or version_tuple(value.get("version", "")) != version_tuple(version)):
        raise UpdateError("The update startup check does not match this app.")
    path.write_text(json.dumps({"token": token, "version": version, "pid": os.getpid(),
                               "executable": str(Path(sys.executable).resolve())}), encoding="utf-8")
