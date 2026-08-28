#!/usr/bin/env python3
"""Director-managed Solo runtime.

This class deliberately subclasses the proven ``solo_arcade`` mode engine.
Quake Live still owns navigation, aiming, firing, physics and weapon behavior.
The Director layer only observes encounter pressure and manages bounded
composition/pacing decisions around the existing mode lifecycle.
"""
from __future__ import annotations

import json
import random
import time

import minqlx

try:
    from .solo_arcade import (
        BOT_ROSTER_RUNTIME,
        RUNTIME_DIR,
        clean_name,
        clamp,
        is_bot,
        is_player_object,
        solo_arcade,
    )
    from .solo_controller import Phase
    from .solo_director import ROLE_SPECS, SoloDirector, distance_between
except ImportError:
    from solo_arcade import (
        BOT_ROSTER_RUNTIME,
        RUNTIME_DIR,
        clean_name,
        clamp,
        is_bot,
        is_player_object,
        solo_arcade,
    )
    from solo_controller import Phase
    from solo_director import ROLE_SPECS, SoloDirector, distance_between


DIRECTOR_LOG_FILE = RUNTIME_DIR / "director.jsonl"


class solo_directed(solo_arcade):
    """Existing Solo modes plus the mode-aware encounter Director."""

    def __init__(self):
        super().__init__()
        self.director = SoloDirector(self.mode, self.difficulty, self.seed)
        self.director_roles: dict[int, str] = {}
        self.director_pending_roles: dict[str, list[str]] = {}
        self.director_next_tick = 0.0
        self.director_next_log = 0.0
        self.add_command("director", self.cmd_director)
        self._director_log("ready", {
            "profile": self.director.profile.name,
            "difficulty": self.difficulty,
            "recovery_enabled": self.director.profile.recovery_enabled,
        })

    # ---------- objective ownership ----------
    def clear_all_bots(self):
        super().clear_all_bots()
        if hasattr(self, "director"):
            self.director.reset()
            self.director_roles.clear()
            self.director_pending_roles.clear()

    def _spawn_objective_bots(self, names, skill, *, auto_clear=True):
        names = list(names)
        self.clear_all_bots()
        self.director.begin_objective(time.time())
        self.pending_replacements = 0
        token = self.controller.begin_objective(len(names), auto_clear=auto_clear)
        for index, name in enumerate(names):
            self._add_bot_later(name, skill, index * 0.15, token)
        return token

    def _add_bot_later(self, name, skill, delay, token):
        skill = min(clamp(int(skill), 1, 5), self.director.profile.skill_cap)

        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.PREPARING, Phase.ACTIVE):
                minqlx.console_command(f"addbot {name} {skill} blue")

        _add()

    def _add_replacement_bot(self, name=None, skill=None, delay=0.35):
        token = self.controller.token()
        name = name or random.choice(BOT_ROSTER_RUNTIME)
        skill = self.skill if skill is None else skill
        skill = min(clamp(int(skill), 1, 5), self.director.profile.skill_cap)
        delay = self.director.reinforcement_delay(time.time(), delay)
        role = self.director.role_for_spawn(self.controller.fulfilled_spawns + self.kills + 1)
        self._queue_director_role(name, role)

        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.ACTIVE):
                minqlx.console_command(f"addbot {name} {skill} blue")

        _add()

    def _queue_director_role(self, name, role):
        key = str(name).lower()
        self.director_pending_roles.setdefault(key, []).append(role)

    def _take_director_role(self, player, spawn_index):
        key = clean_name(player).lower()
        pending = self.director_pending_roles.get(key)
        if pending:
            role = pending.pop(0)
            if not pending:
                self.director_pending_roles.pop(key, None)
            return role
        special = "boss" if (self.current_plan or {}).get("boss") else None
        return self.director.role_for_spawn(spawn_index, special=special)

    # ---------- lifecycle hooks ----------
    def handle_player_spawn(self, player):
        if not is_player_object(player):
            return
        if not is_bot(player):
            return super().handle_player_spawn(player)

        self._put_team(player, "blue")
        if player.id in self.preactive_dead_ids:
            self._kick_bot_id(player.id)
            return

        spawn_index = self.controller.fulfilled_spawns
        role = self._take_director_role(player, spawn_index)
        activated = self.controller.enemy_spawned(player.id)
        self.director.register_bot(player.id, clean_name(player), role, time.time())
        self.director_roles[player.id] = role
        self._apply_bot_loadout(player, role)
        self._log(
            f"enemy spawn id={player.id} role={role} "
            f"fulfilled={self.controller.fulfilled_spawns}/{self.controller.expected_spawns} "
            f"alive={len(self.controller.enemy_ids)} phase={self.controller.phase.value}"
        )
        if activated:
            self._retire_preactive_dead()
            self.msg("^2OBJECTIVE LIVE")
            self._on_objective_activated()

    def handle_map(self, map_name, factory):
        super().handle_map(map_name, factory)
        if hasattr(self, "director"):
            self.director.reset()
            self.director_roles.clear()
            self.director_pending_roles.clear()
            self.director_next_tick = 0.0

    def _handle_bot_death(self, victim, killer, data):
        owned = victim.id in self.controller.enemy_ids
        if owned:
            self.director_roles.pop(victim.id, None)
            self.director.forget_bot(victim.id)
            if is_player_object(killer) and not is_bot(killer):
                self.director.note_human_kill(time.time())
        return super()._handle_bot_death(victim, killer, data)

    def _handle_human_death(self, victim):
        if self.controller.phase == Phase.ACTIVE:
            self.director.note_human_death(time.time())
        return super()._handle_human_death(victim)

    def handle_damage(self, target, attacker, damage, dflags, means_of_death):
        if is_player_object(target) and is_player_object(attacker) and attacker.id != target.id:
            self.director.note_damage(
                now=time.time(),
                damage=damage,
                attacker_id=attacker.id,
                target_id=target.id,
                attacker_is_bot=is_bot(attacker),
                target_is_bot=is_bot(target),
            )
        return super().handle_damage(target, attacker, damage, dflags, means_of_death)

    # ---------- role loadouts ----------
    def _apply_bot_loadout(self, player, role=None):
        plan = self.current_plan or {}
        role = role or self.director_roles.get(player.id, "skirmisher")
        role = role if role in ROLE_SPECS else "skirmisher"
        spec = ROLE_SPECS[role]
        try:
            # Roles change weapon opportunity, not aim or hidden damage.
            player.health = int(plan.get("health", 100)) if plan else 100
            player.armor = int(plan.get("armor", 25)) if plan else 25

            # Explicit mode contracts override generic role composition.
            if self.mode == "rocket_tag":
                self._give_single_weapon(player, 5)
                return

            if self.mode in ("arena_run", "boss_rush", "gauntlet_run") and plan:
                trial = {"rocket": 5, "lg": 6, "rail": 7, "plasma": 8}.get(plan.get("theme"))
                if trial:
                    self._give_single_weapon(player, trial)
                    return
                if plan.get("boss"):
                    self._give_single_weapon(player, 5)
                    return

            keys = {1: "g", 2: "mg", 3: "sg", 4: "gl", 5: "rl", 6: "lg", 7: "rg", 8: "pg"}
            weapon_kwargs = {"g": True}
            ammo_kwargs = {}
            for weapon in spec.weapons:
                key = keys.get(int(weapon))
                if not key:
                    continue
                weapon_kwargs[key] = True
                if key != "g":
                    ammo_kwargs[key] = 180
            player.weapons(reset=True, **weapon_kwargs)
            player.ammo(reset=True, **ammo_kwargs)
            player.weapon(spec.primary)
        except Exception as exc:
            self._log(f"Director role loadout failed: {exc}")

    # ---------- Director observation/action ----------
    def _player_position(self, player):
        try:
            pos = player.position()
            return (float(pos.x), float(pos.y), float(pos.z))
        except Exception:
            return None

    def _director_log(self, kind, payload):
        record = {
            "time": time.time(),
            "kind": str(kind),
            "mode": self.mode,
            "phase": self.controller.phase.value,
            **dict(payload or {}),
        }
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            with DIRECTOR_LOG_FILE.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
        except Exception:
            pass
        self._log(f"Director {kind}: {payload}")

    def _director_tick(self, now=None):
        now = time.time() if now is None else float(now)
        if now < self.director_next_tick:
            return
        self.director_next_tick = now + 0.25
        human = self.primary_player()
        if human is None:
            return

        human_pos = self._player_position(human)
        rows = []
        for bot in self.bot_players():
            if bot.id not in self.controller.enemy_ids:
                continue
            rows.append({
                "id": bot.id,
                "name": clean_name(bot),
                "role": self.director_roles.get(bot.id, "skirmisher"),
                "distance": distance_between(human_pos, self._player_position(bot)),
            })
        try:
            health = int(human.health)
            armor = int(human.armor)
        except Exception:
            health, armor = 100, 0

        snapshot, actions = self.director.tick(
            now=now,
            player_health=health,
            player_armor=armor,
            bots=rows,
            active=self.controller.phase == Phase.ACTIVE,
        )
        if now >= self.director_next_log:
            self.director_next_log = now + 2.0
            self._director_log("snapshot", {
                "summary": snapshot.summary(),
                "pressure": round(snapshot.pressure, 2),
                "engaged": snapshot.engaged,
                "alive": snapshot.alive,
                "idle": snapshot.idle,
                "far": snapshot.far,
            })

        for action in actions:
            self._director_log("action", {
                "action": action.kind,
                "reason": action.reason,
                "bot_id": action.bot_id,
                "role": action.role,
                "duration": action.duration,
            })
            if action.kind == "recover_bot" and action.bot_id is not None:
                self._director_recover_bot(action.bot_id, action.role, action.reason)

    def _director_recover_bot(self, bot_id, role, reason):
        # Recovery is neutral objective maintenance: no kill, no clear, no score.
        if self.controller.phase != Phase.ACTIVE or bot_id not in self.controller.enemy_ids:
            return False
        bot = next((item for item in self.bot_players() if item.id == bot_id), None)
        if bot is None:
            return False

        name = clean_name(bot).lower() or random.choice(BOT_ROSTER_RUNTIME)
        token = self.controller.token()
        self.controller.remove_enemy(bot_id)
        self.director.forget_bot(bot_id)
        self.director_roles.pop(bot_id, None)
        self._queue_director_role(name, role or "skirmisher")
        try:
            bot.kick("Director recovery")
        except Exception:
            try:
                minqlx.console_command(f"kick {bot_id}")
            except Exception:
                return False

        skill = min(self.skill, self.director.profile.skill_cap)

        @minqlx.delay(0.35)
        def _replace():
            if self.controller.token_valid(token, Phase.ACTIVE):
                minqlx.console_command(f"addbot {name} {skill} blue")

        _replace()
        self._director_log("recover", {
            "old_bot_id": bot_id,
            "name": name,
            "role": role,
            "reason": reason,
        })
        return True

    def handle_frame(self):
        self._director_tick(time.time())
        return super().handle_frame()

    # ---------- diagnostics ----------
    def cmd_director(self, player, msg, channel):
        snapshot = self.director.last_snapshot
        if snapshot is None:
            player.tell(f"^6DIRECTOR:^7 {self.director.profile.name} — waiting for encounter data.")
            return
        player.tell("^6DIRECTOR:^7 " + snapshot.summary())
        roles = {}
        for role in self.director_roles.values():
            roles[role] = roles.get(role, 0) + 1
        if roles:
            player.tell("^7Roles: " + ", ".join(f"{name} x{count}" for name, count in sorted(roles.items())))
        if self.director.should_hold_reinforcements(time.time()):
            player.tell("^3Director recovery window:^7 holding future reinforcements briefly.")

    def cmd_help(self, player, msg, channel):
        super().cmd_help(player, msg, channel)
        player.tell("^7Director: ^3!director ^7shows current pressure, engagement and role mix.")
