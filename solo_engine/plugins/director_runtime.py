from __future__ import annotations

from dataclasses import replace
import json
import time
from pathlib import Path

try:
    from .director_learning import DirectorLearning
    from .solo_director import ROLE_SPECS, SoloDirector, distance_between, profile_for
except ImportError:
    from director_learning import DirectorLearning
    from solo_director import ROLE_SPECS, SoloDirector, distance_between, profile_for


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
    """Live adapter for the encounter Director and its learning layer.

    Quake still owns navigation, aiming and firing. The Director is allowed to
    shape role composition, replacement choice, reinforcement timing, bounded
    recovery windows and a small learned pressure target. Every intervention is
    logged as decision -> execution -> later evaluation so the adaptive system
    remains auditable instead of becoming a black box.
    """

    def __init__(self, plugin, mode: str, difficulty: str, seed: int, runtime_dir: Path):
        self.plugin = plugin
        self.mode = str(mode)
        self.difficulty = str(difficulty).lower()
        self.seed = int(seed)
        self.director = SoloDirector(self.mode, self.difficulty, self.seed)
        self.runtime_dir = Path(runtime_dir)
        self.telemetry_file = self.runtime_dir / "director.jsonl"
        self.actions_file = self.runtime_dir / "director_actions.jsonl"
        self.learning = DirectorLearning(self.runtime_dir, self.mode, self.difficulty, self.seed)
        self.last_tick = 0.0
        self.tick_interval = 0.75
        self.pending_roles: list[str] = []
        self.pending_action_ids: list[str | None] = []
        self.spawn_index = 0
        self.enabled = True
        self.session_finished = False
        self._refresh_learned_profile()

    def now(self) -> float:
        return time.time()

    def _refresh_learned_profile(self) -> None:
        base = profile_for(self.mode, self.difficulty)
        shift = self.learning.pressure_shift()
        self.director.profile = replace(
            base,
            pressure_low=max(10.0, base.pressure_low + shift),
            pressure_high=max(20.0, base.pressure_high + shift),
        )

    def begin_objective(self) -> None:
        now = self.now()
        self._refresh_learned_profile()
        self.learning.begin_objective(now)
        self.director.begin_objective(now)
        self.pending_roles.clear()
        self.pending_action_ids.clear()
        self.spawn_index = 0

    def reset(self) -> None:
        now = self.now()
        phase = getattr(getattr(self.plugin, "controller", None), "phase", None)
        phase_value = getattr(phase, "value", "")
        if phase_value in ("complete", "failed") and not self.session_finished:
            self.finish_session(
                phase_value,
                kills=int(getattr(self.plugin, "kills", 0) or 0),
                deaths=int(getattr(self.plugin, "player_deaths", 0) or 0),
                duration=max(0.0, now - float(getattr(self.plugin, "start_time", now) or now)),
            )
        else:
            self.learning.finish_objective(now, reason="runtime_reset")
        self.director.reset()
        self.pending_roles.clear()
        self.pending_action_ids.clear()
        self.spawn_index = 0

    def finish_session(self, result: str, *, kills: int, deaths: int, duration: float) -> None:
        if self.session_finished:
            return
        self.learning.finish_session(
            result=str(result),
            kills=int(kills),
            deaths=int(deaths),
            duration=float(duration),
            now=self.now(),
        )
        self.session_finished = True
        self._write_action_event(
            "session_summary",
            result=str(result),
            kills=int(kills),
            deaths=int(deaths),
            duration=float(duration),
            learned_pressure_shift=self.learning.pressure_shift(),
        )

    def bot_spawned(self, player) -> str:
        special = None
        plan = self.plugin.current_plan or {}
        if plan.get("boss"):
            special = "boss"
        elif self.plugin.mode == "accuracy_trial":
            special = "target"

        action_id = None
        if self.pending_roles:
            role = self.pending_roles.pop(0)
            action_id = self.pending_action_ids.pop(0) if self.pending_action_ids else None
        else:
            base_role = self.director.role_for_spawn(self.spawn_index, special=special)
            current_roles = [track.role for track in self.director.tracks.values()]
            role = self.learning.choose_role(
                base_role,
                self.director.profile.roles,
                current_roles,
                self.director.last_snapshot,
                special=special is not None,
            )
        self.spawn_index += 1
        self.director.register_bot(player.id, self._clean_name(player), role, self.now())

        if action_id:
            self.learning.mark_execution(
                action_id,
                "replacement_spawned",
                replacement_bot_id=int(player.id),
                replacement_name=self._clean_name(player),
                replacement_role=role,
            )
            self._write_action_event(
                "execution_result",
                action_id=action_id,
                result="replacement_spawned",
                replacement_bot_id=int(player.id),
                replacement_name=self._clean_name(player),
                replacement_role=role,
            )
        return role

    def bot_died(self, player, killer=None) -> None:
        track = self.director.tracks.get(int(player.id))
        if track is not None:
            self.learning.record_role_outcome(
                track.role,
                track.damage_dealt,
                track.damage_received,
                killed_by_human=killer is not None and self._is_human(killer),
            )
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
        now = self.now()
        delay = self.director.reinforcement_delay(now, float(base_delay))
        return self.learning.adjust_reinforcement_delay(delay, self.director.last_snapshot)

    def apply_bot_loadout(self, player, plan: dict | None = None) -> bool:
        """Apply a role loadout; authored mode contracts still override it."""
        try:
            plan = plan or {}
            # Rocket Tag is a hard gameplay contract, not a Director preference.
            if self.plugin.mode == "rocket_tag":
                self.plugin._give_single_weapon(player, 5)
                return True

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
        roles = [track.role for track in self.director.tracks.values()]
        evaluations = self.learning.note_snapshot(snapshot, roles, now)
        self._write_snapshot(snapshot, actions, evaluations)
        for evaluation in evaluations:
            self._write_action_event("evaluation", **evaluation)
            self.plugin._log(
                f"DIRECTOR evaluation={evaluation['kind']} success={evaluation['success']} "
                f"pressure={evaluation['pressure_before']:.0f}->{evaluation['pressure_after']:.0f}"
            )
        for action in actions:
            self._execute(action, snapshot, roles, now)
        return snapshot, actions

    def summary(self) -> str:
        snapshot = self.director.last_snapshot
        if snapshot is None:
            return f"{self.director.profile.name} waiting for telemetry"
        shift = self.learning.pressure_shift()
        return f"{snapshot.summary()} learned_shift={shift:+.1f}"

    def _execute(self, action, snapshot, roles: list[str], now: float) -> None:
        action_id = self.learning.open_action(action, snapshot, roles, now)
        self.plugin._log(
            f"DIRECTOR decision={action.kind} id={action_id} reason={action.reason} "
            f"pressure={snapshot.pressure:.0f} target={snapshot.pressure_low:.0f}-{snapshot.pressure_high:.0f}"
        )
        self._write_action_event(
            "decision",
            action_id=action_id,
            action=action.kind,
            reason=action.reason,
            bot_id=action.bot_id,
            role=action.role,
            pressure=snapshot.pressure,
            target=[snapshot.pressure_low, snapshot.pressure_high],
            engaged=snapshot.engaged,
            alive=snapshot.alive,
            idle=snapshot.idle,
            far=snapshot.far,
        )

        if action.kind == "hold_reinforcements":
            self.learning.mark_execution(action_id, "hold_active", duration=float(action.duration))
            self._write_action_event(
                "execution_result", action_id=action_id, result="hold_active", duration=float(action.duration)
            )
            return

        if action.kind != "recover_bot" or action.bot_id is None:
            self.learning.mark_execution(action_id, "skipped_unknown_action")
            self._write_action_event("execution_result", action_id=action_id, result="skipped_unknown_action")
            return

        cid = int(action.bot_id)
        if cid not in self.plugin.controller.enemy_ids:
            self.learning.mark_execution(action_id, "skipped_not_owned", bot_id=cid)
            self._write_action_event("execution_result", action_id=action_id, result="skipped_not_owned", bot_id=cid)
            return
        bot = next((p for p in self.plugin.bot_players() if p.id == cid), None)
        if bot is None:
            self.learning.mark_execution(action_id, "skipped_bot_missing", bot_id=cid)
            self._write_action_event("execution_result", action_id=action_id, result="skipped_bot_missing", bot_id=cid)
            return

        track = self.director.tracks.get(cid)
        if track is None:
            self.learning.mark_execution(action_id, "skipped_track_missing", bot_id=cid)
            self._write_action_event("execution_result", action_id=action_id, result="skipped_track_missing", bot_id=cid)
            return
        if float(track.damage_received) > 0.0:
            self.learning.mark_execution(action_id, "skipped_player_invested_damage", bot_id=cid)
            self._write_action_event(
                "execution_result",
                action_id=action_id,
                result="skipped_player_invested_damage",
                bot_id=cid,
                damage_received=float(track.damage_received),
            )
            return

        name = self._clean_name(bot).lower() or None
        current_roles = [value.role for key, value in self.director.tracks.items() if key != cid]
        base_role = action.role or track.role or "skirmisher"
        role = self.learning.choose_role(
            base_role,
            self.director.profile.roles,
            current_roles,
            snapshot,
            special=base_role in ("boss", "target"),
        )

        self.plugin.controller.remove_enemy(cid)
        self.director.forget_bot(cid)
        self.pending_roles.append(role)
        self.pending_action_ids.append(action_id)
        kick_method = "player.kick"
        try:
            bot.kick("director recovery: non-contributing enemy")
        except Exception:
            kick_method = "console_kick"
            try:
                self.plugin._console_kick(cid)
            except Exception as exc:
                kick_method = "failed"
                self.pending_roles.pop()
                self.pending_action_ids.pop()
                self.learning.mark_execution(action_id, "kick_failed", bot_id=cid, error=str(exc))
                self._write_action_event(
                    "execution_result", action_id=action_id, result="kick_failed", bot_id=cid, error=str(exc)
                )
                return

        self.plugin._add_replacement_bot(name=name, delay=0.25)
        self.learning.mark_execution(
            action_id,
            "replacement_scheduled",
            removed_bot_id=cid,
            removed_name=name,
            removed_role=track.role,
            replacement_role=role,
            kick_method=kick_method,
        )
        self._write_action_event(
            "execution_result",
            action_id=action_id,
            result="replacement_scheduled",
            removed_bot_id=cid,
            removed_name=name,
            removed_role=track.role,
            replacement_role=role,
            kick_method=kick_method,
        )

    def _write_snapshot(self, snapshot, actions, evaluations) -> None:
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
                "learned_pressure_shift": self.learning.pressure_shift(),
                "alive": snapshot.alive,
                "engaged": snapshot.engaged,
                "idle": snapshot.idle,
                "far": snapshot.far,
                "player": {"health": snapshot.player_health, "armor": snapshot.player_armor},
                "recent_damage_taken": snapshot.recent_damage_taken,
                "recent_damage_dealt": snapshot.recent_damage_dealt,
                "hold_until": snapshot.hold_until,
                "roles": {str(cid): track.role for cid, track in self.director.tracks.items()},
                "actions": [
                    {"kind": a.kind, "reason": a.reason, "bot_id": a.bot_id, "role": a.role, "duration": a.duration}
                    for a in actions
                ],
                "evaluations": evaluations,
            }
            with self.telemetry_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")
        except Exception as exc:
            self.plugin._log(f"director telemetry failed: {exc}")

    def _write_action_event(self, event: str, **payload) -> None:
        try:
            self.runtime_dir.mkdir(parents=True, exist_ok=True)
            if self.actions_file.exists() and self.actions_file.stat().st_size > 2_000_000:
                backup = self.actions_file.with_suffix(".previous.jsonl")
                try:
                    backup.unlink(missing_ok=True)
                except Exception:
                    pass
                self.actions_file.replace(backup)
            row = {"kind": "director_action", "event": str(event), "time": self.now(), "mode": self.mode}
            row.update(payload)
            with self.actions_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        except Exception as exc:
            self.plugin._log(f"director action log failed: {exc}")

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
