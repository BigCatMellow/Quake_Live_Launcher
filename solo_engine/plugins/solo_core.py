#!/usr/bin/env python3
"""Pure Arena Run rules for Quake Live Launcher v5.

This module has no minqlx dependency so every roguelite decision can be tested
without launching Quake Live. Every exposed upgrade has a concrete runtime
consumer in ``solo_arcade.py``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import hashlib
import json
import random
import time

RARITY_WEIGHTS = {"common": 55, "uncommon": 27, "rare": 12, "epic": 5, "legendary": 1}
RARITY_COLOR = {"common": "^7", "uncommon": "^2", "rare": "^5", "epic": "^6", "legendary": "^1"}

UPGRADES = [
    {"id": "damage_1", "name": "Heavy Hands", "rarity": "common", "max": 5, "text": "+10% all damage", "effects": {"damage_mult": 0.10}},
    {"id": "health_1", "name": "Thick Skin", "rarity": "common", "max": 5, "text": "+25 max health", "effects": {"max_health": 25}},
    {"id": "armor_1", "name": "Plating", "rarity": "common", "max": 5, "text": "+20 starting armor", "effects": {"max_armor": 20}},
    {"id": "haste_1", "name": "Fleet Footed", "rarity": "uncommon", "max": 2, "text": "Gain long-duration Haste", "effects": {"haste": 1}},
    {"id": "jump_1", "name": "Spring Heels", "rarity": "uncommon", "max": 3, "text": "+18% jump launch velocity", "effects": {"jump_boost": 0.18}},
    {"id": "thrusters_1", "name": "Phase Thrusters", "rarity": "rare", "max": 2, "text": "+1 side-thruster charge and +22% thrust", "effects": {"dash_charge": 1, "dash_power": 0.22}},
    {"id": "regen_1", "name": "Second Wind", "rarity": "rare", "max": 2, "text": "Regenerate 3 health/sec after avoiding damage", "effects": {"regen_per_sec": 3}},
    {"id": "vampire_1", "name": "Bloodlust", "rarity": "rare", "max": 3, "text": "Heal from damage dealt", "effects": {"vampire": 0.04}},
    {"id": "kill_heal", "name": "Feast", "rarity": "uncommon", "max": 3, "text": "Kills restore 15 health", "effects": {"kill_heal": 15}},
    {"id": "rocket_damage", "name": "Heavy Warheads", "rarity": "uncommon", "max": 4, "text": "+15% Rocket Launcher damage", "effects": {"rocket_mult": 0.15}},
    {"id": "lg_damage", "name": "Tight Beam", "rarity": "uncommon", "max": 4, "text": "+12% Lightning Gun damage", "effects": {"lg_mult": 0.12}},
    {"id": "lg_overcharge", "name": "Overcharge", "rarity": "rare", "max": 1, "text": "Continuous LG tracking ramps bonus damage", "effects": {"lg_overcharge": 1}},
    {"id": "lg_vampire", "name": "Vampiric Current", "rarity": "rare", "max": 2, "text": "LG damage restores extra health", "effects": {"lg_vampire": 0.04}},
    {"id": "rail_damage", "name": "High Caliber", "rarity": "uncommon", "max": 4, "text": "+15% Railgun damage", "effects": {"rail_mult": 0.15}},
    {"id": "rail_combo", "name": "Perfect Shot", "rarity": "rare", "max": 1, "text": "Every third consecutive rail hit gets bonus damage", "effects": {"rail_combo": 1}},
    {"id": "plasma_damage", "name": "Hot Plasma", "rarity": "uncommon", "max": 4, "text": "+15% Plasma Gun damage", "effects": {"plasma_mult": 0.15}},
    {"id": "scavenger", "name": "Scavenger", "rarity": "uncommon", "max": 2, "text": "Kills restore ammunition", "effects": {"ammo_on_kill": 30}},
    {"id": "quad_burst", "name": "Quad Burst", "rarity": "legendary", "max": 1, "text": "Every fifth kill triggers a short Quad burst", "effects": {"quad_burst": 1}},
    {"id": "glass_cannon", "name": "Glass Cannon", "rarity": "epic", "max": 1, "text": "+75% damage, max health capped at 75", "effects": {"damage_mult": 0.75, "health_cap": 75}},
    {"id": "berserker", "name": "Berserker", "rarity": "rare", "max": 1, "text": "+35% damage, -50 max health", "effects": {"damage_mult": 0.35, "max_health": -50}},
]
UPGRADE_BY_ID = {item["id"]: item for item in UPGRADES}

SYNERGIES = [
    {"id": "stormbringer", "name": "STORMBRINGER", "needs": {"lg_damage": 2, "lg_overcharge": 1, "lg_vampire": 1}, "text": "Overcharged LG ramps harder and heals more.", "effects": {"lg_mult": 0.20, "lg_vampire": 0.05}},
    {"id": "deadeye", "name": "DEADEYE", "needs": {"rail_damage": 2, "rail_combo": 1}, "text": "Perfect Shot gains a larger third-hit bonus.", "effects": {"rail_mult": 0.15, "rail_combo_bonus": 1}},
    {"id": "juggernaut", "name": "JUGGERNAUT", "needs": {"health_1": 2, "armor_1": 2, "kill_heal": 1}, "text": "Extra health, armor and stronger kill recovery.", "effects": {"max_health": 35, "max_armor": 25, "kill_heal": 10}},
    {"id": "velocity", "name": "VELOCITY", "needs": {"haste_1": 1, "jump_1": 1, "thrusters_1": 1}, "text": "Movement build gains stronger jumps and side thrusters.", "effects": {"jump_boost": 0.12, "dash_power": 0.20}},
]

BOT_ROSTER = ["slash", "keel", "visor", "anarki", "sarge", "ranger", "doom", "bones", "xaero"]


@dataclass
class RunState:
    seed: int
    mode: str = "arena_run"
    difficulty: str = "normal"
    length: int = 20
    round: int = 1
    score: int = 0
    lives: int = 3
    started_at: float = field(default_factory=time.time)
    upgrades: Dict[str, int] = field(default_factory=dict)
    synergies: List[str] = field(default_factory=list)
    choices: List[str] = field(default_factory=list)
    waiting_for_pick: bool = False
    complete: bool = False

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in data.items() if key in allowed})


def deterministic_rng(seed: int, *parts) -> random.Random:
    token = ":".join(str(value) for value in (seed,) + parts)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return random.Random(int(digest[:16], 16))


def upgrade_effects(state: RunState) -> Dict[str, float]:
    effects: Dict[str, float] = {}
    for uid, stacks in state.upgrades.items():
        upgrade = UPGRADE_BY_ID.get(uid)
        if not upgrade:
            continue
        for key, value in upgrade["effects"].items():
            effects[key] = effects.get(key, 0) + value * stacks
    for sid in state.synergies:
        synergy = next((item for item in SYNERGIES if item["id"] == sid), None)
        if synergy:
            for key, value in synergy["effects"].items():
                effects[key] = effects.get(key, 0) + value
    return effects


def available_upgrades(state: RunState) -> List[dict]:
    return [item for item in UPGRADES if state.upgrades.get(item["id"], 0) < item.get("max", 1)]


def roll_upgrade_choices(state: RunState, count: int = 3) -> List[str]:
    pool = available_upgrades(state)
    rng = deterministic_rng(state.seed, state.round, "upgrades")
    chosen: List[str] = []
    while pool and len(chosen) < count:
        weights = [RARITY_WEIGHTS.get(item["rarity"], 1) for item in pool]
        item = rng.choices(pool, weights=weights, k=1)[0]
        chosen.append(item["id"])
        pool = [candidate for candidate in pool if candidate["id"] != item["id"]]
    state.choices = chosen
    state.waiting_for_pick = bool(chosen)
    return chosen


def pick_upgrade(state: RunState, index: int) -> dict:
    if not state.waiting_for_pick or not state.choices:
        raise ValueError("No upgrade choice is waiting.")
    if index < 1 or index > len(state.choices):
        raise ValueError("Choose 1, 2, or 3.")
    uid = state.choices[index - 1]
    state.upgrades[uid] = state.upgrades.get(uid, 0) + 1
    state.waiting_for_pick = False
    state.choices = []
    unlocked = []
    for synergy in SYNERGIES:
        if synergy["id"] in state.synergies:
            continue
        if all(state.upgrades.get(req, 0) >= needed for req, needed in synergy["needs"].items()):
            state.synergies.append(synergy["id"])
            unlocked.append(synergy)
    return {"upgrade": UPGRADE_BY_ID[uid], "synergies": unlocked}


def round_plan(state: RunState) -> dict:
    r = state.round
    diff = {"easy": -1, "normal": 0, "hard": 1, "nightmare": 2}.get(state.difficulty, 0)
    boss = r % 5 == 0
    rng = deterministic_rng(state.seed, r, "wave")
    if boss:
        tier = max(1, r // 5)
        names = ["keel", "slash", "doom", "xaero"]
        return {
            "boss": True, "theme": "boss", "count": 1,
            "skill": min(5, 3 + diff + tier // 2),
            "bots": [names[(tier - 1) % len(names)]],
            "health": 450 + tier * 225, "armor": 150 + tier * 100,
            "damage_mult": 1.0 + tier * 0.12,
        }
    elite = r >= 7 and r % 3 == 0
    if r % 7 == 0:
        theme = "rail"
    elif r % 6 == 0:
        theme = "rocket"
    elif r % 4 == 0:
        theme = "lg"
    elif elite:
        theme = "elite"
    else:
        theme = "normal"
    count = min(8, 2 + (r - 1) // 2 + max(0, diff))
    skill = max(1, min(5, 2 + (r - 1) // 4 + diff))
    roster = BOT_ROSTER[:-1]
    bots = rng.sample(roster, k=min(count, len(roster)))
    while len(bots) < count:
        bots.append(rng.choice(roster))
    return {
        "boss": False, "theme": theme, "count": count, "skill": skill, "bots": bots,
        "health": 100 + max(0, r - 3) * 8,
        "armor": max(0, r - 4) * 5,
        "damage_mult": 1.0 + max(0, r - 5) * 0.025,
    }


def advance_round(state: RunState) -> bool:
    state.score += 1
    if state.length and state.round >= state.length:
        state.complete = True
        return True
    state.round += 1
    return False


def save_state(path: Path, state: RunState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(state.to_dict(), indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def load_state(path: Path) -> Optional[RunState]:
    try:
        return RunState.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return None


def new_state(seed: Optional[int] = None, difficulty: str = "normal", length: int = 20) -> RunState:
    if seed is None:
        seed = int(time.time() * 1000) & 0x7FFFFFFF
    return RunState(seed=int(seed), difficulty=str(difficulty), length=int(length))
