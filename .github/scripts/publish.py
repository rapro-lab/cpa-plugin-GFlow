import hashlib
import json
import os
from pathlib import Path
import subprocess
import tomllib
import zipfile


ROOT = Path(__file__).resolve().parents[2]
# Must stay in sync with the build matrix in .github/workflows/build.yml.
# Linux and macOS targets are intentionally not built: this fork is published
# for Windows only, and a release must never wait on a platform nobody uses.
PLATFORMS = (("windows", "amd64", "dll"),)


def gh(arguments, check=True):
    result = subprocess.run(["gh", *map(str, arguments)], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace")
    if check and result.returncode:
        raise RuntimeError(result.stderr)
    return result


def main():
    version = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
    tag = "v" + version
    assert os.environ["GITHUB_REF"] == "refs/tags/" + tag
    repository = os.environ["GITHUB_REPOSITORY"]
    distribution = ROOT / "dist-download"
    checksums = []
    archives = []
    for operating_system, architecture, extension in PLATFORMS:
        filename = f"gflow_{version}_{operating_system}_{architecture}.zip"
        path = distribution / filename
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        expected = (distribution / (filename + ".sha256")).read_text(encoding="utf-8").strip()
        assert expected == f"{digest}  {filename}", "Checksum mismatch: " + filename
        evidence = json.loads((distribution / f"verification-{operating_system}-{architecture}.json").read_text(encoding="utf-8"))
        assert evidence["clean_install_smoke"] == "passed" and evidence["self_contained_python"] is True
        assert evidence["platform"] == operating_system + "/" + architecture and evidence["version"] == version
        with zipfile.ZipFile(path) as archive:
            assert archive.namelist() == ["gflow." + extension], "Unexpected ZIP layout"
            assert archive.testzip() is None
        archives.append(path)
        checksums.append(expected)
    checksum_file = distribution / "checksums.txt"
    checksum_file.write_text("\n".join(sorted(checksums)) + "\n", encoding="utf-8")
    existing = gh(["release", "view", tag, "--repo", repository, "--json", "isDraft"], check=False)
    if existing.returncode:
        gh(["release", "create", tag, "--repo", repository, "--verify-tag", "--draft",
            "--title", "GFlow " + tag, "--notes-file", ROOT / "RELEASE_NOTES.md"])
    gh(["release", "upload", tag, *archives, checksum_file, "--repo", repository, "--clobber"])
    pages = json.loads(gh(["api", "--paginate", "--slurp", f"repos/{repository}/releases?per_page=100"]).stdout)
    release = next(item for page in pages for item in page if item["tag_name"] == tag)
    published = {asset["name"]: asset for asset in release["assets"]}
    for path in [*archives, checksum_file]:
        asset = published[path.name]
        assert asset["state"] == "uploaded" and asset["size"] == path.stat().st_size
        assert asset["digest"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    gh(["release", "edit", tag, "--repo", repository, "--draft=false", "--latest",
        "--title", "GFlow " + tag, "--notes-file", ROOT / "RELEASE_NOTES.md"])
    print(release["html_url"], flush=True)


if __name__ == "__main__":
    main()
