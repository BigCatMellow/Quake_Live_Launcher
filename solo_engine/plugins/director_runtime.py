from __future__ import annotations

import json
import math
import time
from pathlib import Path

try:
    from .solo_director import ROLE_SPECS, SoloDirector, distance_between
except ImportError:
    from solo_director import ROLE_SPECS, SoloDirector, distance_between


ROLE_STATS = {
    "chaser": (110, 20),
    "gunner": (100, 25),
    "marksman": (90, 15),
    "bruiser": (140, 60),
    "skirmisher": (105, 30),
    "berserker": (120, 20),
    "target": (100, 20),
    "boss": (100, 0),
}

WEAPON_KEYS = {1: "g", 2: "mg", 3: "sg", 4: "gl", 5: "rl", 6: "lg", 7: "rg", 8: "pg"}
AMMO = {"mg": 160, "sg": 45, "gl": 30, "rl": 45, "lg": 140, "rg": 30, "pg": 140}


class DirectorRuntime:
    """Adapter between the pure SoloDirector and a live minqlx plugin.

    It deliberately cannot aim/fire/path for bots. Its only active D1 action is
    replacing an objectively owned, undamaged, long-idle bot in modes whose
    lifecycle safely supports that intervention.
    """

    def __init__(self, plugin, mode: str, difficulty: str, seed: int, runtime_dir: Path):
        self.plugin = plugin
        self.director = SoloDirector(mode, difficulty, seed)
        self.runtime_dir = Path(runtime_dir)
        self.telemetry_file = self.runtime_dir / "director.jsonl"
        self.last_tick = 0.0
        self.tick_interval = 0.75
        self.pending_roles: list[str] = []
        self.spawn_index = 0
        self.enabled = True

    def now(self) -> float:
        return time.time()

    def begin_objective(self) -> None:
        self.director.begin_objective(self.now())
        self.pending_roles.clear()
        self.spawn_index = 0

    def reset(self) -> None:
        self.director.reset()
        self.pending_roles.clear()
        self.spawn_index = 0

    def bot_spawned(self, player) -> str:
        special = None
        plan = self.plugin.current_plan or {}
        if plan.get("boss"):
            special = "boss"
        elif self.plugin.mode == "accuracy_trial":
            special = "target"
        role = self.pending_roles.pop(0) if self.pending_roles else self.director.role_for_spawn(self.spawn_index, special=special)
        self.spawn_index += 1
        self.director.register_bot(player.id, self._clean_name(player), role, self.now())
        return role

    def bot_died(self, player, killer=None) -> None:
        if killer is not None and self._is_human(killer):
            self.director.note_human_kill(self.now())
        self.director.forget_bot(player.id)

    def human_died(self) -> None:
        self.director.note_human_death(self.now())

    def note_damage(self, target, attacker, damage) -> None:
        if not self._is_player(target) or not self._is_player(attacker):
            return
        self.director.note_damage(
            now=self.now(),
            damage=float(damage),
            attacker_id=attacker.id,
            target_id=target.id,
            attacker_is_bot=self._is_bot(attacker),
            target_is_bot=self._is_bot(target),
        )

    def reinforcement_delay(self, base_delay: float) -> float:
        return self.director.reinforcement_delay(self.now(), float(base_delay))

    def apply_bot_loadout(self, player, plan: dict | None = None) -> bool:
        """Apply the Director role loadout. Mode-specific trials/bosses override it."""
        try:
            plan = plan or {}
            track = self.director.tracks.get(int(player.id))
            role = track.role if track else "skirmisher"
            spec = ROLE_SPECS.get(role, ROLE_SPECS["skirmisher"])
            hp, armor = ROLE_STATS.get(role, ROLE_STATS["skirmisher"])
            player.health = int(plan.get("health", hp)) if plan.get("boss") else hp
            player.armor = int(plan.get("armor", armor)) if plan.get("boss") else armor

            weapons = {"g": True}
            ammo = {}
            for weapon in spec.weapons:
                key = WEAPON_KEYS.get(int(weapon))
                if not key:
                    continue
                weapons[key] = True
                if key != "g":
                    ammo[key] = AMMO.get(key, 100)
            player.weapons(reset=True, **weapons)
            player.ammo(reset=True, **ammo)
            player.weapon(int(spec.primary))

            if self.plugin.mode in ("arena_run", "boss_rush", "gauntlet_run") and plan:
                trial = {"rocket": 5, "lg": 6, "rail": 7, "plasma": 8}.get(plan.get("theme"))
                if trial:
                    self.plugin._give_single_weapon(player, trial)
                elif plan.get("boss"):
                    self.plugin._give_single_weapon(player, 5)
            return True
        except Exception as exc:
            self.plugin._log(f"director loadout failed: {exc}")
            return False

    def tick(self, *, force: bool = False, now: float | None = None):
        if not self.enabled:
            return None, []
        now = self.now() if now is None else float(now)
        if not force and now - self.last_tick < self.tick_interval:
            return None, []
        self.last_tick = now
        human = self.plugin.primary_player()
        if human is None:
            return None, []

        human_pos = self._position(human)
        rows = []
        for bot in self.plugin.bot_players():
            if bot.id not in self.plugin.controller.enemy_ids:
                continue
            track = self.director.tracks.get(bot.id)
            rows.append({
                "id": bot.id,
                "name": self._clean_name(bot),
                "role": track.role if track else "skirmisher",
                "distance": distance_between(human_pos, self._position(bot)),
            })

        snapshot, actions = self.director.tick(
            now=now,
            player_health=int(getattr(human, "health", 0) or 0),
            player_armor=int(getattr(human, "armor", 0) or 0),
            bots=rows,
            active=self.plugin.controller.phase.value == "active",
        )
        self._write_snapshot(snapshot, actions)
        for action in actions:
            self._execute(action)
        return snapshot, actions

    def summary(self) -> str:
        snapshot = self.director.last_snapshot
        return snapshot.summary() if snapshot else f"{self.director.profile.name} waiting for telemetry"

    def _execute(self, action) -> None:
        self.plugin._log(f"DIRECTOR action={action.kind} reason={action.reason}")
        if action.kind == "hold_reinforcements":
            return
        if action.kind != "recover_bot" or action.bot_id is None:
            return
        cid = int(action.bot_id)
        if cid not in self.plugin.controller.enemy_ids:
            return
        bot = next((p for p in self.plugin.bot_players() if p.id == cid), None)
        if bot is None:
            return
        name = self._clean_name(bot).lower() or None
        role = action.role or "skirmisher"

        # Remove ownership before kicking so the ordinary death/clear path cannot
        # interpret a Director recovery as objective progress.
        self.plugin.controller.remove_enemy(cid)
        self.director.forget_bot(cid)
        self.pending_roles.append(role)
        try:
            bot.kick("director recovery: non-contributing enemy")
        except Exception:
            try:
                self.plugin._console_kick(cid)
            except Exception:
                pass
        self.plugin._add_replacement_bot(name=name, delay=0.25)

    def _write_snapshot(self, snapshot, actions) -> None:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            if self.telemetry_file.exists() and self.telemetry_file.stat().st_size > 2_000_000:
                backup = self.telemetry_file.with_suffix(".previous.jsonl")
                try:
                    backup.unlink(missing_ok=True)
                except Exception:
                    pass
                self.telemetry_file.replace(backup)
            payload = {
                "kind": "director_tick",
                "time": self.now(),
                "mode": snapshot.mode,
                "profile": snapshot.profile,
                "pressure": snapshot.pressure,
                "target": [snapshot.pressure_low, snapshot.pressure_high],
                "alive": snapshot.alive,
                "engaged": snapshot.engaged,
                "idle": snapshot.idle,
                "far": snapshot.far,
                "player": {"health": snapshot.player_health, "armor": snapshot.player_armor},
                "recent_damage_taken": snapshot.recent_damage_taken,
                "recent_damage_dealt": snapshot.recent_damage_dealt,
                "hold_until": snapshot.hold_until,
                "actions": [
                    {"kind": a.kind, "reason": a.reason, "bot_id": a.bot_id, "role": a.role, "duration": a.duration}
                    for a in actions
                ],
            }
            with self.telemetry_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        except Exception as exc:
            self.plugin._log(f"director telemetry failed: {exc}")

    @staticmethod
    def _position(player):
        try:
            pos = player.position()
            return float(pos.x), float(pos.y), float(pos.z)
        except Exception:
            return None

    @staticmethod
    def _clean_name(player) -> str:
        try:
            import re
            return re.sub(r"\^[0-9]", "", str(player.name)).strip() or "bot"
        except Exception:
            return "bot"

    @staticmethod
    def _is_player(value) -> bool:
        return value is not None and hasattr(value, "id") and hasattr(value, "steam_id")

    @classmethod
    def _is_bot(cls, value) -> bool:
        if not cls._is_player(value):
            return False
        try:
            return int(value.steam_id) > 90_000_000_000_000_000
        except Exception:
            return False

    @classmethod
    def _is_human(cls, value) -> bool:
        return cls._is_player(value) and not cls._is_bot(value)
