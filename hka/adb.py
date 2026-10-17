from __future__ import annotations

import os
import re
import subprocess
import time
import threading
from pathlib import Path
from .cancellation import Cancelled


class AdbError(RuntimeError):
    pass


def _background_options() -> dict:
    # pythonw otherwise gives each console helper its own visible window.
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


class AdbDevice:
    def __init__(self, config: dict):
        self.adb_path = config.get("adb_path", "builtin").strip().strip('"')
        bundled_adb = Path(__file__).resolve().parents[1] / "platform-tools" / "adb.exe"
        if self.adb_path.lower() in {"builtin", "内置"}:
            if not bundled_adb.is_file():
                raise AdbError("内置 ADB 缺失，请重新解压完整免安装包，或指定 adb.exe 的完整路径")
            self.adb_path = str(bundled_adb)
        elif self.adb_path.lower() in {"adb", "adb.exe"} and bundled_adb.is_file():
            self.adb_path = str(bundled_adb)
        elif Path(self.adb_path).is_dir():
            executable = Path(self.adb_path) / "adb.exe"
            if not executable.is_file():
                raise AdbError(f"ADB 文件夹中没有 adb.exe: {self.adb_path}")
            self.adb_path = str(executable.resolve())
        self.serial = config["serial"].strip()
        self.package = config["package"]
        self.emulator_path = config.get("executable", "")
        self.emulator_args = config.get("arguments", [])
        self.launch_delay = float(config.get("launch_delay", 10))
        self._owned_process: subprocess.Popen | None = None
        self.cancel: threading.Event | None = None

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    def _wait(self, seconds: float) -> None:
        self._check_cancel()
        if self.cancel is None:
            time.sleep(seconds)
        else:
            self.cancel.wait(max(0, seconds))
        self._check_cancel()

    def _execute(self, command: list[str], timeout: float):
        self._check_cancel()
        if self.cancel is None:
            return subprocess.run(command, capture_output=True, timeout=timeout, check=False,
                                  **_background_options())
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   **_background_options())
        deadline = time.monotonic() + timeout
        try:
            while True:
                self._check_cancel()
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=min(0.1, remaining))
                    self._check_cancel()
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()

    def _execution_error(self, exc: Exception) -> AdbError:
        if isinstance(exc, PermissionError):
            return AdbError(f"ADB 无法执行: {self.adb_path}。请确认路径指向 adb.exe，完整解压安装包，并检查文件是否被安全软件阻止。原始错误: {exc}")
        return AdbError(f"ADB 执行失败: {self.adb_path}: {exc}")

    def _run(self, *args: str, timeout: float = 30, binary: bool = False) -> str | bytes:
        command = [self.adb_path, "-s", self.serial, *args]
        try:
            result = self._execute(command, timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise self._execution_error(exc) from exc
        if result.returncode:
            message = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
            raise AdbError(f"ADB 返回错误: {message}")
        return result.stdout if binary else result.stdout.decode("utf-8", errors="replace").strip()

    def start_emulator(self) -> None:
        self._check_cancel()
        if not self.emulator_path:
            return
        path = Path(self.emulator_path).expanduser()
        if not path.is_file():
            raise AdbError(f"模拟器程序不存在: {path}")
        self._owned_process = subprocess.Popen([str(path), *self.emulator_args], cwd=str(path.parent),
                                                **_background_options())
        self._wait(self.launch_delay)

    def stop_emulator(self) -> None:
        if self._owned_process and self._owned_process.poll() is None:
            self._owned_process.terminate()
            try:
                self._owned_process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._owned_process.kill()

    def connect(self) -> None:
        if self.serial.lower() in {"auto", "自动"}:
            self.serial = self._detect_emulator()
        connect_message = ""
        if ":" in self.serial:
            try:
                result = self._execute([self.adb_path, "connect", self.serial], 15)
                connect_message = (result.stdout + result.stderr).decode("utf-8", errors="replace").strip()
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise self._execution_error(exc) from exc
        try:
            state = self._run("get-state", timeout=15)
        except AdbError as exc:
            # Older packages shipped this placeholder, which is not MuMu's actual port.
            if self.serial in {"127.0.0.1:5555", "localhost:5555"}:
                self.serial = self._detect_emulator()
                state = self._run("get-state", timeout=15)
            else:
                raise AdbError(f"无法连接设备 {self.serial}: {connect_message or exc}") from exc
        if state != "device":
            raise AdbError(f"设备未就绪: {self.serial} ({state})")

    def _detect_emulator(self) -> str:
        try:
            result = self._execute([self.adb_path, "devices"], 15)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise self._execution_error(exc) from exc
        if result.returncode:
            raise AdbError(result.stderr.decode("utf-8", errors="replace").strip())
        devices = []
        for line in result.stdout.decode("utf-8", errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1] == "device" and (
                    fields[0].startswith(("127.0.0.1:", "localhost:", "emulator-"))):
                devices.append(fields[0])
        if len(devices) == 1:
            return devices[0]
        if devices:
            raise AdbError("检测到多个模拟器，请填写要使用的设备地址: " + "、".join(devices))
        raise AdbError("未检测到已连接的模拟器，请开启模拟器 ADB 调试，并填写模拟器设置中显示的实际 ADB 地址")

    def wait_until_ready(self, timeout: float = 120) -> None:
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                self.connect()
                return
            except AdbError as exc:
                last_error = exc
                self._wait(min(2, max(0.1, deadline - time.monotonic())))
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
        intent = ("-a", "android.intent.action.MAIN", "-c", "android.intent.category.LAUNCHER")
        errors = []
        # Some emulator images omit monkey. Resolve the installed launcher rather
        # than hardcoding an Activity, which can differ between game channels.
        for resolver in (("cmd", "package"), ("pm",)):
            try:
                output = self._run("shell", *resolver, "resolve-activity", "--brief",
                                   *intent, "-p", self.package)
                component = next((line.strip() for line in reversed(output.splitlines())
                                  if re.fullmatch(re.escape(self.package) + r"/[\w.$]+", line.strip())), None)
                if component is None:
                    errors.append(f"{' '.join(resolver)}: {output}")
                    continue
                output = self._run("shell", "am", "start", *intent, "-n", component)
                # Android tools may print a failure while returning exit code 0.
                if re.search(r"(?im)^\s*(?:error\b|exception\b|securityexception\b)", output):
                    raise AdbError(output)
                return
            except AdbError as exc:
                errors.append(str(exc))
        try:
            output = self._run("shell", "monkey", "-p", self.package,
                               "-c", "android.intent.category.LAUNCHER", "1")
            if re.search(r"(?i)monkey aborted|no activities found|inaccessible or not found", output):
                raise AdbError(output)
        except AdbError as exc:
            errors.append(str(exc))
            raise AdbError(f"无法启动游戏 {self.package}，请确认游戏已安装且包名与渠道一致。"
                           + " 启动详情: " + "；".join(errors)) from exc

    def installed(self) -> bool:
        output = self._run("shell", "pm", "path", self.package)
        return "package:" in output
