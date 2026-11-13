"""Publish an already validated portable ZIP, without executing its contents.

The temporary artifact ref keeps large binaries out of the main source history.
Only this repository's GitHub runner token is used for the release upload.
"""
from pathlib import Path
from zipfile import ZipFile
import hashlib
import json
import os
import re
import subprocess
import tempfile


REPO = "mykelovve/ATTIRESWAPPER"


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def git_bytes(ref, path):
    return subprocess.check_output(["git", "show", f"{ref}:{path}"])


def main():
    assert os.environ.get("GITHUB_REPOSITORY", REPO) == REPO
    tag = os.environ["RELEASE_TAG"]
    artifact_ref = os.environ["ARTIFACT_REF"]
    assert re.fullmatch(r"v\d+\.\d+\.\d+", tag), "Invalid release tag"
    assert artifact_ref == f"release-input/{tag}", "Unexpected artifact branch"
    subprocess.run(["git", "fetch", "--depth=1", "origin", artifact_ref], check=True)
    artifact_commit = command("git", "rev-parse", "FETCH_HEAD")
    metadata = json.loads(git_bytes(artifact_commit, "package/release.json"))
    assert metadata["tag"] == tag
    source_commit = metadata["source_commit"]
    assert re.fullmatch(r"[0-9a-f]{40}", source_commit)
    # Fetch the exact source commit to check the binary/source binding.
    subprocess.run(["git", "fetch", "--depth=1", "origin", source_commit], check=True)
    version = tag[1:]
    short_version = version[:-2] if version.endswith(".0") else version
    filename = f"Mykelovve_Attire_Swapper_v{short_version}_Portable.zip"
    assert metadata["zip_name"] == filename
    expected_sha = metadata["zip_sha256"]
    assert re.fullmatch(r"[0-9a-f]{64}", expected_sha)
    payload = git_bytes(artifact_commit, f"package/{filename}")
    assert len(payload) == metadata["zip_bytes"] <= 100 * 1024**2
    assert hashlib.sha256(payload).hexdigest() == expected_sha
    directory = Path(tempfile.mkdtemp(prefix="attire-release-"))
    archive = directory / filename
    archive.write_bytes(payload)
    with ZipFile(archive) as zipped:
        assert zipped.testzip() is None
        for path, checksum in metadata["source_sha256"].items():
            assert re.fullmatch(r"Source/[A-Za-z0-9_]+\.py", path)
            actual = zipped.read(path)
            assert hashlib.sha256(actual).hexdigest() == checksum
            assert git_bytes(source_commit, path) == actual
    sums = directory / "SHA256SUMS.txt"
    sums.write_text(f"{expected_sha}  {filename}\n")
    notes = directory / "RELEASE_NOTES.md"
    notes.write_bytes(git_bytes(artifact_commit, "package/RELEASE_NOTES.md"))
    existing = subprocess.run(
        ["gh", "api", f"repos/{REPO}/releases/tags/{tag}"],
        capture_output=True, text=True,
    )
    if existing.returncode:
        subprocess.run([
            "gh", "release", "create", tag, "--repo", REPO, "--draft",
            "--target", source_commit, "--title", f"Attire Swapper {tag}",
            "--notes-file", str(notes),
        ], check=True)
        release = json.loads(command("gh", "api", f"repos/{REPO}/releases/tags/{tag}"))
    else:
        release = json.loads(existing.stdout)
    # Never overwrite an existing published asset with a different build.
    assets = {asset["name"]: asset for asset in release["assets"]}
    for path in (archive, sums):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in assets:
            assert assets[path.name]["size"] == path.stat().st_size
            assert assets[path.name].get("digest") == "sha256:" + checksum
        else:
            subprocess.run(["gh", "release", "upload", tag, str(path), "--repo", REPO], check=True)
    release = json.loads(command("gh", "api", f"repos/{REPO}/releases/tags/{tag}"))
    uploaded = {asset["name"]: asset for asset in release["assets"]}
    for path in (archive, sums):
        assert uploaded[path.name]["size"] == path.stat().st_size
        assert uploaded[path.name]["digest"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    subprocess.run(["gh", "release", "edit", tag, "--repo", REPO, "--draft=false", "--latest"], check=True)
    result = {
        "tag": tag, "source_commit": source_commit,
        "artifact_commit": artifact_commit, "zip_sha256": expected_sha,
        "release_url": release["html_url"],
        "asset_url": uploaded[filename]["browser_download_url"],
    }
    print(json.dumps(result, indent=2))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as file:
            file.write(f"Published [{tag}]({result['release_url']})\n\nSHA-256: `{expected_sha}`\n")


if __name__ == "__main__":
    main()
