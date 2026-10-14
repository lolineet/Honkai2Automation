"""Build a Windows x64 ZIP using the active, tested Python 3.10 environment."""
from pathlib import Path
import hashlib
import importlib.metadata as metadata
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = "3.10.11"
ADB_VERSION = "36.0.2"


def main():
    if sys.platform != "win32" or sys.version_info[:2] != (3, 10):
        raise SystemExit("Build with the project's Windows x64 Python 3.10 environment.")
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"python-{VERSION}-embed-amd64.zip"
    if not archive.exists():
        urllib.request.urlretrieve(
            f"https://www.python.org/ftp/python/{VERSION}/{archive.name}", archive)
    adb_archive = output / f"platform-tools_r{ADB_VERSION}-win.zip"
    if not adb_archive.exists():
        urllib.request.urlretrieve(
            f"https://dl.google.com/android/repository/{adb_archive.name}", adb_archive)
    # Use a fresh directory on each build; never overwrite a user's portable folder.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="hka-package-") as temporary:
        package = Path(temporary) / "HKA-Windows-x64"
        runtime = package / "runtime"
        runtime.mkdir(parents=True)
        with zipfile.ZipFile(adb_archive) as source:
            source.extractall(package)
        adb_version = subprocess.check_output(
            [str(package / "platform-tools" / "adb.exe"), "version"], text=True).strip()
        with zipfile.ZipFile(archive) as source:
            source.extractall(runtime)
        (runtime / "python310._pth").write_text(
            "python310.zip\n.\nLib/site-packages\n..\nimport site\n", encoding="ascii")
        target = runtime / "Lib" / "site-packages"
        versions = []
        for distribution in metadata.distributions():
            name = distribution.metadata["Name"]
            if name.lower() in {"hka", "pip", "setuptools", "wheel", "opencv-python"}:
                continue
            versions.append(f"{name}=={distribution.version}")
            for entry in distribution.files or []:
                if ".." in entry.parts or entry.suffix == ".pyc":
                    continue
                source = Path(distribution.locate_file(entry))
                if source.is_file():
                    destination = target / entry
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
            # Legacy egg metadata can list source paths rather than installed paths.
            for module in (distribution.read_text("top_level.txt") or "").splitlines():
                if not module.isidentifier():
                    continue
                source = Path(distribution.locate_file(module))
                if source.is_dir():
                    shutil.copytree(source, target / module, dirs_exist_ok=True,
                                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                elif source.with_suffix(".py").is_file():
                    shutil.copy2(source.with_suffix(".py"), target / (module + ".py"))
        import rapidocr
        shutil.copytree(Path(rapidocr.__file__).parent / "models",
                        target / "rapidocr" / "models", dirs_exist_ok=True)
        shutil.copytree(ROOT / "hka", package / "hka",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in ["start_hka.pyw", "打开助手.bat", "config.example.json", "LICENSE", "README.md"]:
            shutil.copy2(ROOT / name, package / name)
        bat = package / "打开助手.bat"
        bat.write_bytes(bat.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n"))
        (package / "DEPENDENCIES.txt").write_text(
            "\n".join(sorted(versions)) + "\n", encoding="utf-8")
        (package / "BUILD.json").write_text(json.dumps({
            "python": VERSION,
            "adb": adb_version,
            "adb_archive_sha256": hashlib.sha256(adb_archive.read_bytes()).hexdigest(),
            "runtime_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
            "source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
            "includes_working_tree": True,
        }, indent=2), encoding="utf-8")
        # Initialize OCR from the packaged models before shipping.
        subprocess.run([str(runtime / "python.exe"), "-c",
                        "from hka.ocr import _engine; _engine(); "
                        "from hka.config import load_config; load_config('config.example.json'); "
                        "print('Portable imports, config and OCR OK')"], cwd=package, check=True)
        result = shutil.make_archive(str(output / "HKA-Windows-x64"), "zip", temporary,
                                     "HKA-Windows-x64")
        print(result)


if __name__ == "__main__":
    main()
