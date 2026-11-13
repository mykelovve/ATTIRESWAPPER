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
    # A draft need not have a Git tag yet, so the tag lookup endpoint can
    # return 404 for it. Find drafts by listing releases, then use their IDs.
    releases = json.loads(command("gh", "api", f"repos/{REPO}/releases?per_page=100"))
    matches = [release for release in releases if release["tag_name"] == tag]
    assert len(matches) <= 1, "More than one release uses this tag"
    if matches:
        release = matches[0]
        assert release["target_commitish"] == source_commit
    else:
        request = directory / "release-request.json"
        request.write_text(json.dumps({
            "tag_name": tag, "target_commitish": source_commit,
            "name": f"Attire Swapper {tag}", "draft": True,
            "prerelease": False, "body": notes.read_text(),
        }))
        release = json.loads(command(
            "gh", "api", "--method", "POST", f"repos/{REPO}/releases",
            "--input", str(request),
        ))
    release_endpoint = f"repos/{REPO}/releases/{release['id']}"
    # Never overwrite an existing published asset with a different build.
    assets = {asset["name"]: asset for asset in release["assets"]}
    for path in (archive, sums):
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in assets:
            assert assets[path.name]["size"] == path.stat().st_size
            assert assets[path.name].get("digest") == "sha256:" + checksum
        else:
            upload_url = (
                f"https://uploads.github.com/repos/{REPO}/releases/"
                f"{release['id']}/assets?name={path.name}"
            )
            command("gh", "api", "--method", "POST", upload_url,
                    "-H", "Content-Type: application/octet-stream",
                    "--input", str(path))
    release = json.loads(command("gh", "api", release_endpoint))
    uploaded = {asset["name"]: asset for asset in release["assets"]}
    for path in (archive, sums):
        assert uploaded[path.name]["size"] == path.stat().st_size
        assert uploaded[path.name]["digest"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    release = json.loads(command(
        "gh", "api", "--method", "PATCH", release_endpoint,
        "-F", "draft=false", "-F", "prerelease=false", "-f", "make_latest=true",
    ))
    # Publishing replaces the temporary draft URLs with versioned URLs.
    uploaded = {asset["name"]: asset for asset in release["assets"]}
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
