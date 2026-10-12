"""Smoke-test the ZIP in a relocated folder without running game tasks."""
from pathlib import Path
import os
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    with tempfile.TemporaryDirectory(prefix="HKA relocated ") as temporary:
        relocated = Path(temporary) / "中文目录 with spaces"
        with zipfile.ZipFile(ROOT / "dist" / "HKA-Windows-x64.zip") as archive:
            names = archive.namelist()
            assert not any(Path(name).name in {"config.json", "state.json", "assistant.lock"}
                           for name in names)
            archive.extractall(relocated)
        package = relocated / "HKA-Windows-x64"
        python = str(package / "runtime" / "python.exe")
        environment = dict(os.environ, HKA_NO_BROWSER="1", PYTHONPATH="", PYTHONHOME="")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            adb_port = str(probe.getsockname()[1])
        adb = str(package / "platform-tools" / "adb.exe")
        try:
            devices = subprocess.check_output([adb, "-P", adb_port, "devices"],
                                              stderr=subprocess.STDOUT, timeout=20)
            assert b"List of devices attached" in devices
        finally:
            subprocess.run([adb, "-P", adb_port, "kill-server"],
                           capture_output=True, timeout=10, check=False)
        subprocess.run([python, "-c",
                        "from pathlib import Path; import subprocess; from hka.adb import AdbDevice; "
                        "c={'adb_path':'builtin','serial':'unused','package':'unused'}; "
                        "d=AdbDevice(c); assert Path(d.adb_path).is_absolute(); "
                        "assert 'Android Debug Bridge' in subprocess.check_output([d.adb_path,'version'],text=True); "
                        "c['adb_path']='custom/adb.exe'; assert AdbDevice(c).adb_path == c['adb_path']"],
                       cwd=package, env=environment, check=True)
        bootstrap = (
            "import runpy, hka.web; "
            "hka.web.serve=lambda *args: None; "
            "runpy.run_path('start_hka.pyw', run_name='__main__')"
        )
        subprocess.run([python, "-c", bootstrap], cwd=package, env=environment, check=True)
        config = package / "config.json"
        original = config.read_bytes()
        subprocess.run([python, "-c", bootstrap], cwd=package, env=environment, check=True)
        assert config.read_bytes() == original
        subprocess.run([python, "-c",
                        "import io; from PIL import Image; from hka.ocr import read_text; "
                        "b=io.BytesIO(); Image.new('RGB',(320,180),'white').save(b,format='PNG'); "
                        "assert read_text(b.getvalue()) == []"],
                       cwd=package, env=environment, check=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        process = subprocess.Popen([python, "-c",
                                    "from pathlib import Path; from hka.web import serve; "
                                    f"serve(Path('config.json'), {port})"],
                                   cwd=package, env=environment, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.PIPE)
        try:
            deadline = time.monotonic() + 30
            while True:
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as response:
                        assert response.status == 200 and b"<html" in response.read().lower()
                    break
                except OSError:
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError("Portable web startup failed")
                    time.sleep(0.2)
        finally:
            process.terminate()
            _, errors = process.communicate(timeout=10)
            if errors:
                print(errors.decode("utf-8", errors="replace"))
        # Exercise the actual BAT entry without opening a browser or a game task.
        (package / "start_hka.pyw").write_text(
            "from pathlib import Path\nimport sys\n"
            "assert '--no-browser' in sys.argv\n"
            "assert '--console' in sys.argv\n"
            "import time; time.sleep(0.3)\n"
            "Path('bat-started.txt').write_text('OK')\n", encoding="ascii")
        subprocess.run(["cmd.exe", "/d", "/c", "打开助手.bat", "--no-browser"],
                       cwd=package, env=environment, check=True, timeout=10)
        assert (package / "bat-started.txt").exists(), "BAT must wait for its foreground service"
        print("BAT, bundled ADB, custom ADB, ZIP isolation, relocated Unicode path, first-run config, config preservation, OCR and HTTP: OK")


if __name__ == "__main__":
    main()
