from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

from .adb import AdbDevice
from .config import ConfigError, load_config, save_config
from .defaults import DEFAULT_CONFIG
from .runner import TaskRunner


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="hka", description="崩坏学园2每日任务自动化")
    parser.add_argument("--config", default="config.json", help="配置文件路径")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="生成初始配置")
    sub.add_parser("doctor", help="检查模拟器连接")
    sub.add_parser("plan", help="显示今天待运行的任务")
    run = sub.add_parser("run", help="执行任务")
    run.add_argument("--task", action="append", help="只运行指定任务 ID，可重复")
    run.add_argument("--force", action="store_true", help="忽略日期和已执行记录")
    capture = sub.add_parser("capture", help="保存模拟器截图")
    capture.add_argument("--output", default="capture.png")
    daemon = sub.add_parser("daemon", help="每天定时运行，使用电脑本地时间")
    daemon.add_argument("--at", required=True, help="执行时间 HH:MM")
    serve = sub.add_parser("serve", help="打开本地配置界面")
    serve.add_argument("--port", type=int, default=8765)
    args = parser.parse_args(argv)
    path = Path(args.config).resolve()
    try:
        if args.command == "init":
            if path.exists():
                raise ConfigError(f"配置已经存在: {path}")
            save_config(path, DEFAULT_CONFIG)
            (path.parent / "templates").mkdir(exist_ok=True)
            print(f"已创建 {path}")
            return 0
        config = load_config(path)
        if args.command == "doctor":
            device = AdbDevice(config["emulator"])
            device.connect()
            print(f"ADB 已连接: {device.serial}")
            print(f"游戏安装: {'是' if device.installed() else '否'} ({device.package})")
            print(f"截图尺寸: {device.size()}")
            return 0
        if args.command == "plan":
            tasks = TaskRunner(config, path).plan()
            print("\n".join(f"{item['id']}: {item['name']}" for item in tasks) or "当前没有待执行任务")
            return 0
        if args.command == "capture":
            device = AdbDevice(config["emulator"])
            device.connect()
            target = Path(args.output).resolve()
            target.write_bytes(device.screen())
            print(f"截图已保存: {target}")
            return 0
        if args.command == "run":
            result = TaskRunner(config, path).run(task_ids=set(args.task) if args.task else None, force=args.force)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 1 if result["status"] == "failed" else 0
        if args.command == "daemon":
            try:
                hour, minute = (int(part) for part in args.at.split(":"))
                if not (0 <= hour <= 23 and 0 <= minute <= 59):
                    raise ValueError()
            except ValueError as exc:
                raise ConfigError("--at 时间格式必须是 HH:MM") from exc
            print(f"定时任务已启动，每天 {hour:02d}:{minute:02d} 运行，按 Ctrl+C 停止")
            while True:
                now = datetime.now()
                target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
                if target <= now:
                    target += timedelta(days=1)
                while (target - datetime.now()).total_seconds() > 0:
                    time.sleep(min(30, max(0.1, (target - datetime.now()).total_seconds())))
                config = load_config(path)
                TaskRunner(config, path).run()
        if args.command == "serve":
            from .web import serve as web_serve
            web_serve(path, args.port)
            return 0
    except (ConfigError, OSError, RuntimeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("已停止")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
