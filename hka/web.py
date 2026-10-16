from __future__ import annotations

import json
import copy
import hashlib
import os
import socket
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .adb import AdbDevice
from .config import ConfigError, load_config, save_config, validate_config
from .runner import TaskRunner
from .vision import crop_template, template_path
from .mail_credentials import protect_secret
from .notify import email_options, send_email


class LocalServer(ThreadingHTTPServer):
    def server_bind(self):
        if os.name == "nt":
            # Windows SO_REUSEADDR allows unrelated instances to share requests.
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def config_revision(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def config_snapshot(path: Path) -> dict:
    raw = path.read_bytes()
    data = json.loads(raw.decode("utf-8"))
    validate_config(data)
    email = data.get('notifications', {}).get('email', {})
    email['has_authorization_code'] = bool(email.pop('authorization_code_encrypted', ''))
    email.pop('authorization_code', None)
    data["_revision"] = hashlib.sha256(raw).hexdigest()
    return data


def save_config_snapshot(path: Path, data: dict) -> str:
    value = copy.deepcopy(data)
    if value.pop("_revision", None) != config_revision(path):
        raise ConfigError("配置已更新，请刷新页面后再保存，避免旧页面覆盖新流程")
    value.setdefault("runtime", {})["scheduler_enabled"] = False
    email = value.setdefault('notifications', {}).setdefault('email', {})
    current = load_config(path).get('notifications', {}).get('email', {})
    secret = email.pop('authorization_code', '').strip()
    email.pop('has_authorization_code', None)
    email.pop('authorization_code_encrypted', None)
    if secret:
        email['authorization_code_encrypted'] = protect_secret(secret)
    elif all(email.get(key, '') == current.get(key, '') for key in ('qq_number', 'username', 'host', 'provider')):
        if current.get('authorization_code_encrypted'):
            email['authorization_code_encrypted'] = current['authorization_code_encrypted']
    if email.get('enabled'):
        email_options(email)
    save_config(path, value)
    return config_revision(path)


class WebState:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.lock = threading.Lock()
        self.running = False
        self.cancel = threading.Event()
        self.logs: list[str] = []
        self.result: dict | None = None
        self.last_screenshot: bytes | None = None

    def log(self, message: str) -> None:
        with self.lock:
            self.logs.append(message)
            self.logs = self.logs[-300:]

    def start_run(self, tasks: list[str] | None, force: bool) -> None:
        with self.lock:
            if self.running:
                raise RuntimeError("任务正在运行")
            self.running = True
            self.cancel = threading.Event()
            self.logs = []
            self.result = None

        def worker() -> None:
            try:
                config = load_config(self.config_path)
                result = TaskRunner(config, self.config_path, log=self.log, cancel=self.cancel).run(
                    task_ids=set(tasks) if tasks else None, force=force)
                with self.lock:
                    self.result = result
            except Exception as exc:
                self.log(f"运行失败: {exc}")
                with self.lock:
                    self.result = {"status": "failed", "setup_error": str(exc)}
            finally:
                with self.lock:
                    self.running = False

        threading.Thread(target=worker, daemon=True).start()


def serve(config_path: Path, port: int = 8765) -> None:
    state = WebState(config_path)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, value: object, status: int = 200) -> None:
            payload = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _bytes(self, payload: bytes, mime: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self) -> None:
            route = urlparse(self.path).path
            try:
                if route == "/":
                    return self._bytes((Path(__file__).parent / "ui" / "index.html").read_bytes(), "text/html; charset=utf-8")
                if route == "/api/config":
                    return self._json(config_snapshot(config_path))
                if route == "/api/plan":
                    runner = TaskRunner(load_config(config_path), config_path)
                    return self._json({"date": runner.today.isoformat(), "items": runner.preview()})
                if route == "/api/status":
                    with state.lock:
                        return self._json({"running": state.running, "logs": state.logs, "result": state.result})
                if route == "/api/screenshot":
                    config = load_config(config_path)
                    device = AdbDevice(config["emulator"])
                    device.connect()
                    screenshot = device.screen()
                    with state.lock:
                        state.last_screenshot = screenshot
                    return self._bytes(screenshot, "image/png")
                if route == "/api/templates":
                    config = load_config(config_path)
                    directory = config_path.parent / config.get("runtime", {}).get("template_dir", "templates")
                    return self._json(sorted(p.name for p in directory.glob("*.png")) if directory.is_dir() else [])
                self._json({"error": "未找到页面"}, 404)
            except Exception as exc:
                self._json({"error": str(exc)}, 400)

        def do_POST(self) -> None:
            route = urlparse(self.path).path
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > 2_000_000:
                    raise ValueError("请求太大")
                data = json.loads(self.rfile.read(length) or b"{}")
                if route == "/api/config":
                    with state.lock:
                        if state.running:
                            raise RuntimeError("运行中不能修改配置")
                        revision = save_config_snapshot(config_path, data)
                    return self._json({"ok": True, "revision": revision})
                if route == "/api/doctor":
                    config = load_config(config_path)
                    device = AdbDevice(config["emulator"])
                    device.connect()
                    return self._json({"serial": device.serial, "package": device.package, "installed": device.installed(), "size": device.size()})
                if route == '/api/test-email':
                    email = load_config(config_path).get('notifications', {}).get('email', {})
                    send_email(email, title='HKA 测试邮件', body='邮件通知配置成功。装备仓库已满或任务失败时，将按通知设置发送提醒。')
                    return self._json({'ok': True})
                if route == "/api/run":
                    state.start_run(data.get("tasks"), bool(data.get("force", False)))
                    return self._json({"ok": True})
                if route == "/api/stop":
                    state.cancel.set()
                    state.log("收到停止请求，正在中断当前任务")
                    return self._json({"ok": True})
                if route == "/api/tap":
                    with state.lock:
                        if state.running:
                            raise RuntimeError("运行中不能手动点击")
                    x, y = float(data["x"]), float(data["y"])
                    if not 0 <= x <= 1 or not 0 <= y <= 1:
                        raise ValueError("点击坐标必须在画面内")
                    device = AdbDevice(load_config(config_path)["emulator"])
                    device.connect()
                    width, height = device.size()
                    device.tap(round(x * width), round(y * height))
                    return self._json({"ok": True})
                if route == "/api/template":
                    config = load_config(config_path)
                    directory = (config_path.parent / config.get("runtime", {}).get("template_dir", "templates")).resolve()
                    path = template_path(directory, data["name"])
                    with state.lock:
                        screenshot = state.last_screenshot
                    if not screenshot:
                        raise ValueError("请先获取模拟器截图")
                    size = crop_template(screenshot, data["box"], path)
                    return self._json({"ok": True, "name": path.name, "size": size})
                self._json({"error": "未找到接口"}, 404)
            except Exception as exc:
                self._json({"error": str(exc)}, 400)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = LocalServer(("127.0.0.1", port), Handler)
    address = f"http://127.0.0.1:{port}/"
    print(f"配置界面: {address}")
    if os.getenv("HKA_NO_BROWSER") != "1":
        webbrowser.open(address)
    try:
        server.serve_forever()
    finally:
        server.server_close()
