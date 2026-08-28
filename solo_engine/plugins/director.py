from __future__ import annotations

from dataclasses import dataclass, field
import random
from typing import Any, Iterable


ROLE_LOADOUTS = {
    "chaser": {"weapons": ("sg", "rl"), "weapon": 5, "health": 110, "armor": 20},
    "gunner": {"weapons": ("lg", "pg"), "weapon": 6, "health": 100, "armor": 25},
    "marksman": {"weapons": ("rg", "mg"), "weapon": 7, "health": 90, "armor": 15},
    "bruiser": {"weapons": ("sg", "rl"), "weapon": 5, "health": 145, "armor": 60},
    "skirmisher": {"weapons": ("sg", "rl", "lg"), "weapon": 5, "health": 105, "armor": 30},
    "berserker": {"weapons": ("g", "sg"), "weapon": 3, "health": 125, "armor": 20},
}


MODE_PROFILES: dict[str, dict[str, Any]] = {
    "horde": {"name": "Hunt", "pressure": (42, 64), "max_engaged": 3, "idle_timeout": 6.0, "far_distance": 1450.0, "roles": (("chaser", 4), ("gunner", 3), ("skirmisher", 3), ("marksman", 1), ("bruiser", 1))},
    "arena_run": {"name": "Roguelite", "pressure": (40, 66), "max_engaged": 3, "idle_timeout": 7.0, "far_distance": 1550.0, "roles": (("skirmisher", 4), ("chaser", 3), ("gunner", 3), ("marksman", 1), ("bruiser", 1))},
    "gun_game": {"name": "Flow", "pressure": (38, 60), "max_engaged": 3, "idle_timeout": 4.5, "far_distance": 1250.0, "roles": (("skirmisher", 5), ("chaser", 3), ("gunner", 2), ("marksman", 1))},
    "boss_rush": {"name": "Duel", "pressure": (36, 58), "max_engaged": 1, "idle_timeout": 7.0, "far_distance": 1500.0, "roles": (("bruiser", 1),)},
    "wipeout_solo": {"name": "Squad", "pressure": (44, 66), "max_engaged": 3, "idle_timeout": 7.0, "far_distance": 1500.0, "roles": (("chaser", 3), ("gunner", 3), ("skirmisher", 3), ("marksman", 1))},
    "gauntlet_run": {"name": "Trial", "pressure": (40, 64), "max_engaged": 3, "idle_timeout": 7.0, "far_distance": 1500.0, "roles": (("skirmisher", 4), ("chaser", 3), ("gunner", 2), ("marksman", 1))},
    "last_stand": {"name": "Siege", "pressure": (48, 72), "max_engaged": 4, "idle_timeout": 5.0, "far_distance": 1400.0, "roles": (("chaser", 4), ("gunner", 3), ("bruiser", 2), ("skirmisher", 2), ("marksman", 1))},
    "one_life": {"name": "Tension", "pressure": (34, 55), "max_engaged": 2, "idle_timeout": 6.0, "far_distance": 1450.0, "roles": (("skirmisher", 4), ("chaser", 2), ("gunner", 2), ("marksman", 1))},
    "bounty_hunt": {"name": "Escort", "pressure": (40, 62), "max_engaged": 3, "idle_timeout": 6.0, "far_distance": 1450.0, "roles": (("bruiser", 3), ("gunner", 3), ("skirmisher", 3), ("marksman", 1))},
    "rocket_tag": {"name": "Chase", "pressure": (42, 64), "max_engaged": 3, "idle_timeout": 5.0, "far_distance": 1350.0, "roles": (("chaser", 5), ("skirmisher", 3), ("bruiser", 1))},
    "movement_hunter": {"name": "Pursuit", "pressure": (40, 60), "max_engaged": 3, "idle_timeout": 5.0, "far_distance": 1350.0, "roles": (("chaser", 4), ("gunner", 3), ("skirmisher", 3))},
    "predator": {"name": "Swarm", "pressure": (52, 76), "max_engaged": 4, "idle_timeout": 4.5, "far_distance": 1350.0, "roles": (("chaser", 4), ("gunner", 3), ("skirmisher", 3), ("bruiser", 1))},
    "accuracy_trial": {"name": "Target", "pressure": (26, 46), "max_engaged": 2, "idle_timeout": 5.0, "far_distance": 1300.0, "roles": (("skirmisher", 5), ("chaser", 2), ("gunner", 1))},
    "speedrun_combat": {"name": "Feed", "pressure": (52, 74), "max_engaged": 4, "idle_timeout": 3.5, "far_distance": 1150.0, "roles": (("chaser", 4), ("skirmisher", 4), ("gunner", 2))},
    "random_loadout": {"name": "Improvisation", "pressure": (40, 64), "max_engaged": 3, "idle_timeout": 6.0, "far_distance": 1450.0, "roles": (("skirmisher", 5), ("chaser", 2), ("gunner", 2), ("marksman", 1))},
}

