import re

from .ocr import TextHit


class EquipmentFull(RuntimeError):
    def __init__(self):
        super().__init__("装备仓库已满，请整理装备后重新运行")


def check_equipment_capacity(hits: list[TextHit]) -> None:
    words = ''.join(re.sub(r'\s+', '', hit.text) for hit in hits
                    if 180 <= hit.x <= 1400 and 130 <= hit.y <= 800)
    if '装备' not in words:
        return
    if any(text in words for text in ('装备已满', '装备已经满', '装备已达到上限')):
        raise EquipmentFull()
    if any(text in words for text in ('未满', '未达到上限', '未达上限')):
        return
    inventory = any(text in words for text in ('仓库', '背包', '装备数量', '持有装备', '装备栏', '装备空间'))
    full = any(text in words for text in ('已满', '已达上限', '达到上限', '超过上限', '空间不足', '容量不足', '格子不足'))
    if inventory and full:
        raise EquipmentFull()
