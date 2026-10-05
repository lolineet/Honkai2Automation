from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image


class VisionError(RuntimeError):
    pass


def template_path(template_dir: Path, name: str) -> Path:
    if not name or Path(name).name != name or Path(name).suffix.lower() != ".png":
        raise VisionError("模板名称必须是 templates 目录下的 PNG 文件名")
    return template_dir / name


def find_image(screenshot: bytes, template_dir: Path, step: dict[str, Any]) -> tuple[int, int, float] | None:
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise VisionError("缺少图像识别依赖，请运行 pip install -e .") from exc
    path = template_path(template_dir, step["image"])
    if not path.is_file():
        raise VisionError(f"缺少截图模板: {path}")
    screen = cv2.imdecode(np.frombuffer(screenshot, dtype=np.uint8), cv2.IMREAD_COLOR)
    template = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if screen is None or template is None:
        raise VisionError(f"无法读取截图或模板: {path}")
    height, width = screen.shape[:2]
    region = step.get("region", [0, 0, 1, 1])
    if not isinstance(region, list) or len(region) != 4 or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in region):
        raise VisionError("region 必须是 [左, 上, 右, 下] 的比例坐标")
    x1, y1, x2, y2 = int(region[0] * width), int(region[1] * height), int(region[2] * width), int(region[3] * height)
    if x1 >= x2 or y1 >= y2:
        raise VisionError("region 范围无效")
    crop = screen[y1:y2, x1:x2]
    th, tw = template.shape[:2]
    if th > crop.shape[0] or tw > crop.shape[1]:
        return None
    score_map = cv2.matchTemplate(crop, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, point = cv2.minMaxLoc(score_map)
    threshold = float(step.get("threshold", 0.88))
    if not 0 <= threshold <= 1:
        raise VisionError("threshold 必须在 0 到 1 之间")
    if score < threshold:
        return None
    return x1 + point[0] + tw // 2, y1 + point[1] + th // 2, float(score)


def crop_template(screenshot: bytes, box: list[float], path: Path) -> tuple[int, int]:
    if len(box) != 4 or any(not isinstance(v, (int, float)) or not 0 <= v <= 1 for v in box):
        raise VisionError("裁剪坐标必须在 0 到 1 之间")
    with Image.open(io.BytesIO(screenshot)) as image:
        width, height = image.size
        coords = (round(box[0] * width), round(box[1] * height), round(box[2] * width), round(box[3] * height))
        if coords[2] - coords[0] < 8 or coords[3] - coords[1] < 8:
            raise VisionError("模板区域至少需要 8×8 像素")
        cropped = image.crop(coords).convert("RGB")
        path.parent.mkdir(parents=True, exist_ok=True)
        cropped.save(path)
        return cropped.size
