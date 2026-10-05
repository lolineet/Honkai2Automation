from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path


class AdbError(RuntimeError):
    pass


def _background_options() -> dict:
    # pythonw otherwise gives each console helper its own visible window.
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class AdbDevice:
    def __init__(self, config: dict):
        self.adb_path = config["adb_path"]
        self.serial = config["serial"]
        self.package = config["package"]
        self.emulator_path = config.get("executable", "")
        self.emulator_args = config.get("arguments", [])
        self.launch_delay = float(config.get("launch_delay", 10))
        self._owned_process: subprocess.Popen | None = None

    def _run(self, *args: str, timeout: float = 30, binary: bool = False) -> str | bytes:
        command = [self.adb_path, "-s", self.serial, *args]
        try:
            result = subprocess.run(command, capture_output=True, timeout=timeout, check=False,
                                    **_background_options())
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdbError(f"ADB 执行失败: {exc}") from exc
        if result.returncode:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise AdbError(f"ADB 返回错误: {message}")
        return result.stdout if binary else result.stdout.decode("utf-8", errors="replace").strip()

    def start_emulator(self) -> None:
        if not self.emulator_path:
            return
        path = Path(self.emulator_path).expanduser()
        if not path.is_file():
            raise AdbError(f"模拟器程序不存在: {path}")
        self._owned_process = subprocess.Popen([str(path), *self.emulator_args], cwd=str(path.parent),
                                                **_background_options())
        time.sleep(self.launch_delay)

    def stop_emulator(self) -> None:
        if self._owned_process and self._owned_process.poll() is None:
            self._owned_process.terminate()
            try:
                self._owned_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._owned_process.kill()

    def connect(self) -> None:
        if ":" in self.serial:
            try:
                subprocess.run([self.adb_path, "connect", self.serial], capture_output=True, timeout=15, check=False,
                               **_background_options())
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise AdbError(f"无法连接 ADB: {exc}") from exc
        state = self._run("get-state", timeout=15)
        if state != "device":
            raise AdbError(f"设备未就绪: {self.serial} ({state})")

    def wait_until_ready(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.connect()
                return
            except AdbError as exc:
                last_error = exc
                time.sleep(min(2, max(0.1, deadline - time.monotonic())))
        raise AdbError(f"模拟器启动超时: {last_error}")

    def screen(self) -> bytes:
        data = self._run("exec-out", "screencap", "-p", timeout=30, binary=True)
        if not data.startswith(b"\x89PNG"):
            raise AdbError("模拟器截图不是 PNG 数据")
        return data

    def size(self) -> tuple[int, int]:
        import io
        from PIL import Image
        with Image.open(io.BytesIO(self.screen())) as image:
            return image.size

    def tap(self, x: int, y: int) -> None:
        self._run("shell", "input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 400) -> None:
        self._run("shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms))

    def key(self, code: int) -> None:
        self._run("shell", "input", "keyevent", str(code))

    def launch_game(self) -> None:
        self._run("shell", "monkey", "-p", self.package, "-c", "android.intent.category.LAUNCHER", "1")

    def installed(self) -> bool:
        output = self._run("shell", "pm", "path", self.package)
        return "package:" in output
