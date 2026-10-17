"""Manually open the assistant without a console or a Windows startup task."""
from pathlib import Path
import os
import site
import sys
import webbrowser
import shutil
import time

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))
os.chdir(ROOT)
(ROOT / "runs").mkdir(exist_ok=True)
if not (ROOT / "config.json").exists():
    shutil.copyfile(ROOT / "config.example.json", ROOT / "config.json")
if "--console" not in sys.argv:
    sys.stdout = sys.stderr = (ROOT / "runs" / "assistant-service.log").open(
        "a", encoding="utf-8", buffering=1)

import msvcrt
lock = (ROOT / "assistant.lock").open("a+b")
if lock.tell() == 0:
    lock.write(b"0")
    lock.flush()
lock.seek(0)
silent = "--no-browser" in sys.argv
url_path = ROOT / "runs" / "assistant-url.txt"
try:
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
except OSError:
    if not silent:
        for _ in range(50):
            if url_path.exists():
                webbrowser.open(url_path.read_text(encoding="utf-8").strip() + "#tasks")
                break
            time.sleep(0.1)
        else:
            print("助手正在启动，请稍后重新打开。")
    raise SystemExit(0)

# Opening the assistant never runs game tasks. The Run button starts the pipeline.
if silent:
    os.environ["HKA_NO_BROWSER"] = "1"
else:
    os.environ.pop("HKA_NO_BROWSER", None)
from hka.web import serve
url_path.unlink(missing_ok=True)
try:
    serve(ROOT / "config.json", 8765, fallback_port=True, ready_path=url_path)
except KeyboardInterrupt:
    pass
