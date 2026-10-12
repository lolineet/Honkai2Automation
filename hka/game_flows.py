"""Screen driven 崩坏学园2 flows for the 1600×900 landscape layout.

Every operation that can spend premium currency or stamina checks the visible
screen again immediately before its final confirmation. Unknown UI states stop.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from PIL import Image, ImageChops, ImageStat

from .ocr import TextHit, double_stamina, find_text, integer_after, read_stamina_hud, read_text, stage_labels, stamina
from .runner import SkipTask
from .vision import find_image


class GameFlows:
    def __init__(self, runner: Any):
        self.runner = runner
        self.device = runner.device
        self.log = runner.log
        self.sleep = runner.sleep

    def _check_cancel(self) -> None:
        if self.runner.cancel.is_set():
            from .runner import Cancelled
            raise Cancelled()

    def _tap(self, x: int, y: int, wait: float = 0.65) -> None:
        self._check_cancel()
        width, height = self.runner._size()
        self.device.tap(round(x * width / 1600), round(y * height / 900))
        self.sleep(wait)

    def _swipe(self, x1: int, y1: int, x2: int, y2: int,
               duration_ms: int = 450, wait: float = 0.55) -> None:
        self._check_cancel()
        width, height = self.runner._size()
        self.device.swipe(round(x1 * width / 1600), round(y1 * height / 900),
                          round(x2 * width / 1600), round(y2 * height / 900), duration_ms)
        self.sleep(wait)

    def _screen(self) -> bytes:
        self._check_cancel()
        png = self.device.screen()
        with Image.open(io.BytesIO(png)) as screen:
            if abs(screen.width / screen.height - 1600 / 900) > 0.03:
                raise RuntimeError("游戏流程需要横屏 16:9 画面")
            if screen.size == (1600, 900):
                return png
            output = io.BytesIO()
            screen.convert("RGB").resize((1600, 900)).save(output, format="PNG")
            return output.getvalue()

    def _hits(self, box: tuple[int, int, int, int] = (0, 0, 1600, 900),
              *, score: float = 0.75) -> list[TextHit]:
        self._check_cancel()
        return read_text(self._screen(), tuple(v / (1600 if i % 2 == 0 else 900)
                                                      for i, v in enumerate(box)), min_score=score)

    def _text(self, needle: str, box: tuple[int, int, int, int] = (0, 0, 1600, 900)) -> TextHit | None:
        return find_text(self._hits(box), needle)

    def _click(self, needle: str, box: tuple[int, int, int, int] = (0, 0, 1600, 900),
               *, missing: str = "fail") -> bool:
        hit = None
        for _ in range(4):
            hit = self._text(needle, box)
            if hit is not None:
                break
            self.sleep(0.6)
        if hit is None:
            if missing == "skip":
                raise SkipTask(f"当前页面未找到“{needle}”")
            if missing == "continue":
                return False
            raise RuntimeError(f"当前页面未找到“{needle}”")
        self._tap(hit.x + hit.width // 2, hit.y + hit.height // 2)
        return True

    def _dismiss_navigation_notice(self, hits: list[TextHit]) -> bool:
        rewards = find_text(hits, "已领取以下奖励")
        if rewards and rewards.y < 150:
            self.log("关闭已领取奖励展示")
            self._tap(1450, 740, 1.0)
            return True
        dialog = [h for h in hits if 480 <= h.x <= 1100 and 240 <= h.y <= 650]
        words = "".join(h.text for h in dialog)
        # Only this informational notice is allowed to use its confirm button.
        # Never confirm an arbitrary dialog while recovering navigation.
        if all(word in words for word in ("提示", "未领取", "活动奖励", "背包")):
            confirm = next((h for h in dialog if h.text.strip() == "确定" and
                            650 <= h.x <= 900 and 535 <= h.y <= 640), None)
            if confirm:
                self.log("关闭活动奖励送达背包提示")
                self._tap(confirm.x + confirm.width // 2, confirm.y + confirm.height // 2, 1.0)
                return True
        return False

    def _dismiss_navigation_card(self) -> bool:
        screenshot = self._screen()
        for image, region, label in (
                ("navigation_close.png", [0.65, 0.08, 0.95, 0.5], "活动宣传卡片"),
                ("navigation_announcement_close.png", [0.8, 0, 1, 0.25], "游戏公告")):
            close = find_image(screenshot, Path(__file__).parent / "assets", {
                "image": image, "region": region, "threshold": 0.94})
            if close:
                self.log(f"关闭遮挡导航的{label}")
                self._tap(close[0], close[1], 1.0)
                return True
        return False

    def _home(self) -> None:
        for _ in range(8):
            hits = self._hits()
            modal = [h for h in hits if 300 <= h.x <= 1300 and 220 <= h.y <= 730]
            words = "".join(h.text for h in modal)
            if find_text(modal, "取消") and "体力" in words and any(
                    token in words for token in ("水晶", "钻石", "双倍体力", "将消耗")):
                self._cancel_exchange(modal, "返回首页前关闭体力兑换提示")
                continue
            if self._dismiss_navigation_notice(hits) or self._dismiss_navigation_card():
                continue
            hit = next((hit for hit in hits if "首页" in hit.text and
                        750 <= hit.x <= 890 and 70 <= hit.y < 130), None)
            if hit:
                self._tap(hit.x + hit.width // 2, hit.y + hit.height // 2, 2.5)
                return
            self.sleep(0.6)
        raise RuntimeError("未确认顶部首页入口，已停止导航")

    def _shop(self) -> None:
        self._home()
        self._tap(1240, 68, 1.5)
        for _ in range(5):
            if self._text("礼包商店", (160, 145, 1440, 410)):
                return
            self.sleep(0.7)
        raise RuntimeError("未进入商店首页")

    def enter_game(self, options: dict[str, Any]) -> None:
        entered = False
        for _ in range(45):
            hits = self._hits()
            if any("首页" in hit.text and 750 <= hit.x <= 900 and hit.y < 135 for hit in hits):
                self.log("已进入游戏")
                return
            if find_text(hits, "选择服务器"):
                previous = find_text(hits, "上次登录")
                if previous is None:
                    raise RuntimeError("没有上次登录服务器，请先在模拟器选择一次服务器")
                enter = next((hit for hit in hits if hit.text.strip() == "进入" and
                              hit.x > 1000 and 0 <= hit.y - previous.y < 150), None)
                if enter is None:
                    raise RuntimeError("未找到上次登录服务器的进入按钮")
                if not entered:
                    self._tap(enter.x + enter.width // 2, enter.y + enter.height // 2, 2)
                    entered = True
            elif find_text(hits, "验证码") or find_text(hits, "账号密码"):
                raise RuntimeError("游戏需要登录验证，请在模拟器完成后再运行")
            elif find_text(hits, "屏幕继续"):
                self._tap(800, 835, 2)
            self.sleep(1)
        raise RuntimeError("进入游戏超时，请检查模拟器中的登录或加载提示")

    def refresh_resonance(self, options: dict[str, Any]) -> None:
        self._shop()
        self._click("崩坏屋", (1000, 175, 1450, 410))
        self._click("共鸣屋", (0, 240, 225, 520))
        hits = self._hits((1010, 145, 1410, 235))
        remaining = integer_after(hits, "剩余次数")
        if remaining is None or remaining <= 0:
            raise SkipTask("共鸣屋免费刷新次数为零或无法识别，已跳过")
        self.log(f"共鸣屋剩余刷新次数 {remaining}")
        self._tap(1360, 190)
        dialog = self._hits((450, 220, 1150, 670))
        words = "".join(hit.text for hit in dialog)
        if not ("免费刷新" in words and "确定" in words):
            raise RuntimeError("无法核对刷新确认框，未确认")
        if re.search(r"(?:消耗|花费|水晶|共鸣)[^。]{0,12}\d", words):
            raise RuntimeError("刷新确认框疑似提示付费，未确认")
        hit = next((item for item in dialog if item.text.strip() == "确定" and item.y > 500), None)
        if hit is None:
            raise RuntimeError("未找到免费刷新确认按钮")
        self._tap(hit.x + hit.width // 2, hit.y + hit.height // 2, 1.0)
        after = integer_after(self._hits((1010, 145, 1410, 235)), "剩余次数")
        if after is None or after >= remaining:
            raise RuntimeError("刷新后剩余次数未减少，无法确认刷新成功")

    def free_gift(self, options: dict[str, Any]) -> None:
        self._shop()
        self._click("礼包商店", (420, 165, 860, 410))
        self._click("吼美礼包", (0, 245, 230, 550))
        card_hits: list[TextHit] = []
        for _ in range(8):
            card_hits = self._hits((230, 180, 1500, 820))
            title = find_text(card_hits, "零时馈礼")
            if title and title.y <= 380:
                break
            if title:
                # A title at the bottom belongs to a partly hidden card. Bring
                # its price and purchase button into view before checking it.
                self._swipe(900, 775, 900, 775 - min(475, title.y - 260))
                continue
            self._swipe(900, 775, 900, 300)
        title = find_text(card_hits, "零时馈礼")
        if title is None or title.y > 380:
            raise SkipTask("未找到零时馈礼")
        card_x = title.x
        card = [hit for hit in card_hits if abs(hit.x - card_x) < 250 and title.y <= hit.y < title.y + 440]
        price = next((hit for hit in card if re.fullmatch(r"[×xX*]\s*0", hit.text.strip())), None)
        buy = find_text(card, "购买")
        if price is None or buy is None:
            raise SkipTask("零时馈礼价格不是 0，或已售完")
        self.log("已确认零时馈礼价格为 0")
        self._tap(buy.x + buy.width // 2, buy.y + buy.height // 2)
        dialog = self._hits((500, 230, 1080, 630))
        if not find_text(dialog, "零时馈礼"):
            raise RuntimeError("购买确认框不是零时馈礼，未确认")
        candidates = [hit for hit in dialog if "购买" in hit.text and hit.y > 490]
        if not candidates:
            raise RuntimeError("未找到零时馈礼确认按钮")
        hit = min(candidates, key=lambda h: h.x)
        self._tap(hit.x + hit.width // 2, hit.y + hit.height // 2, 0.9)
        if not self._text("购买成功", (490, 220, 1110, 650)):
            raise RuntimeError("未看到零时馈礼购买成功提示")
        self._tap(800, 555)

    def _battle_picker(self) -> list[TextHit]:
        self._home()
        self._tap(955, 70, 1.5)
        home_retries = 0
        for _ in range(12):
            # The activity picker has three left-hand tabs. Its right-hand
            # artwork occupies the same coordinates as a stage map's reset
            # button, so a blind tap there may enter an unrelated activity.
            tabs = self._hits((35, 175, 185, 490), score=0.6)
            if sum(find_text(tabs, label) is not None
                   for label in ("主线", "活动", "挑战")) >= 2:
                return self._hits((180, 160, 1550, 875))
            if self._dismiss_navigation_notice(self._hits()) or self._dismiss_navigation_card():
                continue
            screenshot = self._screen()
            reset = find_image(screenshot, Path(__file__).parent / "assets", {
                "image": "battle_reset.png", "region": [0.8, 0.16, 1, 0.28], "threshold": 0.94})
            if reset:
                self._tap(reset[0], reset[1], 0.8)
                continue
            home = find_image(screenshot, Path(__file__).parent / "assets", {
                "image": "home_active.png", "region": [0.47, 0, 0.56, 0.15], "threshold": 0.94})
            if home and home_retries < 3:
                battle = self._text("战斗", (900, 0, 1020, 135))
                if battle:
                    home_retries += 1
                    self.log(f"仍停留首页，重新进入战斗页面（{home_retries}/3）")
                    self._tap(battle.x + battle.width // 2, battle.y + battle.height // 2, 1.5)
                    continue
            self.sleep(0.7)
        raise RuntimeError("未确认战斗关卡选择页面，已停止以避免误入其他活动")

    def _rift(self) -> None:
        self._battle_picker()
        for _ in range(5):
            hits = self._hits((925, 180, 1535, 875))
            hit = find_text(hits, "多元裂缝")
            if hit:
                self._tap(hit.x + hit.width // 2, hit.y + hit.height // 2, 1.0)
                return
            self._swipe(1290, 760, 1290, 320)
        raise RuntimeError("活动列表未找到多元裂缝")

    def _open_quick_detail(self, stage: str) -> None:
        self._rift()
        if stage == "使魔的爱":
            hit = self._text(stage, (570, 160, 990, 740))
            if hit is None:
                raise RuntimeError("多元裂缝未找到使魔的爱入口")
            # The caption is above the clickable stage icon.
            self._tap(hit.x + hit.width // 2, hit.y + hit.height + 75, 1.2)
        else:
            self._click(stage, (570, 160, 1520, 740))
            if not self._text("虚轴之庭", (1290, 150, 1580, 240)):
                raise RuntimeError("未确认虚轴之庭地图，未点击出击")
            # The blue triangle prints 出/击 vertically, which OCR may split.
            self._tap(850, 660)
    def quick_battle(self, options: dict[str, Any]) -> dict[str, Any]:
        stage = str(options.get("stage", "使魔的爱"))
        if stage not in {"使魔的爱", "虚轴之庭"}:
            raise ValueError("快捷战斗 stage 仅支持使魔的爱或虚轴之庭")
        operations = battles = 0
        expected_remaining = None
        for _ in range(12):
            self._open_quick_detail(stage)
            allowance = self._quick_allowance(stage, allow_zero=operations > 0)
            if expected_remaining is not None and allowance != expected_remaining:
                raise RuntimeError("免费次数未按确认的战斗次数减少，已停止后续快捷战斗")
            if allowance == 0:
                self.log(f"{stage}免费次数已用完，共确认 {operations} 次快捷战斗、完成 {battles} 场")
                return {"operations": operations, "battles": battles, "remaining_free": 0}
            count = self._execute_quick(stage, allowance)
            operations += 1
            battles += count
            expected_remaining = allowance - count
            if stage == "使魔的爱":
                return {"operations": operations, "battles": battles,
                        "remaining_free": expected_remaining}
        raise RuntimeError("虚轴之庭快捷战斗超过安全上限，已停止")

    def _execute_quick(self, stage: str, allowance: int) -> int:
        self._click("快捷战斗", (1040, 740, 1440, 870))
        previous_count = None
        for _ in range(3):
            dialog = self._hits((600, 300, 1010, 620))
            if not find_text(dialog, "快速战斗") and not find_text(dialog, "快捷战斗"):
                raise RuntimeError("无法核对快捷战斗确认框")
            counts = [int(hit.text) for hit in dialog if 465 <= hit.y <= 515 and
                      670 <= hit.x <= 810 and re.fullmatch(r"\d{1,2}", hit.text)]
            if len(counts) != 1 or not 1 <= counts[0] <= 3:
                raise RuntimeError("无法识别快捷战斗场数，未确认")
            if previous_count is not None and counts[0] != previous_count - 1:
                raise RuntimeError("快捷战斗场数调整未生效，未确认")
            if counts[0] <= allowance:
                break
            previous_count = counts[0]
            self._tap(641, 492)
        else:
            raise RuntimeError("快捷战斗次数超出免费次数，未确认")
        values = [int(hit.text) for hit in sorted(dialog, key=lambda hit: hit.x)
                  if 385 <= hit.y <= 455 and re.fullmatch(r"\d{1,4}", hit.text)]
        if len(values) != 2 or len(counts) != 1 or not 1 <= counts[0] <= allowance:
            raise RuntimeError("无法确认快捷战斗次数与体力变化，未确认")
        expected_cost = counts[0] * 30 if stage == "使魔的爱" else 0
        if values[0] < expected_cost or values[0] - values[1] != expected_cost:
            raise RuntimeError("快捷战斗体力消耗与预期不符，未确认")
        self._click("确定", (600, 535, 800, 640))
        for _ in range(12):
            self.sleep(0.6)
            if self._text("战斗结算", (680, 125, 1340, 210)):
                self._click("确定", (840, 700, 1150, 825))
                return counts[0]
        raise RuntimeError("快捷战斗后未识别到结算")

    def _quick_allowance(self, stage: str, *, allow_zero: bool = False) -> int:
        label = "今日剩余次数" if stage == "使魔的爱" else "今日免费"
        for _ in range(8):
            hits = self._hits((735, 145, 1450, 850))
            if stage == "使魔的爱":
                detail = any(stage in hit.text and hit.y < 230 for hit in hits)
            else:
                # Axis details are titled with the rotating character's name,
                # not the map name. Require the detail controls and progress
                # labels; the dimmed map name behind the panel is unreliable.
                detail = all(find_text(hits, text) is not None for text in
                             ("进度", "已参与次数", "选择助战好友", "快捷战斗"))
            remaining = integer_after(hits, label) if detail else None
            if stage == "虚轴之庭" and remaining is None and self._axis_exhausted(hits):
                # Once the last free stage is complete, the game removes both
                # the free-count label and quick-battle button and shows a paid
                # 35-stamina replay instead. Never enter that replay.
                remaining = 0
            if remaining is not None:
                if remaining == 0 and not allow_zero:
                    raise SkipTask(f"{stage}{'今日次数' if stage == '使魔的爱' else '今日免费次数'}已用完")
                self.log(f"{stage}可用次数 {remaining}")
                return remaining
            self.sleep(0.6)
        raise RuntimeError(f"未确认{stage}详情页或无法识别{label}，未执行快捷战斗")

    @staticmethod
    def _axis_exhausted(hits: list[TextHit]) -> bool:
        return (find_text(hits, "进度") is not None and
                find_text(hits, "选择助战好友") is not None and
                integer_after(hits, "消耗体力") == 35 and
                any(re.search(r"已参与次数\s*[:：]?\s*3\s*/\s*3", h.text) for h in hits) and
                find_text(hits, "今日免费") is None and find_text(hits, "快捷战斗") is None)

    def _activity(self) -> None:
        hits = self._battle_picker()
        # The event artwork moves between releases; use the B difficulty tag and
        # its nearby remaining-time tag to identify a live activity card.
        for _ in range(4):
            difficulty = [h for h in hits if re.search(r"难度指数\s*[:：]?\s*B\b", h.text, re.I)]
            timers = [h for h in hits if "剩余时间" in h.text and
                      any(int(value) > 0 for value in re.findall(r"\d+", h.text.split("剩余时间", 1)[1]))]
            for tag in difficulty:
                if any("空想迷域" in h.text and abs(h.x - tag.x) < 350 and
                       abs(h.y - tag.y) < 200 for h in hits):
                    continue
                if any(abs(tag.x - timer.x) < 340 and abs(tag.y - timer.y) < 65 for timer in timers):
                    self._tap(max(300, tag.x - 190), min(820, tag.y + 115), 1.0)
                    if self._text("空想迷域"):
                        raise SkipTask("空想迷域不属于本次每日副本流程")
                    return
            self._swipe(510, 770, 510, 350)
            hits = self._hits((180, 160, 1550, 875))
        raise SkipTask("未找到难度 B 且仍有剩余时间的活动")

    def _stage(self, name: str) -> None:
        for _ in range(6):
            hits = stage_labels(self._screen())
            hit = next((h for h in hits if re.sub(r"\s+", "", h.text).upper() == name.upper()), None)
            if hit:
                # Stage name is printed immediately above the stage icon.
                self._tap(hit.x + hit.width // 2, hit.y + 110, 1.2)
                if self._stage_detail_visible(name):
                    return
                self.sleep(0.8)
                continue
            page = self._hits()
            page_hit = next((h for h in page if 740 < h.x < 850 and h.y > 780 and
                             re.fullmatch(r"\d+\s*/\s*\d+", h.text)), None)
            if page_hit:
                current, total = map(int, re.findall(r"\d+", page_hit.text))
                stage_number = re.fullmatch(r"(\d+)-\d+", name)
                target_page = total if name == "BONUS" else int(stage_number.group(1)) if stage_number else 1
                if not 1 <= target_page <= total:
                    raise RuntimeError(f"关卡 {name} 所在页超出当前地图范围")
                if current < target_page:
                    self._tap(928, 828, 1.2)
                    continue
                if current > target_page:
                    self._tap(674, 828, 1.2)
                    continue
            self.sleep(0.6)
        raise RuntimeError(f"关卡地图未找到 {name}")

    def _stage_detail_visible(self, name: str) -> bool:
        detail = self._hits((735, 145, 1040, 265))
        return bool(find_text(detail, name) and find_text(detail, "最高得分"))

    def _stamina(self) -> int:
        return self._confirmed_stamina(doubled=False)

    def _double_stamina(self) -> int:
        return self._confirmed_stamina(doubled=True)

    def _confirmed_stamina(self, *, doubled: bool) -> int:
        previous = None
        parse = double_stamina if doubled else stamina
        label = "双倍体力" if doubled else "普通体力"
        for _ in range(20):
            hits = read_stamina_hud(self._screen())
            value = parse(hits)
            # A crossfade can briefly leave only "70" of "270" readable.
            # Missing labels or the other resource interrupt confirmation.
            if value is not None and value == previous:
                raw = " / ".join(h.text for h in hits)
                self.log(f"{label}复核：{value}（连续两帧一致；OCR：{raw}）")
                return value
            previous = value
            self.sleep(0.4)
        raise RuntimeError(f"无法连续确认{label}，未使用本次识别结果（顶部可能正在切换显示）")

    def _exchange_snapshot(self) -> tuple[list[TextHit], bool]:
        frame = self._screen()
        hits = read_text(frame, (300 / 1600, 220 / 900, 1300 / 1600, 730 / 900))
        premium = bool(re.search(r"钻石|水晶|充值|人民币|￥|¥", "".join(h.text for h in hits)))
        premium = premium or find_image(frame, Path(__file__).parent / "assets", {
            "image": "premium_diamond.png", "region": [0.25, 0.25, 0.8, 0.75],
            "threshold": 0.92}) is not None
        return hits, premium

    def _cancel_exchange(self, hits: list[TextHit], reason: str) -> None:
        for _ in range(3):
            cancel = next((h for h in hits if h.text.strip() == "取消" and
                           800 <= h.x <= 1200 and 500 <= h.y <= 700), None)
            if cancel is not None:
                self._tap(cancel.x + cancel.width // 2, cancel.y + cancel.height // 2, 1.0)
            else:
                # Android Back closes an unknown dialog without approving a purchase.
                self._check_cancel()
                self.device.key(4)
                self.sleep(1.0)
            self.log(f"已取消体力兑换：{reason}")
            hits, premium = self._exchange_snapshot()
            if not premium and not any(h.text.strip() == "取消" for h in hits):
                return
            reason = "继续关闭后续体力兑换弹窗"
        raise RuntimeError("体力兑换弹窗未关闭，未确认任何购买")

    @staticmethod
    def _safe_double_exchange(hits: list[TextHit], premium: bool,
                              balance: int) -> tuple[int, int, TextHit] | None:
        if premium or not find_text(hits, "兑换双倍体力"):
            return None
        words = re.sub(r"\s+", "", "".join(h.text for h in sorted(hits, key=lambda h: (h.y, h.x))))
        match = re.search(r"(?:主人)?可兑换(\d+)双倍体力[，,]?补充(\d+)体力", words)
        confirm = next((h for h in hits if h.text.strip() == "兑换" and
                        500 <= h.x <= 800 and 520 <= h.y <= 620), None)
        cancel = next((h for h in hits if h.text.strip() == "取消" and
                       800 <= h.x <= 1200 and 500 <= h.y <= 700), None)
        if match is None or confirm is None or cancel is None:
            return None
        amount, restored = map(int, match.groups())
        if balance < 50 or not 0 < amount <= balance or restored != amount * 2:
            return None
        return amount, restored, confirm

    def _exchange_double_stamina(self) -> None:
        balance = self._double_stamina()
        if balance < 50:
            raise SkipTask("双倍体力不足 50，未补充体力")
        ordinary = self._stamina()
        self._click("补充体力", (725, 745, 1090, 865))
        first_hits, first_premium = self._exchange_snapshot()
        first = self._safe_double_exchange(first_hits, first_premium, balance)
        if first is None:
            reason = "出现钻石或水晶兑换" if first_premium else "无法确认双倍体力兑换资源及数量"
            self._cancel_exchange(first_hits, reason)
            raise SkipTask(reason + "，已关闭兑换")
        # Read a fresh screenshot, including currency icons, immediately before
        # clicking. Never approve a dialog that changed between the two reads.
        self.sleep(0.25)
        second_hits, second_premium = self._exchange_snapshot()
        second = self._safe_double_exchange(second_hits, second_premium, balance)
        if second is None or second[:2] != first[:2] or (
                abs(second[2].x - first[2].x) > 8 or abs(second[2].y - first[2].y) > 8):
            reason = "二次检查发现钻石或水晶兑换" if second_premium else "二次检查的兑换信息与首次不一致"
            self._cancel_exchange(second_hits, reason)
            raise SkipTask(reason + "，已关闭兑换")
        amount, restored, confirm = second
        self._tap(confirm.x + confirm.width // 2, confirm.y + confirm.height // 2, 1.0)
        for _ in range(8):
            hits, premium = self._exchange_snapshot()
            # A second purchase prompt is never confirmed, even if the first
            # prompt correctly identified the free double-stamina resource.
            if premium or any(h.text.strip() == "取消" for h in hits):
                self._cancel_exchange(hits, "兑换后出现额外确认弹窗")
                raise RuntimeError("体力兑换出现额外确认，已取消并停止本局 BONUS")
            if self._text("选择助战好友", (725, 745, 1090, 865)):
                break
            self.sleep(0.6)
        else:
            raise RuntimeError("兑换后未恢复关卡助战入口")
        if self._stamina() != ordinary + restored or self._double_stamina() != balance - amount:
            raise RuntimeError("兑换后的普通体力或双倍体力变化与预期不符，未开战")
        self._double_exchange_details = {"double_spent": amount, "restored_stamina": restored}
        self.log(f"已核对两次并兑换 {amount} 双倍体力，补充 {restored} 普通体力")

    def _combat(self, options: dict[str, Any]) -> None:
        combat = options.get("combat", {})
        skills = combat.get("skills", [])
        shoot = combat.get("shoot", {"x": 1480, "y": 565, "mode": "tap"})
        max_cycles = int(combat.get("max_cycles", 24))
        movement_ms = int(combat.get("movement_ms", 6500))
        hold_ms = int(combat.get("hold_ms", 900))
        combat_mode = combat.get("mode", "move_shoot")
        if shoot.get("mode") == "once":
            self._tap(int(shoot["x"]), int(shoot["y"]), 0.1)
        for skill in skills:
            if skill.get("mode") == "once":
                self._tap(int(skill["x"]), int(skill["y"]), 0.1)
        for _ in range(max_cycles):
            self._check_cancel()
            hits = self._hits((680, 80, 1370, 865))
            if find_text(hits, "失败"):
                raise RuntimeError("体力本战斗失败")
            if find_text(hits, "战斗结算"):
                self._click("确定", (900, 710, 1170, 815))
                return
            if shoot.get("mode") == "tap":
                self._tap(int(shoot["x"]), int(shoot["y"]), 0.1)
            elif shoot.get("mode") == "hold":
                self._swipe(int(shoot["x"]), int(shoot["y"]), int(shoot["x"]), int(shoot["y"]), hold_ms, 0.1)
            for skill in skills:
                mode = skill.get("mode", "off")
                if mode == "tap":
                    self._tap(int(skill["x"]), int(skill["y"]), 0.1)
                elif mode == "hold":
                    self._swipe(int(skill["x"]), int(skill["y"]), int(skill["x"]), int(skill["y"]), hold_ms, 0.1)
            if combat_mode == "move_shoot":
                self._swipe(210, 690, 345, 690, movement_ms, 0.5)
            else:
                self.sleep(movement_ms / 1000)
        raise RuntimeError("体力本战斗超时，已停止后续刷取")

    def _run_stage(self, name: str, options: dict[str, Any], *, double_extra: bool = False) -> None:
        self._stage(name)
        if not self._text(name, (735, 145, 1040, 220)):
            raise RuntimeError(f"未进入 {name} 关卡详情")
        if name != "BONUS":
            detail_cost = integer_after(self._hits((760, 635, 1050, 730)), "消耗体力")
            if detail_cost is None or detail_cost < 0:
                raise RuntimeError(f"无法识别 {name} 消耗体力")
            if self._stamina() < detail_cost:
                raise SkipTask(f"体力不足以挑战 {name}")
        if name == "BONUS":
            # This threshold is intentionally stricter than the displayed 50 cost.
            if double_extra:
                if self._double_stamina() < 50:
                    raise SkipTask("双倍体力不足 50，已跳过额外 BONUS")
            elif self._stamina() < int(options.get("min_stamina", 100)):
                raise SkipTask("体力不足 100，已停止 BONUS 刷取")
            detail = self._hits((760, 635, 1415, 730))
            cost = integer_after(detail, "消耗体力")
            if cost not in {50, 100}:
                raise RuntimeError("BONUS 消耗体力与预期不符")
            use_ticket = not double_extra and bool(options.get("use_double_ticket", False))
            expected_cost = 100 if use_ticket else 50
            if cost != expected_cost:
                self._tap(1375, 680)
                updated_cost = integer_after(self._hits((760, 635, 1415, 730)), "消耗体力")
                if updated_cost != expected_cost:
                    raise RuntimeError("双倍券开关状态无法确认，未进入战斗")
            if double_extra and self._stamina() < 50:
                self._exchange_double_stamina()
            confirmed_stamina = self._stamina()
            if confirmed_stamina < (50 if double_extra else int(options.get("min_stamina", 100))):
                raise SkipTask("普通体力仍不足，本局 BONUS 未进入战斗")
            self.log(f"BONUS 开战核对：普通体力 {confirmed_stamina}，"
                     f"双倍券{'开启' if use_ticket else '关闭'}，详情页消耗 {expected_cost}")
        self._click("选择助战好友", (725, 745, 1090, 865))
        self._tap(530, 325)
        self._click("开战", (820, 765, 1240, 885))
        self.sleep(float(options.get("load_wait", 8)))
        self._combat(options)

    def bonus_double_stamina(self, options: dict[str, Any]) -> dict[str, Any]:
        self._home()
        balance = self._double_stamina()
        if balance < 50:
            raise SkipTask(f"当前双倍体力 {balance} 不足 50，已跳过额外 BONUS")
        self._double_exchange_details = {}
        self._activity()
        self.log(f"当前双倍体力 {balance}，增加一次 BONUS（不使用双倍券）")
        self._run_stage("BONUS", options, double_extra=True)
        return {"runs": 1, "stop_reason": "daily_extra_completed", "initial_double_stamina": balance,
                "final_stamina": self._stamina(), "max_runs": 1, **self._double_exchange_details}

    def stamina_farm(self, options: dict[str, Any]) -> dict[str, Any]:
        self._activity()
        # First-clear detection uses the stage's displayed best score. A missing
        # value is ambiguous and stops rather than entering an unknown stage.
        if options.get("first_clear", "auto") in {True, "auto"}:
            for name in ("1-1", "1-2", "1-3", "2-1"):
                self._first_clear_stage(name, options)
            # The next stage can remain hidden until 2-1 is complete. Check the
            # map after clearing 2-1 rather than caching the initial stage list.
            for number in range(2, 9):
                name = f"2-{number}"
                if not self._optional_stage_visible(name):
                    break
                self._first_clear_stage(name, options)
            else:
                raise RuntimeError("第二页首通检查达到安全上限，未开始 BONUS")
        runs = 0
        limit = int(options.get("max_runs", 20))
        while runs < limit:
            current = self._stamina()
            if current < int(options.get("min_stamina", 100)):
                self.log(f"BONUS 已完成 {runs} 次；当前普通体力 {current} 不足 {options.get('min_stamina', 100)}，结束刷取")
                return {"runs": runs, "stop_reason": "stamina_below_minimum", "final_stamina": current,
                        "max_runs": limit, "min_stamina": options.get("min_stamina", 100)}
            self.log(f"当前体力 {current}，开始第 {runs + 1} 次 BONUS")
            self._run_stage("BONUS", options)
            runs += 1
            self.log(f"第 {runs} 次 BONUS 已完成，继续检查普通体力")
        self.log(f"BONUS 已达到本次上限 {limit} 次")
        return {"runs": runs, "stop_reason": "max_runs_reached", "max_runs": limit,
                "min_stamina": options.get("min_stamina", 100)}

    def _first_clear_stage(self, name: str, options: dict[str, Any]) -> None:
        self._stage(name)
        score = None
        for _ in range(5):
            detail = self._hits((745, 145, 1030, 265))
            if find_text(detail, name):
                score = integer_after(detail, "最高得分")
                if score is not None:
                    break
            self.sleep(0.5)
        if score is None:
            raise RuntimeError(f"无法识别 {name} 最高得分，未判断是否需首通")
        self._tap(70, 168, 1.2)
        self.log(f"{name} 最高得分 {score}，{'开始首通' if score == 0 else '已通关'}")
        if score == 0:
            self._run_stage(name, options)

    def _optional_stage_visible(self, name: str) -> bool:
        # New stage icons and their orange captions fade in separately after
        # settlement. Let the unlock animation finish before testing absence.
        self.sleep(2.0)
        missing_frames = 0
        for _ in range(5):
            frame = self._screen()
            labels = {re.sub(r"\s+", "", h.text).upper() for h in stage_labels(frame)}
            footer = read_text(frame, (630 / 1600, 780 / 900, 970 / 1600, 890 / 900), min_score=0.6)
            on_page_two = any(re.fullmatch(r"2\s*/\s*[2-6]", h.text.strip()) for h in footer)
            if on_page_two and any(label == "BONUS" or re.fullmatch(r"2-\d+", label) for label in labels):
                if name.upper() in labels:
                    self.log(f"第二页已出现 {name}，继续检查首通")
                    return True
                missing_frames += 1
                if missing_frames >= 2:
                    self.log(f"已重复检查第二页，当前未出现 {name}")
                    return False
            else:
                missing_frames = 0
            self.sleep(0.8)
        raise RuntimeError(f"未确认第二页地图，无法判断是否出现 {name}")

    def _tasks(self) -> None:
        self._home()
        self._tap(65, 205, 1.5)
        if not self._text("使魔探险", (0, 490, 175, 640)):
            raise RuntimeError("未进入首页任务页面")

    def _scroll_expeditions(self) -> bool:
        with Image.open(io.BytesIO(self._screen())) as image:
            before = image.convert("RGB").crop((1080, 270, 1500, 850))
        self._swipe(1210, 770, 1210, 285)
        with Image.open(io.BytesIO(self._screen())) as image:
            after = image.convert("RGB").crop((1080, 270, 1500, 850))
        return max(ImageStat.Stat(ImageChops.difference(before, after)).mean) >= 2

    def expeditions(self, options: dict[str, Any]) -> None:
        self._tasks()
        self._click("使魔探险", (0, 480, 180, 640))
        claimed = 0
        stationary = 0
        for _ in range(60):
            hits = self._hits((1130, 250, 1480, 870))
            reward = find_text(hits, "点击领取奖励")
            if reward:
                accepted = integer_after(self._hits(), "已接收的探险数")
                self._tap(reward.x + reward.width // 2, reward.y + reward.height // 2, 0.6)
                for _ in range(5):
                    after = self._hits()
                    if find_text(after, "获得奖励"):
                        self._tap(800, 740)
                        break
                    remaining = integer_after(after, "已接收的探险数")
                    if accepted is not None and remaining is not None and remaining < accepted:
                        break
                    self.sleep(0.4)
                else:
                    raise RuntimeError("未确认使魔探险奖励领取成功")
                claimed += 1
                stationary = 0
                continue
            stationary = 0 if self._scroll_expeditions() else stationary + 1
            if stationary >= 2:
                break
        else:
            raise RuntimeError("使魔探险领取扫描超过上限")
        self.log(f"使魔探险领取 {claimed} 份奖励")
        # Claiming scans down the whole list. Dispatch must begin at the top.
        for _ in range(6):
            self._swipe(1210, 300, 1210, 790)
        dispatched = 0
        stationary = 0
        for _ in range(60):
            hits = self._hits((1120, 270, 1490, 875))
            select = find_text(hits, "点击选择使魔")
            if select:
                self._tap(select.x + select.width // 2, select.y + select.height // 2)
                self._click("一键派遣", (860, 730, 1200, 865))
                success = self._text("成功率", (1200, 135, 1530, 285))
                if success is None:
                    raise RuntimeError("一键派遣后未显示成功率")
                self._click("出发", (1180, 730, 1510, 865))
                dispatched += 1
                stationary = 0
                continue
            stationary = 0 if self._scroll_expeditions() else stationary + 1
            if stationary >= 2:
                break
        else:
            raise RuntimeError("使魔探险派遣扫描超过上限")
        self.log(f"使魔探险派遣 {dispatched} 队")

    def claim_presence(self, options: dict[str, Any]) -> None:
        self._tasks()
        self._click("存在感", (0, 335, 180, 445))
        self._claim_task_rewards("存在感")

    def claim_weekly(self, options: dict[str, Any]) -> None:
        self._tasks()
        self._click("每周任务", (0, 430, 180, 525))
        # Tight crops cut through the decorative logo and can merge its two
        # lines with the adjacent stars. OCR the page, then restrict positions.
        header = [h for h in self._hits() if 280 <= h.x <= 570 and 180 <= h.y <= 320]
        if not (find_text(header, "每周") and find_text(header, "存在感")):
            raise RuntimeError("未确认每周任务奖励页面，未领取")
        self._claim_task_rewards("每周")

    def _claim_task_rewards(self, label: str) -> None:
        self._click("一键领取", (1150, 230, 1400, 320), missing="skip")
        claimed = False
        for _ in range(10):
            if self._text("已领取以下奖励"):
                claimed = True
                # The reward panel grows with its contents. Tap outside it and
                # allow the loading animation to finish before checking again.
                self._tap(1450, 740, 1.0)
                continue
            if claimed:
                self.log(f"已领取{label}奖励")
                return
            self.sleep(0.5)
        if claimed:
            raise RuntimeError(f"{label}奖励弹窗未关闭")
        raise SkipTask(f"{label}没有可领取奖励")


def run_game_flow(runner: Any, step: dict[str, Any]) -> dict[str, Any] | None:
    flows = GameFlows(runner)
    name = step["flow"]
    options = step.get("options", {})
    mapping = {
        "enter_game": flows.enter_game,
        "refresh_resonance": flows.refresh_resonance,
        "free_gift": flows.free_gift,
        "quick_battle": flows.quick_battle,
        "stamina_farm": flows.stamina_farm,
        "bonus_double_stamina": flows.bonus_double_stamina,
        "expeditions": flows.expeditions,
        "claim_presence": flows.claim_presence,
        "claim_weekly": flows.claim_weekly,
    }
    if name not in mapping:
        raise ValueError(f"未知游戏流程: {name}")
    return mapping[name](options)
