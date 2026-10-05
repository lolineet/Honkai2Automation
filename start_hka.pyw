"""Manually open the assistant without a console or a Windows startup task."""
from pathlib import Path
import os
import site
import sys
import webbrowser

ROOT = Path(__file__).resolve().parent
site.addsitedir(str(ROOT / ".venv" / "Lib" / "site-packages"))
os.chdir(ROOT)
(ROOT / "runs").mkdir(exist_ok=True)
sys.stdout = sys.stderr = (ROOT / "runs" / "assistant-service.log").open(
    "a", encoding="utf-8", buffering=1)

import msvcrt
lock = (ROOT / "assistant.lock").open("a+b")
if lock.tell() == 0:
    lock.write(b"0")
    lock.flush()
lock.seek(0)
silent = "--no-browser" in sys.argv
try:
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
except OSError:
    if not silent:
        webbrowser.open("http://127.0.0.1:8765/#tasks")
    raise SystemExit(0)

# Opening the assistant never runs game tasks. The Run button starts the pipeline.
if silent:
    os.environ["HKA_NO_BROWSER"] = "1"
else:
    os.environ.pop("HKA_NO_BROWSER", None)
from hka.web import serve
serve(ROOT / "config.json", 8765)