DIFFICULTY = {
    "easy": {"pressure": -10, "engaged": -1, "idle": 2.0, "recovery": 3.5},
    "normal": {"pressure": 0, "engaged": 0, "idle": 0.0, "recovery": 2.5},
    "hard": {"pressure": 8, "engaged": 0, "idle": -1.0, "recovery": 1.7},
    "nightmare": {"pressure": 14, "engaged": 1, "idle": -1.5, "recovery": 1.1},
}

SAFE_RECOVERY_MODES = {"horde", "gun_game", "speedrun_combat"}


@dataclass
class BotObservation:
    client_id: int
    role: str
    spawned_at: float
    last_seen_at: float
    last_contact_at: float
    last_damage_dealt_at: float = -1e9
    last_damage_taken_at: float = -1e9
    distance: float = 0.0
    recoveries: int = 0


@dataclass
class DirectorDecision:
    action: str
    reason: str
    bot_id: int | None = None
    role: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


class EncounterDirector:
    """Pure mode-aware encounter manager.

    The Director never aims, fires, or directly steers a bot. It only measures
    encounter pressure, assigns bounded roles, and requests safe encounter-level
    interventions such as replacing a bot that has clearly stopped contributing.
    """

    def __init__(self, mode: str, difficulty: str = "normal", seed: int = 0):
        if mode not in MODE_PROFILES:
            raise ValueError(f"unsupported Director mode: {mode}")
        self.mode = mode
        self.difficulty = difficulty if difficulty in DIFFICULTY else "normal"
        self.seed = int(seed)
        self.profile = dict(MODE_PROFILES[mode])
        diff = DIFFICULTY[self.difficulty]
        low, high = self.profile["pressure"]
        self.pressure_low = max(5, low + diff["pressure"])
        self.pressure_high = min(95, high + diff["pressure"])
        self.max_engaged = max(1, self.profile["max_engaged"] + diff["engaged"])
        self.idle_timeout = max(2.5, self.profile["idle_timeout"] + diff["idle"])
        self.far_distance = float(self.profile["far_distance"])
        self.recovery_seconds = float(diff["recovery"])
        self.bots: dict[int, BotObservation] = {}
        self.spawn_serial = 0
        self.last_intervention_at = -1e9
        self.recovery_until = -1e9
        self.recent_player_damage: list[tuple[float, float]] = []
        self.telemetry: list[dict[str, Any]] = []

    @property
    def profile_name(self) -> str:
        return str(self.profile["name"])

    def choose_role(self, *, stage: int = 0, serial: int | None = None) -> str:
        if serial is None:
            serial = self.spawn_serial
            self.spawn_serial += 1
        weighted = tuple(self.profile["roles"])
        rng = random.Random(self.seed + int(stage) * 1009 + int(serial) * 9176 + 41)
        total = sum(weight for _, weight in weighted)
        pick = rng.uniform(0, total)
        running = 0.0
        for role, weight in weighted:
            running += weight
            if pick <= running:
                return role
        return weighted[-1][0]

    def register_spawn(self, client_id: int, now: float, *, stage: int = 0, role: str | None = None) -> str:
        role = role or self.choose_role(stage=stage)
        cid = int(client_id)
        self.bots[cid] = BotObservation(cid, role, float(now), float(now), float(now))
        return role

    def remove_bot(self, client_id: int) -> None:
        self.bots.pop(int(client_id), None)

    def record_damage(self, attacker_id: int | None, target_id: int | None, human_id: int | None, damage: float, now: float) -> None:
        now = float(now)
        if attacker_id in self.bots:
            bot = self.bots[int(attacker_id)]
            bot.last_contact_at = now
            bot.last_damage_dealt_at = now
        if target_id in self.bots:
            bot = self.bots[int(target_id)]
            bot.last_contact_at = now
            bot.last_damage_taken_at = now
        if human_id is not None and target_id == human_id and attacker_id in self.bots:
            self.recent_player_damage.append((now, max(0.0, float(damage))))
        self._trim_damage(now)

    def observe(self, now: float, bots: Iterable[dict[str, Any]], *, human_health: float, human_armor: float, phase: str = "active") -> tuple[dict[str, Any], list[DirectorDecision]]:
        now = float(now)
        live_ids = set()
        for item in bots:
            cid = int(item["id"])
            live_ids.add(cid)
            if cid not in self.bots:
                self.register_spawn(cid, now)
            obs = self.bots[cid]
            obs.last_seen_at = now
            obs.distance = max(0.0, float(item.get("distance", 0.0)))

        for cid in list(self.bots):
            if cid not in live_ids:
                self.bots.pop(cid, None)

        self._trim_damage(now)
        recent_damage = sum(amount for when, amount in self.recent_player_damage if now - when <= 2.5)
        if human_health <= 35 or recent_damage >= 80:
            self.recovery_until = max(self.recovery_until, now + self.recovery_seconds)

        engaged = 0
        idle = []
        for obs in self.bots.values():
            recent_contact = now - obs.last_contact_at <= 3.0
            if recent_contact or obs.distance <= min(self.far_distance * 0.55, 800.0):
                engaged += 1
            idle_for = now - max(obs.spawned_at, obs.last_contact_at)
            if idle_for >= self.idle_timeout and obs.distance >= self.far_distance:
                idle.append((idle_for, obs))

        population = len(self.bots)
        engage_component = min(1.0, engaged / max(1, self.max_engaged)) * 65.0
        proximity_component = 0.0
        if population:
            near = sum(1 for obs in self.bots.values() if obs.distance <= self.far_distance)
            proximity_component = (near / population) * 25.0
        activity_component = 10.0 if any(now - obs.last_contact_at <= 2.0 for obs in self.bots.values()) else 0.0
        pressure = round(min(100.0, engage_component + proximity_component + activity_component), 1)

        snapshot = {
            "time": now,
            "mode": self.mode,
            "profile": self.profile_name,
            "difficulty": self.difficulty,
            "phase": phase,
            "pressure": pressure,
            "target_low": self.pressure_low,
            "target_high": self.pressure_high,
            "engaged": engaged,
            "max_engaged": self.max_engaged,
            "population": population,
            "idle": [obs.client_id for _, obs in idle],
            "recent_player_damage": round(recent_damage, 1),
            "recovery": now < self.recovery_until,
            "human_health": float(human_health),
            "human_armor": float(human_armor),
        }
        self.telemetry.append(snapshot)
        if len(self.telemetry) > 240:
            self.telemetry = self.telemetry[-240:]

        decisions: list[DirectorDecision] = []
        if phase != "active" or self.mode not in SAFE_RECOVERY_MODES:
            return snapshot, decisions
        if now < self.recovery_until:
            return snapshot, decisions
        if now - self.last_intervention_at < 3.0:
            return snapshot, decisions
        if pressure >= self.pressure_low:
            return snapshot, decisions
        if idle:
            idle.sort(key=lambda pair: pair[0], reverse=True)
            idle_for, obs = idle[0]
            self.last_intervention_at = now
            decisions.append(DirectorDecision(
                "recover_bot",
                f"pressure {pressure} below {self.pressure_low}; bot {obs.client_id} idle {idle_for:.1f}s at distance {obs.distance:.0f}",
                bot_id=obs.client_id,
                role=obs.role,
                data={"pressure": pressure, "idle_for": round(idle_for, 2), "distance": round(obs.distance, 1)},
            ))
        return snapshot, decisions

    def loadout_for(self, client_id: int) -> dict[str, Any]:
        obs = self.bots.get(int(client_id))
        role = obs.role if obs else "skirmisher"
        return dict(ROLE_LOADOUTS.get(role, ROLE_LOADOUTS["skirmisher"])) | {"role": role}

    def _trim_damage(self, now: float) -> None:
        self.recent_player_damage = [(when, amount) for when, amount in self.recent_player_damage if now - when <= 3.0]
