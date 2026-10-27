import os
from importlib.metadata import distribution
from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules


root = Path(SPECPATH).resolve().parents[1]
sys.path[:0] = [str(root / "python"), str(root / "python" / "flow2api")]
lean = os.environ.get("GFLOW_LEAN", "").strip().lower() in {"1", "true", "yes", "on"}
datas = [
    (str(root / "python/flow2api/config/setting_example.toml"), "config"),
    (str(root / "python/flow2api/LICENSE"), "licenses/flow2api"),
]
binaries = []
# The worker only speaks stdio JSON-RPC: the FastAPI app (src.main) and the
# browser dashboard router (src.api.admin) are bundled for the standalone
# server entry point, never started here.
unused_modules = ["src.main", "src.api.admin"]
hiddenimports = [path.stem for path in (root / "python").glob("*.py") if path.name != "worker.py"]
hiddenimports += collect_submodules("src", filter=lambda name: name not in unused_modules)
hiddenimports += [
    Path(entry).name.split(".", 1)[0]
    for entry in distribution("tomli").files or []
    if "__mypyc." in Path(entry).name
]
packages = ["curl_cffi", "nodriver", "aiosqlite", "bcrypt", "psutil", "tomli"]
excludes = ["tkinter", "matplotlib", "numpy", "pandas", "pytest", *unused_modules]
if lean:
    # GFLOW_LEAN=1 (build.py --lean) drops playwright: 64 MB -> 25 MB on
    # Windows. Only the legacy 'browser' captcha mode needs it; that mode then
    # reports "playwright is not installed or unavailable" instead of starting a
    # headful Chromium. The default 'personal' mode uses nodriver and is
    # unaffected. Never enable this for a build that must support 'browser'.
    excludes.append("playwright")
else:
    packages.append("playwright")
for package in packages:
    package_data, package_binaries, package_imports = collect_all(package)
    datas += package_data
    binaries += package_binaries
    hiddenimports += package_imports

analysis = Analysis(
    [str(root / "python/worker.py")],
    pathex=[str(root / "python"), str(root / "python" / "flow2api")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="gflow-worker",
    console=True,
    debug=False,
    strip=False,
    upx=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
