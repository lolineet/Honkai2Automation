from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image


@dataclass(frozen=True)
class TextHit:
    text: str
    score: float
    x: int
    y: int
    width: int
    height: int


@lru_cache(maxsize=1)
def _engine():
    try:
        from rapidocr import RapidOCR
    except ImportError as exc:
        raise RuntimeError("OCR 未安装，请执行 pip install -e .") from exc
    return RapidOCR()


def read_text(png: bytes, region: tuple[float, float, float, float] = (0, 0, 1, 1),
              *, min_score: float = 0.75) -> list[TextHit]:
    with Image.open(io.BytesIO(png)) as image:
        width, height = image.size
        left, top = round(region[0] * width), round(region[1] * height)
        right, bottom = round(region[2] * width), round(region[3] * height)
        crop = image.convert("RGB").crop((left, top, right, bottom))
    result = _engine()(crop)
    hits = []
    texts = result.txts if result.txts is not None else []
    boxes = result.boxes if result.boxes is not None else []
    scores = result.scores if result.scores is not None else []
    for value, box, score in zip(texts, boxes, scores):
        if float(score) < min_score:
            continue
        xs = [int(point[0]) for point in box]
        ys = [int(point[1]) for point in box]
        hits.append(TextHit(str(value).strip(), float(score), left + min(xs), top + min(ys),
                            max(xs) - min(xs), max(ys) - min(ys)))
    return hits


def find_text(hits: list[TextHit], needle: str) -> TextHit | None:
    needle = re.sub(r"\s+", "", needle).casefold()
    return next((hit for hit in hits if needle in re.sub(r"\s+", "", hit.text).casefold()), None)


def integer_after(hits: list[TextHit], label: str) -> int | None:
    for hit in hits:
        text = re.sub(r"\s+", "", hit.text)
        match = re.search(re.escape(label) + r"[^0-9]*(\d+)", text)
        if match:
            return int(match.group(1))
        if label in text:
            neighbors = sorted((other for other in hits if other is not hit and
                                -30 <= other.x - (hit.x + hit.width) < 110 and
                                abs(other.y - hit.y) < 28), key=lambda other: other.x)
            if neighbors:
                match = re.match(r"\D*(\d+)", neighbors[0].text)
                if match:
                    return int(match.group(1))
    return None


def read_stamina_hud(png: bytes) -> list[TextHit]:
    """Enlarge the small HUD with padding, preserving 1600×900 coordinates."""
    left, top, right, bottom = 280, 72, 500, 140
    scale = 3
    with Image.open(io.BytesIO(png)) as image:
        crop = image.convert("RGB").crop((left, top, right, bottom))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        crop.save(output, format="PNG")
    return [TextHit(hit.text, hit.score, left + hit.x // scale, top + hit.y // scale,
                    hit.width // scale, hit.height // scale)
            for hit in read_text(output.getvalue(), min_score=0.85)]


def _stamina_value(hits: list[TextHit], *, doubled: bool) -> int | None:
    hud = [hit for hit in hits if 280 <= hit.x < 500 and 72 <= hit.y < 140]
    # Sometimes OCR splits the pink label into "双倍" and "体力".
    # A frame with any double label must never be ordinary stamina.
    is_double = any("双倍" in hit.text for hit in hud)
    if is_double != doubled or not any("体力" in hit.text for hit in hud):
        return None
    values = []
    labels = [hit for hit in hud if "体力" in hit.text]
    for hit in hud:
        text = re.sub(r"\s+", "", hit.text)
        match = re.fullmatch(r"(?:(?:双倍)?体力[:：]?)?(\d{1,4})/(\d{1,4})", text)
        if match and int(match.group(2)) > 0 and any(
                abs(label.y - hit.y) <= 20 and 0 <= hit.x - label.x < 160 for label in labels):
            values.append(int(match.group(1)))
    return values[0] if len(values) == 1 else None


def stamina(hits: list[TextHit]) -> int | None:
    return _stamina_value(hits, doubled=False)


def double_stamina(hits: list[TextHit]) -> int | None:
    return _stamina_value(hits, doubled=True)


def stage_labels(png: bytes) -> list[TextHit]:
    """Read orange stage numbers on a colorful map in 1600×900 space."""
    import cv2
    import numpy as np

    with Image.open(io.BytesIO(png)) as image:
        image = image.convert("RGB").resize((1600, 900))
        rgb = np.asarray(image)[190:760, 170:1450]
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    orange = cv2.inRange(hsv, (5, 130, 130), (30, 255, 255))
    prepared = cv2.resize(255 - orange, None, fx=2, fy=2,
                          interpolation=cv2.INTER_NEAREST)
    ok, encoded = cv2.imencode(".png", prepared)
    if not ok:
        return []
    hits = read_text(encoded.tobytes(), min_score=0.3)
    return [TextHit(hit.text, hit.score, 170 + hit.x // 2, 190 + hit.y // 2,
                    hit.width // 2, hit.height // 2) for hit in hits]
