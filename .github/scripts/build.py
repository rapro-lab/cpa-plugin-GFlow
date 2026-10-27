import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib


ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT / ".build"


def run(arguments, environment):
    print("Running " + " ".join(str(argument) for argument in arguments), flush=True)
    subprocess.run([str(argument) for argument in arguments], cwd=ROOT, env=environment, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--goos", required=True, choices=["windows", "linux", "darwin"])
    parser.add_argument("--goarch", required=True, choices=["amd64", "arm64"])
    parser.add_argument("--target", required=True)
    parser.add_argument(
        "--lean", action="store_true",
        help="omit playwright from the frozen worker (64 MB -> 25 MB on Windows); the legacy "
             "'browser' captcha mode is unavailable in that build",
    )
    options = parser.parse_args()
    temporary = BUILD / "tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(
        TEMP=str(temporary), TMP=str(temporary), TMPDIR=str(temporary),
        PYTHONDONTWRITEBYTECODE="1", PIP_DISABLE_PIP_VERSION_CHECK="1",
        PIP_CACHE_DIR=str(BUILD / "pip-cache"), GFLOW_TEST_PYTHON=sys.executable,
    )
    if options.lean:
        environment["GFLOW_LEAN"] = "1"
    version = tomllib.loads((ROOT / "Cargo.toml").read_text(encoding="utf-8"))["package"]["version"]
    reference = environment.get("GITHUB_REF", "")
    if reference.startswith("refs/tags/") and reference != "refs/tags/v" + version:
        raise RuntimeError("Release tag and plugin metadata versions must match")
    run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py", "-v"], environment)
    upstream_environment = environment | {"PYTHONPATH": str(ROOT / "python/flow2api")}
    run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests/upstream", "-p", "test_*.py", "-v"], upstream_environment)
    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--workpath", BUILD / "freezer", "--distpath", BUILD / "worker",
        ROOT / ".github/scripts/worker.spec",
    ], environment)
    worker = BUILD / "worker" / ("gflow-worker.exe" if options.goos == "windows" else "gflow-worker")
    worker_digest = hashlib.sha256(worker.read_bytes()).hexdigest()
    environment.update(GFLOW_WORKER_BINARY=str(worker), GFLOW_WORKER_SHA256=worker_digest)
    run(["cargo", "test", "--locked"], environment)
    run(["cargo", "build", "--locked", "--release", "--target", options.target], environment)
    extension = {"windows": "dll", "darwin": "dylib", "linux": "so"}[options.goos]
    library_name = "gflow." + extension
    compiled = ROOT / "target" / options.target / "release" / (("" if options.goos == "windows" else "lib") + library_name)
    distribution = ROOT / "dist"
    distribution.mkdir(exist_ok=True)
    library = distribution / library_name
    shutil.copy2(compiled, library)
    run([sys.executable, "-B", ROOT / ".github/scripts/smoke_plugin.py", "--library", library], environment)
    native_environment = environment | {"GFLOW_NATIVE_LIBRARY": str(library)}
    run([sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-p", "test_bootstrap.py", "-v"], native_environment)
    archive_name = f"gflow_{version}_{options.goos}_{options.goarch}.zip"
    run([
        sys.executable, "-B", ROOT / ".github/scripts/package.py",
        "--library", library, "--archive", distribution / archive_name,
        "--checksum", distribution / (archive_name + ".sha256"),
    ], environment)
    evidence = {
        "version": version, "platform": options.goos + "/" + options.goarch,
        "worker_sha256": worker_digest, "self_contained_python": True,
        "lean_worker": options.lean, "clean_install_smoke": "passed",
        "live_google_generation": "not requested",
    }
    (distribution / f"verification-{options.goos}-{options.goarch}.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
