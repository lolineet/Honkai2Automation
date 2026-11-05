"""Weapon slots follow the equipment page's left-to-right A/B/C order."""
from __future__ import annotations

import copy


WEAPON_POSITIONS = {"A": (1255, 790), "B": (1480, 790), "C": (1480, 565)}
LEGACY_SKILL_POSITIONS = [(1105, 644), (1255, 570), (1040, 790)]


def weapon_controls(combat: dict) -> dict:
    controls = {slot: {"x": x, "y": y, "mode": "off"}
                for slot, (x, y) in WEAPON_POSITIONS.items()}
    if "weapons" in combat:
        for slot, control in combat["weapons"].items():
            controls[slot].update(control)
        return controls
    skills = combat.get("skills", [])
    for index, skill in enumerate(skills):
        slot = "ABC"[index]
        controls[slot].update(skill)
        # The shipped skill coordinates target character/familiar skills,
        # not weapons. Preserve only deliberately calibrated coordinates.
        if (skill.get("x"), skill.get("y")) == LEGACY_SKILL_POSITIONS[index]:
            controls[slot]["x"], controls[slot]["y"] = WEAPON_POSITIONS[slot]
    # An authored A/B/C plan takes precedence over the old implicit shot.
    # With no enabled slot, retain the old shooting behavior as slot C.
    if not any(control["mode"] != "off" for control in controls.values()):
        controls["C"].update(combat.get("shoot", {"mode": "tap"}))
    return controls


def upgrade_combat_controls(data: dict) -> None:
    def visit(steps):
        for step in steps:
            if step.get("type") == "game_flow" and step.get("flow") in {"stamina_farm", "bonus_double_stamina"}:
                combat = step.setdefault("options", {}).setdefault("combat", {})
                combat["weapons"] = copy.deepcopy(weapon_controls(combat))
                combat.pop("shoot", None)
                combat.pop("skills", None)
            for key in ("steps", "then", "else"):
                visit(step.get(key, []))
    for task in data.get("tasks", []):
        visit(task.get("steps", []))
