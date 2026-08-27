#!/usr/bin/env python3
"""Quake Live Launcher v5 scripted Solo runtime for shinqlx/minqlx.

Quake Live owns physics, navigation and combat.  This plugin owns objectives,
progression, bot ownership, run completion and the Solo movement layer.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import time
from pathlib import Path

import minqlx

try:  # Runtime: minqlx loads this inside its plugin package.
    from .modes.gun_game import GunGameState
    from .modes.horde import HordeState
    from .solo_controller import Phase, SoloController
    from .solo_core import (
        BOT_ROSTER, RARITY_COLOR, UPGRADE_BY_ID, advance_round, load_state,
        new_state, pick_upgrade, roll_upgrade_choices, round_plan, save_state,
        upgrade_effects,
    )
except ImportError:  # Direct local/unit-test import fallback.
    from modes.gun_game import GunGameState
    from modes.horde import HordeState
    from solo_controller import Phase, SoloController
    from solo_core import (
        BOT_ROSTER, RARITY_COLOR, UPGRADE_BY_ID, advance_round, load_state,
        new_state, pick_upgrade, roll_upgrade_choices, round_plan, save_state,
        upgrade_effects,
    )

SESSION_FILE = Path.home() / ".config/quake-live-launcher/solo_session.json"
STATE_FILE = Path.home() / ".config/quake-live-launcher/arena_run_state_v5.json"
RUNTIME_DIR = Path.home() / ".local/share/quake-live-launcher/solo_runtime"
PLUGIN_READY_FILE = RUNTIME_DIR / "plugin_ready.json"
PLUGIN_VERSION = "5.0-alpha"

SUPPORTED_MODES = {
    "arena_run", "horde", "gun_game", "boss_rush", "wipeout_solo",
    "gauntlet_run", "last_stand", "one_life", "bounty_hunt", "rocket_tag",
    "movement_hunter", "predator", "accuracy_trial", "speedrun_combat",
    "random_loadout",
}
BOT_ROSTER_RUNTIME = tuple(BOT_ROSTER[:-1])
WEAPON_NAMES = {
    1: "Gauntlet", 2: "Machine Gun", 3: "Shotgun", 4: "Grenade Launcher",
    5: "Rocket Launcher", 6: "Lightning Gun", 7: "Railgun", 8: "Plasma Gun",
}


def clamp(value, low, high):
    return max(low, min(high, value))


def is_player_object(value) -> bool:
    return value is not None and hasattr(value, "id") and hasattr(value, "steam_id")


def is_bot(player) -> bool:
    try:
        return int(player.steam_id) > 90_000_000_000_000_000
    except Exception:
        return False


def clean_name(player) -> str:
    try:
        return re.sub(r"\^[0-9]", "", str(player.name)).strip()
    except Exception:
        return "bot"


class solo_arcade(minqlx.Plugin):
    def __init__(self):
        self.session = self._load_session()
        self.mode = str(self.session.get("mode", "horde"))
        self.seed = int(self.session.get("seed", int(time.time())))
        self.skill = clamp(int(self.session.get("skill", 3)), 1, 5)
        self.difficulty = str(self.session.get("difficulty", "normal"))
        self.length = int(self.session.get("length", 20))
        self.maps = list(self.session.get("maps") or [self.session.get("map", "campgrounds")])
        self.map_pools = dict(self.session.get("map_pools") or {})
        self.movement = dict(self.session.get("movement") or {})
        self.air_control = str(self.movement.get("air_control", "enhanced")).lower()
        self.side_thrusters = bool(self.movement.get("side_thrusters", True))
        self.dash_strength = float(self.movement.get("dash_strength", 340))
        self.ground_dash_hop = float(self.movement.get("ground_dash_hop", 155))
        self.base_dash_charges = max(1, min(3, int(self.movement.get("dash_charges", 1))))

        self.controller = SoloController(self.mode)
        self.horde = HordeState(self.seed) if self.mode == "horde" else None
        self.gun_game = GunGameState() if self.mode == "gun_game" else None
        self.run = None
        self.current_plan = None
        self.player_id = None
        self.mode_started = False
        self.pending_resume_payload = None
        self.preactive_dead_ids = set()
        self.pending_replacements = 0
        self.start_time = time.time()
        self.kills = 0
        self.player_deaths = 0

        self.boss_round = 1
        self.gauntlet_stage = 1
        self.gauntlet_kind = "survival"
        self.wipeout_round = 1
        self.wipeout_respawn_level = 0
        self.wipeout_generation = 0
        self.target_bot_id = None
        self.target_name = None
        self.target_score = 0
        self.challenge_goal = 0
        self.random_round = 1

        self.last_damage_time = {}
        self.last_hurt_time = {}
        self.last_regen_tick = {}
        self.lg_streak = {}
        self.rail_hits = {}

        self.dash_ready = {}
        self.dash_used = {}
        self.airborne = set()
        self.prev_vz = {}
        self.ground_ticks = {}

        self._require_runtime_contract()
        self._configure_engine()

        self.add_hook("player_loaded", self.handle_player_loaded)
        self.add_hook("player_spawn", self.handle_player_spawn)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("death", self.handle_death)
        self.add_hook("damage", self.handle_damage)
        self.add_hook("map", self.handle_map)
        self.add_hook("new_game", self.handle_new_game)
        self.add_hook("frame", self.handle_frame)
        self.add_hook("client_command", self.handle_client_command)
        self.add_hook("unload", self.handle_unload)

        self.add_command(("run", "solo"), self.cmd_run)
        self.add_command("solohelp", self.cmd_help)
        self.add_command(("pick", "choose"), self.cmd_pick)
        self.add_command(("upgrades", "build"), self.cmd_upgrades)
        self.add_command("dash", self.cmd_dash)

        self.controller.wait_for_player()
        self._write_ready(True)
        self._log(f"plugin ready mode={self.mode} seed={self.seed} skill={self.skill}")

    # ---------- runtime/bootstrap ----------
    def _load_session(self):
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _require_runtime_contract(self):
        if self.mode not in SUPPORTED_MODES:
            raise RuntimeError(f"unsupported scripted Solo mode: {self.mode}")
        if not hasattr(minqlx, "allow_single_player"):
            raise RuntimeError("shinqlx/minqlx does not expose allow_single_player()")
        try:
            zmq_enabled = int(self.get_cvar("zmq_stats_enable") or 0)
        except Exception:
            zmq_enabled = 0
        if zmq_enabled != 1:
            raise RuntimeError("zmq_stats_enable must be 1 before solo_arcade loads")

    def _configure_engine(self):
        minqlx.allow_single_player(True)
        cvars = {
            "sv_hostname": "Quake Live // Solo Engine v5",
            "bot_enable": "1", "bot_thinktime": "0", "bot_challenge": "1",
            "bot_minplayers": "0", "fraglimit": "0", "timelimit": "0",
            "capturelimit": "0", "roundlimit": "0", "scorelimit": "0",
            "g_doWarmup": "0", "g_warmup": "0", "sv_warmupReadyPercentage": "0",
            "g_warmupDelay": "0", "g_friendlyFire": "0", "g_teamForceBalance": "0",
            "g_teamSizeMin": "0", "g_teamSizeMax": "0",
        }
        for name, value in cvars.items():
            try:
                self.set_cvar(name, value)
            except Exception as exc:
                self._log(f"cvar {name} failed: {exc}")
        self._configure_movement()

    def _configure_movement(self):
        profiles = {"standard": (0, 1.0), "enhanced": (1, 1.35), "high": (1, 1.75)}
        air_control, air_accel = profiles.get(self.air_control, profiles["enhanced"])
        for command in (
            f"set pmove_AirControl {air_control}",
            f"set pmove_AirAccel {air_accel:.2f}",
            "set pmove_RampJump 1",
        ):
            try:
                minqlx.console_command(command)
            except Exception as exc:
                self._log(f"movement cvar failed: {command}: {exc}")

    def _write_ready(self, ready: bool, error: str | None = None):
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "ready": bool(ready), "mode": self.mode, "version": PLUGIN_VERSION,
                "pid": os.getpid(), "time": time.time(), "error": error,
            }
            temp = PLUGIN_READY_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temp.replace(PLUGIN_READY_FILE)
        except Exception as exc:
            self._log(f"could not write plugin readiness: {exc}")

    def _log(self, message):
        try:
            minqlx.console_print(f"[solo_arcade:v5] {message}")
        except Exception:
            pass

    def handle_unload(self, plugin):
        try:
            PLUGIN_READY_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def handle_new_game(self):
        minqlx.allow_single_player(True)
        self._configure_engine()

    def handle_map(self, map_name, factory):
        minqlx.allow_single_player(True)
        self._configure_engine()
        self.airborne.clear(); self.dash_used.clear(); self.ground_ticks.clear()
        self._log(f"map={map_name} factory={factory} phase={self.controller.phase.value}")

    # ---------- player/bot helpers ----------
    def human_players(self):
        players = []
        teams = self.teams()
        for team in ("free", "red", "blue"):
            players.extend(teams.get(team, []))
        return [p for p in players if not is_bot(p)]

    def bot_players(self):
        players = []
        teams = self.teams()
        for team in ("free", "red", "blue"):
            players.extend(teams.get(team, []))
        return [p for p in players if is_bot(p)]

    def primary_player(self):
        if self.player_id is not None:
            for player in self.human_players():
                if player.id == self.player_id:
                    return player
        humans = self.human_players()
        return humans[0] if humans else None

    def _put_team(self, player, team):
        try:
            if getattr(player, "team", None) != team:
                player.put(team)
        except Exception as exc:
            self._log(f"could not put {getattr(player, 'id', '?')} on {team}: {exc}")

    def clear_all_bots(self):
        for bot in list(self.bot_players()):
            try:
                bot.kick("solo objective reset")
            except Exception:
                try:
                    minqlx.console_command(f"kick {bot.id}")
                except Exception:
                    pass
        self.controller.enemy_ids.clear()
        self.preactive_dead_ids.clear()

    def _spawn_objective_bots(self, names, skill, *, auto_clear=True):
        names = list(names)
        self.clear_all_bots()
        self.pending_replacements = 0
        token = self.controller.begin_objective(len(names), auto_clear=auto_clear)
        for index, name in enumerate(names):
            self._add_bot_later(name, skill, index * 0.15, token)
        return token

    def _add_bot_later(self, name, skill, delay, token):
        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.PREPARING, Phase.ACTIVE):
                minqlx.console_command(f"addbot {name} {clamp(int(skill), 1, 5)} blue")
        _add()

    def _add_replacement_bot(self, name=None, skill=None, delay=0.35):
        token = self.controller.token()
        name = name or random.choice(BOT_ROSTER_RUNTIME)
        skill = self.skill if skill is None else skill
        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.ACTIVE):
                minqlx.console_command(f"addbot {name} {clamp(int(skill), 1, 5)} blue")
        _add()

    def _kick_bot_id(self, client_id):
        for bot in list(self.bot_players()):
            if bot.id == client_id:
                try:
                    bot.kick("solo enemy defeated")
                except Exception:
                    try: minqlx.console_command(f"kick {client_id}")
                    except Exception: pass
                return

    def _retire_preactive_dead(self):
        ids = list(self.preactive_dead_ids)
        self.preactive_dead_ids.clear()
        for cid in ids:
            self._kick_bot_id(cid)
        while self.pending_replacements > 0:
            self.pending_replacements -= 1
            self._add_replacement_bot(delay=0.1 + self.pending_replacements * 0.08)

    # ---------- lifecycle hooks ----------
    def handle_player_loaded(self, player):
        if not is_player_object(player) or is_bot(player):
            return
        self.player_id = player.id
        minqlx.allow_single_player(True)
        self._put_team(player, "red")
        if self.controller.pending_map:
            payload = self.controller.resume_map_if_ready(self.current_map_name(), player.id)
            if payload is not None:
                self.pending_resume_payload = payload
                return
        self.controller.player_loaded(player.id)

    def handle_player_spawn(self, player):
        if not is_player_object(player):
            return
        if is_bot(player):
            self._put_team(player, "blue")
            if player.id in self.preactive_dead_ids:
                self._kick_bot_id(player.id)
                return
            activated = self.controller.enemy_spawned(player.id)
            self._apply_bot_loadout(player)
            self._log(
                f"enemy spawn id={player.id} fulfilled={self.controller.fulfilled_spawns}/"
                f"{self.controller.expected_spawns} alive={len(self.controller.enemy_ids)} "
                f"phase={self.controller.phase.value}"
            )
            if activated:
                self._retire_preactive_dead()
                self.msg("^2OBJECTIVE LIVE")
                self._on_objective_activated()
            return

        self.player_id = player.id
        self._put_team(player, "red")
        self._apply_human_loadout(player)
        if self.pending_resume_payload is not None:
            payload = self.pending_resume_payload
            self.pending_resume_payload = None
            self.mode_started = True
            self._resume_payload(payload)
            return
        if not self.mode_started and self.controller.phase == Phase.PREPARING:
            self.mode_started = True
            self._start_selected_mode()

    def handle_player_disconnect(self, player, reason):
        if is_player_object(player) and not is_bot(player) and player.id == self.player_id:
            self.controller.finish()
            self.clear_all_bots()

    def _on_objective_activated(self):
        if self.mode in ("bounty_hunt", "rocket_tag") and self.target_bot_id is None:
            self._choose_target()

    # ---------- mode bootstrap ----------
    def _start_selected_mode(self):
        self.start_time = time.time()
        if self.mode == "arena_run": self._start_arena_run()
        elif self.mode == "horde": self._start_horde_wave()
        elif self.mode == "gun_game": self._start_gun_game()
        elif self.mode == "boss_rush": self._start_boss()
        elif self.mode == "wipeout_solo": self._start_wipeout()
        elif self.mode == "gauntlet_run": self._start_gauntlet_stage()
        elif self.mode == "last_stand": self._start_continuous(5, "^6LAST STAND^7 — survive as long as you can.", goal=0)
        elif self.mode == "one_life": self._start_continuous(5, "^6ONE LIFE^7 — reach 12 kills without dying.", goal=12)
        elif self.mode == "bounty_hunt": self._start_bounty_hunt()
        elif self.mode == "rocket_tag": self._start_rocket_tag()
        elif self.mode == "movement_hunter": self._start_movement_hunter()
        elif self.mode == "predator": self._start_continuous(5, "^6PREDATOR^7 — reach 25 kills; kills restore health.", goal=25)
        elif self.mode == "accuracy_trial": self._start_continuous(4, "^6ACCURACY TRIAL^7 — clear 20 LG kills and review your accuracy.", goal=20)
        elif self.mode == "speedrun_combat": self._start_continuous(5, "^6SPEEDRUN COMBAT^7 — clear 15 kills as fast as possible.", goal=15)
        elif self.mode == "random_loadout": self._start_continuous(5, "^6RANDOM LOADOUT^7 — reach 20 kills; loadout rerolls every 4 kills and death.", goal=20)

    # ---------- Horde ----------
    def _start_horde_wave(self):
        if not self.horde or self.horde.complete:
            return
        plan = self.horde.plan()
        elite = " ^1ELITE" if plan["elite"] else ""
        self.msg(f"^6HORDE WAVE {plan['wave']}^7 — {plan['count']} enemies{elite}")
        self._spawn_objective_bots(plan["bots"], plan["skill"], auto_clear=True)

    def _horde_clear(self):
        if not self.horde or self.horde.complete:
            return
        self.horde.clear_wave()
        self._schedule(1.0, self._start_horde_wave, Phase.BETWEEN_ROUNDS)

    # ---------- Gun Game ----------
    def _start_gun_game(self):
        self.msg(f"^6GUN GAME^7 — start with {self.gun_game.weapon_name}")
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)

    def _gun_game_kill(self, killer):
        if not self.gun_game or self.gun_game.complete:
            return
        if self.gun_game.scored_kill():
            self._finish_mode("^2GUN GAME COMPLETE!^7 Gauntlet kill finished the ladder.")
            return
        self._give_single_weapon(killer, self.gun_game.weapon)
        killer.tell(f"^2ADVANCE:^7 {self.gun_game.weapon_name}")

    # ---------- Boss Rush ----------
    def _start_boss(self):
        if self.boss_round > 10:
            self._finish_mode("^2BOSS RUSH COMPLETE!^7 Ten bosses defeated.")
            return
        bosses = ["keel", "slash", "doom", "xaero"]
        name = bosses[(self.boss_round - 1) % len(bosses)]
        self.current_plan = {
            "boss": True, "theme": "boss", "health": 500 + self.boss_round * 250,
            "armor": 150 + self.boss_round * 125, "damage_mult": 1.0 + self.boss_round * 0.08,
        }
        self.msg(f"^1BOSS {self.boss_round}/10:^7 {name.upper()}")
        self._spawn_objective_bots([name], min(5, 3 + self.boss_round // 2), auto_clear=True)

    # ---------- Wipeout ----------
    def _start_wipeout(self):
        if self.wipeout_round > 5:
            self._finish_mode("^2WIPEOUT SOLO COMPLETE!^7 Five squads wiped simultaneously.")
            return
        self.wipeout_generation += 1
        self.wipeout_respawn_level = 0
        self.msg(f"^6WIPEOUT ROUND {self.wipeout_round}/5:^7 eliminate the whole squad at once.")
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki"], min(5, self.skill + (self.wipeout_round - 1) // 2), auto_clear=True)

    def _schedule_wipeout_respawn(self, name):
        self.wipeout_respawn_level += 1
        delay = min(25.0, 2.0 + self.wipeout_respawn_level * 2.0)
        generation = self.wipeout_generation
        token = self.controller.token()
        @minqlx.delay(delay)
        def _respawn():
            if generation != self.wipeout_generation:
                return
            if not self.controller.token_valid(token, Phase.ACTIVE):
                return
            minqlx.console_command(f"addbot {name} {min(5, self.skill + (self.wipeout_round - 1) // 2)} blue")
        _respawn()

    # ---------- Gauntlet ----------
    def _start_gauntlet_stage(self):
        if self.gauntlet_stage > 10:
            self._finish_mode("^2THE GAUNTLET COMPLETE!^7 Ten stages cleared.")
            return
        kinds = ["rail", "rocket", "lg", "survival", "duel", "plasma", "boss"]
        kind = kinds[(self.gauntlet_stage - 1) % len(kinds)]
        self.gauntlet_kind = kind
        target_map = self._choose_session_map(kind, 100 + self.gauntlet_stage)
        if target_map and target_map != self.current_map_name():
            self._request_map(target_map, {"kind": "gauntlet", "stage": self.gauntlet_stage, "trial": kind})
            return
        self._launch_gauntlet_stage(kind)

    def _launch_gauntlet_stage(self, kind):
        self.gauntlet_kind = kind
        self.msg(f"^6GAUNTLET {self.gauntlet_stage}/10:^7 {kind.upper()}")
        if kind == "boss":
            name = ["keel", "slash", "doom", "xaero"][(self.gauntlet_stage // 2) % 4]
            self.current_plan = {"boss": True, "theme": "boss", "health": 650 + self.gauntlet_stage * 60, "armor": 250, "damage_mult": 1.25}
            self._spawn_objective_bots([name], min(5, self.skill + 1), auto_clear=True)
            return
        count = 3 if kind == "duel" else 6
        rng = random.Random(self.seed + self.gauntlet_stage * 53)
        names = rng.sample(list(BOT_ROSTER_RUNTIME), k=min(count, len(BOT_ROSTER_RUNTIME)))
        self.current_plan = {"theme": kind, "health": 120, "armor": 25, "damage_mult": 1.0}
        self._spawn_objective_bots(names, min(5, self.skill + self.gauntlet_stage // 4), auto_clear=True)

    # ---------- Continuous challenge modes ----------
    def _start_continuous(self, count, message, *, goal):
        self.challenge_goal = int(goal)
        self.msg(message)
        names = list(BOT_ROSTER_RUNTIME[:count])
        self._spawn_objective_bots(names, self.skill, auto_clear=False)

    def _start_bounty_hunt(self):
        self.target_score = 0
        self.challenge_goal = 8
        self.msg("^6BOUNTY HUNT^7 — eliminate 8 marked targets.")
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)

    def _start_rocket_tag(self):
        self.target_score = 0
        self.challenge_goal = 10
        self.msg("^6ROCKET TAG^7 — rocket-only; eliminate 10 marked targets.")
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)

    def _start_movement_hunter(self):
        self.msg("^6MOVEMENT HUNTER^7 — survive 90 seconds against five armed bots.")
        token = self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)
        @minqlx.delay(90.0)
        def _finish():
            if self.controller.token_valid(token, Phase.ACTIVE):
                self._finish_mode(f"^2MOVEMENT HUNTER CLEAR!^7 Survived 90 seconds with {self.kills} kills.")
        _finish()

    def _choose_target(self):
        bots = [bot for bot in self.bot_players() if bot.id in self.controller.enemy_ids]
        if not bots:
            self.target_bot_id = None; self.target_name = None
            return
        rng = random.Random(self.seed + self.target_score * 97 + self.kills * 13)
        target = rng.choice(bots)
        self.target_bot_id = target.id
        self.target_name = clean_name(target)
        self.msg(f"^3TARGET:^7 {self.target_name}")

    # ---------- Arena Run ----------
    def current_map_name(self):
        try:
            return str(self.game.map).lower()
        except Exception:
            try: return str(self.get_cvar("mapname") or self.session.get("map", "")).lower()
            except Exception: return str(self.session.get("map", "")).lower()

    def _choose_session_map(self, pool_key, salt=0):
        pool = list(self.map_pools.get(pool_key) or self.maps)
        if not pool:
            return None
        if self.mode == "arena_run" and self.run and self.run.round == 1 and pool_key == "normal":
            first = str(self.session.get("map") or "").lower()
            if first in [str(item).lower() for item in pool]:
                return first
        rng = random.Random(self.seed + int(salt) * 7919 + sum(ord(c) for c in str(pool_key)))
        return str(rng.choice(pool)).lower()

    def _request_map(self, target_map, payload):
        self.controller.request_map(target_map, payload)
        self.clear_all_bots()
        self.msg(f"^5NEXT ARENA:^7 {target_map}")
        minqlx.console_command(f"map {target_map} tdm")

    def _resume_payload(self, payload):
        kind = payload.get("kind") if isinstance(payload, dict) else None
        if kind == "arena": self._launch_arena_plan(payload["plan"])
        elif kind == "gauntlet": self._launch_gauntlet_stage(payload["trial"])
        else: self.controller.fail(f"unknown map resume payload: {payload}")

    def _start_arena_run(self):
        resume = bool(self.session.get("continue_run"))
        self.run = load_state(STATE_FILE) if resume else None
        if not self.run or self.run.complete:
            self.run = new_state(self.seed, self.difficulty, self.length)
            save_state(STATE_FILE, self.run)
        self.msg(f"^2ARENA RUN^7 seed ^3{self.run.seed}^7 round ^3{self.run.round}^7 lives ^3{self.run.lives}")
        self._start_arena_round()

    def _start_arena_round(self):
        if not self.run or self.run.complete:
            return
        if self.run.waiting_for_pick:
            self._show_upgrade_choices(); return
        plan = round_plan(self.run)
        self.current_plan = plan
        theme = plan.get("theme", "normal")
        target_map = self._choose_session_map(theme, self.run.round)
        if target_map and target_map != self.current_map_name():
            self._request_map(target_map, {"kind": "arena", "plan": plan})
            return
        self._launch_arena_plan(plan)

    def _launch_arena_plan(self, plan):
        self.current_plan = plan
        theme = plan.get("theme", "normal")
        if plan.get("boss"):
            self.msg(f"^1BOSS ROUND {self.run.round}^7 — ^3{plan['bots'][0].upper()}")
        elif theme in ("rail", "rocket", "lg"):
            self.msg(f"^6ROUND {self.run.round}^7 — ^3{theme.upper()} TRIAL")
        elif theme == "elite":
            self.msg(f"^1ELITE ROUND {self.run.round}^7 — {plan['count']} enemies")
        else:
            self.msg(f"^6ROUND {self.run.round}^7 — {plan['count']} enemies")
        self._spawn_objective_bots(plan["bots"], plan["skill"], auto_clear=True)

    def _arena_clear(self):
        if not self.run:
            return
        if advance_round(self.run):
            save_state(STATE_FILE, self.run)
            self._finish_mode(f"^2ARENA RUN COMPLETE!^7 Rounds cleared: ^3{self.run.round}")
            return
        roll_upgrade_choices(self.run)
        save_state(STATE_FILE, self.run)
        self.msg("^2ROUND CLEARED.^7 Choose one upgrade:")
        self._show_upgrade_choices()

    def _show_upgrade_choices(self, player=None):
        if not self.run or not self.run.choices:
            return
        target = player.tell if player else self.msg
        for index, uid in enumerate(self.run.choices, 1):
            upgrade = UPGRADE_BY_ID[uid]
            color = RARITY_COLOR.get(upgrade["rarity"], "^7")
            target(f"^3!pick {index} ^7— {color}{upgrade['name']} ^7[{upgrade['rarity'].upper()}] — {upgrade['text']}")

    def cmd_pick(self, player, msg, channel):
        if self.mode != "arena_run" or not self.run:
            player.tell("^7Upgrade picks are only used in Arena Run."); return
        if len(msg) < 2:
            self._show_upgrade_choices(player); return
        try:
            result = pick_upgrade(self.run, int(msg[1]))
        except Exception as exc:
            player.tell(f"^1{exc}"); return
        upgrade = result["upgrade"]
        self.msg(f"^2PICKED:^7 {upgrade['name']} — {upgrade['text']}")
        for synergy in result["synergies"]:
            self.msg(f"^6SYNERGY UNLOCKED:^7 {synergy['name']} — {synergy['text']}")
        save_state(STATE_FILE, self.run)
        self._apply_human_loadout(player)
        self._start_arena_round()

    def cmd_upgrades(self, player, msg, channel):
        if self.mode != "arena_run" or not self.run:
            player.tell("^7This mode has no roguelite upgrade build."); return
        if not self.run.upgrades:
            player.tell("^7No upgrades yet."); return
        items = [f"{UPGRADE_BY_ID[uid]['name']} x{stacks}" for uid, stacks in self.run.upgrades.items() if uid in UPGRADE_BY_ID]
        player.tell("^6BUILD:^7 " + ", ".join(items))
        if self.run.synergies:
            player.tell("^6SYNERGIES:^7 " + ", ".join(self.run.synergies))

    # ---------- objective completion/death ----------
    def handle_death(self, victim, killer, data):
        if not is_player_object(victim):
            return
        if is_bot(victim):
            self._handle_bot_death(victim, killer, data)
            return
        self._handle_human_death(victim)

    def _handle_bot_death(self, victim, killer, data):
        if victim.id not in self.controller.enemy_ids:
            self._log(f"ignored death of unowned bot id={victim.id}")
            return
        phase_before = self.controller.phase
        killer_human = is_player_object(killer) and not is_bot(killer)
        killer_bot = is_player_object(killer) and is_bot(killer)
        if killer_bot:
            self.controller.fail(f"bot-vs-bot kill detected ({killer.id}->{victim.id}); team sandbox contract failed")
            self.clear_all_bots()
            self.msg("^1SOLO ENGINE CONTRACT FAILURE:^7 bots damaged each other; see diagnostics.")
            return

        if killer_human:
            self.kills += 1
            self._on_human_kill(killer, victim)

        cleared = self.controller.enemy_died(victim.id)
        if phase_before == Phase.PREPARING:
            self.preactive_dead_ids.add(victim.id)
            if self.mode in self._continuous_modes():
                self.pending_replacements += 1
        else:
            self._kick_bot_id(victim.id)

        if self.mode == "wipeout_solo" and phase_before == Phase.ACTIVE and not cleared:
            self._schedule_wipeout_respawn(clean_name(victim).lower())

        if cleared:
            self._objective_cleared()
            return

        if phase_before == Phase.ACTIVE and self.mode in self._continuous_modes():
            self._add_replacement_bot()

    def _continuous_modes(self):
        return {
            "gun_game", "last_stand", "one_life", "bounty_hunt", "rocket_tag",
            "movement_hunter", "predator", "accuracy_trial", "speedrun_combat",
            "random_loadout",
        }

    def _objective_cleared(self):
        if self.mode == "horde": self._horde_clear()
        elif self.mode == "arena_run": self._arena_clear()
        elif self.mode == "boss_rush":
            self.boss_round += 1; self._schedule(1.0, self._start_boss, Phase.BETWEEN_ROUNDS)
        elif self.mode == "wipeout_solo":
            self.msg("^2WIPEOUT!^7 Enemy squad eliminated simultaneously.")
            self.wipeout_generation += 1
            self.wipeout_round += 1
            self._schedule(1.0, self._start_wipeout, Phase.BETWEEN_ROUNDS)
        elif self.mode == "gauntlet_run":
            self.gauntlet_stage += 1; self._schedule(1.0, self._start_gauntlet_stage, Phase.BETWEEN_ROUNDS)

    def _on_human_kill(self, killer, victim):
        if self.mode == "gun_game":
            self._gun_game_kill(killer)
        elif self.mode == "arena_run":
            self._arena_human_kill(killer)
        elif self.mode == "predator":
            try: killer.health = min(200, int(killer.health) + 30)
            except Exception: pass
        elif self.mode in ("bounty_hunt", "rocket_tag") and victim.id == self.target_bot_id:
            self.target_score += 1
            self.msg(f"^2TARGET ELIMINATED:^7 {self.target_name or 'bounty'} ({self.target_score}/{self.challenge_goal})")
            self.target_bot_id = None; self.target_name = None
            if self.target_score >= self.challenge_goal:
                self._finish_mode("^2TARGET CHALLENGE COMPLETE!")
                return
            self._schedule_target_refresh()

        if self.mode == "one_life" and self.kills >= 12:
            self._finish_mode("^2ONE LIFE CLEAR!^7 12 kills without dying.")
        elif self.mode == "predator" and self.kills >= 25:
            self._finish_mode("^2PREDATOR COMPLETE!^7 25-kill streak reached.")
        elif self.mode == "accuracy_trial" and self.kills >= 20:
            self._finish_mode("^2ACCURACY TRIAL COMPLETE!^7 20 LG kills cleared; review final weapon accuracy.")
        elif self.mode == "speedrun_combat" and self.kills >= 15:
            self._finish_mode(f"^2SPEEDRUN COMPLETE!^7 {time.time() - self.start_time:.2f} seconds")
        elif self.mode == "random_loadout":
            if self.kills >= 20:
                self._finish_mode("^2RANDOM LOADOUT COMPLETE!^7 20 kills cleared.")
            elif self.kills % 4 == 0:
                self.random_round += 1
                self._apply_human_loadout(killer)

    def _schedule_target_refresh(self):
        token = self.controller.token()
        @minqlx.delay(0.8)
        def _pick():
            if self.controller.token_valid(token, Phase.ACTIVE):
                self._choose_target()
        _pick()

    def _handle_human_death(self, victim):
        if self.controller.phase in (Phase.COMPLETE, Phase.FAILED):
            return
        self.player_deaths += 1
        if self.mode == "arena_run" and self.run:
            if self.controller.phase == Phase.ACTIVE:
                self.run.lives -= 1
                save_state(STATE_FILE, self.run)
                self.msg(f"^1LIFE LOST.^7 {max(0, self.run.lives)} lives remaining.")
                if self.run.lives <= 0:
                    self.run.complete = True; save_state(STATE_FILE, self.run)
                    self._finish_mode(f"^1ARENA RUN OVER.^7 Reached round {self.run.round}.")
            return
        if self.mode in ("horde", "boss_rush", "wipeout_solo", "gauntlet_run", "last_stand", "one_life", "movement_hunter", "predator"):
            if self.mode == "horde" and self.horde: self.horde.player_died()
            self._finish_mode(f"^1RUN OVER.^7 Kills: {self.kills}  Time: {time.time() - self.start_time:.1f}s")
            self._spectate_after_death(victim)
        elif self.mode == "random_loadout":
            self.random_round += 1

    def _finish_mode(self, message):
        if self.controller.phase in (Phase.COMPLETE, Phase.FAILED):
            return
        self.controller.finish()
        self.clear_all_bots()
        self.msg(message)

    def _schedule(self, delay, callback, required_phase):
        token = self.controller.token()
        @minqlx.delay(delay)
        def _run():
            if self.controller.token_valid(token, required_phase):
                callback()
        _run()

    def _spectate_after_death(self, player):
        @minqlx.delay(0.5)
        def _spec():
            try: player.put("spectator")
            except Exception: pass
        _spec()

    # ---------- loadouts/upgrades ----------
    def _arena_max_health(self):
        effects = upgrade_effects(self.run) if self.run else {}
        hp = int(125 + effects.get("max_health", 0))
        if effects.get("health_cap"):
            hp = min(hp, int(effects["health_cap"]))
        return max(1, hp)

    def _arena_human_kill(self, killer):
        if not self.run:
            return
        effects = upgrade_effects(self.run)
        heal = int(effects.get("kill_heal", 0))
        if heal:
            try: killer.health = min(self._arena_max_health(), int(killer.health) + heal)
            except Exception: pass
        ammo = int(effects.get("ammo_on_kill", 0))
        if ammo:
            try:
                current = killer.ammo()
                kwargs = {}
                for key in ("mg", "sg", "gl", "rl", "lg", "rg", "pg"):
                    value = getattr(current, key, 0)
                    kwargs[key] = min(250, int(value) + ammo)
                killer.ammo(**kwargs)
            except Exception: pass
        if effects.get("quad_burst") and self.kills % 5 == 0:
            try: killer.powerups(quad=5); killer.tell("^1QUAD BURST!")
            except Exception: pass

    def _apply_human_loadout(self, player):
        try:
            if self.mode == "gun_game" and self.gun_game:
                self._give_single_weapon(player, self.gun_game.weapon); return
            if self.mode == "rocket_tag":
                player.health = 125; player.armor = 25; self._give_single_weapon(player, 5); return
            if self.mode == "accuracy_trial":
                player.health = 125; player.armor = 25; self._give_single_weapon(player, 6); return
            if self.mode == "movement_hunter":
                player.health = 125; player.armor = 25; player.weapons(reset=True, g=True, mg=True); player.ammo(reset=True, mg=120); player.weapon(2); return
            if self.mode == "random_loadout":
                self._roll_random_loadout(player); return
            if self.mode == "arena_run" and self.run:
                effects = upgrade_effects(self.run)
                player.health = self._arena_max_health()
                player.armor = max(0, int(50 + effects.get("max_armor", 0)))
                player.weapons(g=True, mg=True, sg=True, gl=True, rl=True, lg=True, rg=True, pg=True)
                player.ammo(mg=200, sg=60, gl=60, rl=60, lg=200, rg=50, pg=200)
                trial = {"rocket": 5, "lg": 6, "rail": 7}.get((self.current_plan or {}).get("theme"))
                if trial: self._give_single_weapon(player, trial)
                else: player.weapon(5)
                if effects.get("haste"): player.powerups(haste=3600)
                return
            if self.mode == "predator":
                player.health = 75; player.armor = 0
            else:
                player.health = 150; player.armor = 50
            player.weapons(g=True, mg=True, sg=True, gl=True, rl=True, lg=True, rg=True, pg=True)
            player.ammo(mg=200, sg=50, gl=50, rl=50, lg=150, rg=30, pg=150)
            if self.mode == "gauntlet_run":
                trial = {"rail": 7, "rocket": 5, "lg": 6, "plasma": 8}.get(self.gauntlet_kind)
                if trial: self._give_single_weapon(player, trial); return
            player.weapon(5)
        except Exception as exc:
            self._log(f"human loadout failed: {exc}")

    def _apply_bot_loadout(self, player):
        plan = self.current_plan or {}
        try:
            if self.mode in ("arena_run", "boss_rush", "gauntlet_run") and plan:
                player.health = int(plan.get("health", 100))
                player.armor = int(plan.get("armor", 0))
                trial = {"rocket": 5, "lg": 6, "rail": 7, "plasma": 8}.get(plan.get("theme"))
                if trial: self._give_single_weapon(player, trial)
                elif plan.get("boss"): self._give_single_weapon(player, 5)
        except Exception as exc:
            self._log(f"bot loadout failed: {exc}")

    def _give_single_weapon(self, player, weapon):
        keys = {1: "g", 2: "mg", 3: "sg", 4: "gl", 5: "rl", 6: "lg", 7: "rg", 8: "pg"}
        key = keys.get(int(weapon))
        kwargs = {"g": True}
        if key: kwargs[key] = True
        player.weapons(reset=True, **kwargs)
        if key and key != "g": player.ammo(reset=True, **{key: 200})
        else: player.ammo(reset=True)
        player.weapon(int(weapon))

    def _roll_random_loadout(self, player):
        rng = random.Random(self.seed + self.random_round * 101 + self.player_deaths * 17)
        pool = [[5, 6], [5, 7], [6, 7], [3, 5], [7, 8], [5, 6, 7], [2, 3, 8]]
        weapons = rng.choice(pool)
        keys = {1: "g", 2: "mg", 3: "sg", 4: "gl", 5: "rl", 6: "lg", 7: "rg", 8: "pg"}
        weapon_kwargs = {"g": True}; ammo_kwargs = {}
        for weapon in weapons:
            weapon_kwargs[keys[weapon]] = True
            ammo_kwargs[keys[weapon]] = 200
        player.health = 125; player.armor = 25
        player.weapons(reset=True, **weapon_kwargs); player.ammo(reset=True, **ammo_kwargs); player.weapon(weapons[0])
        player.tell("^6LOADOUT:^7 " + " + ".join(WEAPON_NAMES[w] for w in weapons))

    # ---------- damage/upgrades ----------
    def handle_damage(self, target, attacker, damage, dflags, means_of_death):
        if self.mode != "arena_run" or not self.run:
            return
        if not is_player_object(target) or not is_player_object(attacker):
            return
        if attacker.id == target.id:
            return
        if is_bot(attacker) and not is_bot(target):
            self.last_hurt_time[target.id] = time.time()
            multiplier = max(0.0, float((self.current_plan or {}).get("damage_mult", 1.0)) - 1.0)
            bonus = max(0, int(round(damage * multiplier)))
            if bonus and getattr(target, "is_alive", False):
                try: target.health = max(1, int(target.health) - bonus)
                except Exception: pass
            return
        if is_bot(attacker) or is_bot(target) is False:
            return
        effects = upgrade_effects(self.run)
        multiplier = float(effects.get("damage_mult", 0))
        try: mod = int(means_of_death)
        except Exception: return
        rocket_mods = {getattr(minqlx, "MOD_ROCKET", -100), getattr(minqlx, "MOD_ROCKET_SPLASH", -101)}
        lightning_mods = {getattr(minqlx, "MOD_LIGHTNING", -102), getattr(minqlx, "MOD_LIGHTNING_DISCHARGE", -103)}
        rail_mods = {getattr(minqlx, "MOD_RAILGUN", -104), getattr(minqlx, "MOD_RAILGUN_HEADSHOT", -105)}
        plasma_mods = {getattr(minqlx, "MOD_PLASMA", -106), getattr(minqlx, "MOD_PLASMA_SPLASH", -107)}
        if mod in rocket_mods: multiplier += effects.get("rocket_mult", 0)
        if mod in lightning_mods:
            multiplier += effects.get("lg_mult", 0)
            now = time.time(); last = self.last_damage_time.get(attacker.id, 0)
            self.lg_streak[attacker.id] = self.lg_streak.get(attacker.id, 0) + 1 if now - last < 0.25 else 1
            self.last_damage_time[attacker.id] = now
            if effects.get("lg_overcharge"): multiplier += min(0.40, self.lg_streak[attacker.id] * 0.015)
            vamp = effects.get("lg_vampire", 0)
            if vamp:
                try: attacker.health = min(self._arena_max_health(), int(attacker.health) + max(1, int(damage * vamp)))
                except Exception: pass
        if mod in rail_mods:
            multiplier += effects.get("rail_mult", 0)
            self.rail_hits[attacker.id] = self.rail_hits.get(attacker.id, 0) + 1
            if effects.get("rail_combo") and self.rail_hits[attacker.id] % 3 == 0:
                multiplier += 0.80 if effects.get("rail_combo_bonus") else 0.50
                attacker.tell("^5PERFECT SHOT!")
        if mod in plasma_mods: multiplier += effects.get("plasma_mult", 0)
        vamp = effects.get("vampire", 0)
        if vamp:
            try: attacker.health = min(self._arena_max_health(), int(attacker.health) + max(1, int(damage * vamp)))
            except Exception: pass
        bonus = max(0, int(round(damage * multiplier)))
        if bonus and getattr(target, "is_alive", False):
            try: target.health = max(1, int(target.health) - bonus)
            except Exception: pass

    # ---------- movement ----------
    def _movement_effects(self):
        return upgrade_effects(self.run) if self.mode == "arena_run" and self.run else {}

    def _dash_charge_limit(self):
        return self.base_dash_charges + int(self._movement_effects().get("dash_charge", 0))

    def _dash_power(self):
        return self.dash_strength * (1.0 + float(self._movement_effects().get("dash_power", 0)))

    def cmd_dash(self, player, msg, channel):
        if not self.side_thrusters:
            player.tell("^7Side thrusters are disabled."); return
        if len(msg) < 2 or str(msg[1]).lower() not in ("left", "right"):
            player.tell("^7Use ^3!dash left ^7or ^3!dash right^7."); return
        self.request_side_dash(player, str(msg[1]).lower())

    def handle_client_command(self, player, command):
        try:
            parts = str(command).strip().split()
            if not parts or parts[0].lower() != "qldash":
                return
            if len(parts) >= 2 and parts[1].lower() in ("left", "right"):
                self.request_side_dash(player, parts[1].lower())
            return minqlx.RET_STOP_ALL
        except Exception:
            return minqlx.RET_STOP_ALL

    def request_side_dash(self, player, direction):
        if not self.side_thrusters or not is_player_object(player) or is_bot(player):
            return
        was_airborne = player.id in self.airborne
        if not was_airborne: self.dash_used[player.id] = 0
        if int(self.dash_used.get(player.id, 0)) >= self._dash_charge_limit():
            return
        now = time.time()
        if now < self.dash_ready.get(player.id, 0):
            return
        try:
            before = player.velocity(); bx, by = float(before.x), float(before.y)
        except Exception:
            return
        self.dash_ready[player.id] = now + 0.18
        self._apply_side_dash_delayed(player.id, direction, bx, by, was_airborne)

    @minqlx.delay(0.035)
    def _apply_side_dash_delayed(self, player_id, direction, bx, by, was_airborne):
        try:
            player = next((p for p in self.human_players() if p.id == player_id), None)
            if not player or not player.is_alive: return
            if was_airborne and player.id not in self.airborne: return
            if int(self.dash_used.get(player.id, 0)) >= self._dash_charge_limit(): return
            v = player.velocity(); vx, vy = float(v.x), float(v.y)
            dx, dy = vx - bx, vy - by; magnitude = math.hypot(dx, dy)
            if magnitude >= 2.0:
                nx, ny = dx / magnitude, dy / magnitude
            else:
                speed = math.hypot(vx, vy)
                if speed < 20: return
                if direction == "left": nx, ny = -vy / speed, vx / speed
                else: nx, ny = vy / speed, -vx / speed
            impulse = self._dash_power(); nvx, nvy = vx + nx * impulse, vy + ny * impulse
            speed = math.hypot(nvx, nvy)
            if speed > 950:
                scale = 950 / speed; nvx *= scale; nvy *= scale
            nz = float(v.z) if was_airborne else max(float(v.z), self.ground_dash_hop)
            player.velocity(x=nvx, y=nvy, z=nz)
            if not was_airborne:
                self.airborne.add(player.id); self.ground_ticks[player.id] = 0
            self.dash_used[player.id] = int(self.dash_used.get(player.id, 0)) + 1
            player.center_print(f"^6{'THRUST' if was_airborne else 'DODGE'} ^7{self.dash_used[player.id]}/{self._dash_charge_limit()}")
        except Exception as exc:
            self._log(f"side dash failed: {exc}")

    def handle_frame(self):
        effects = self._movement_effects(); jump = float(effects.get("jump_boost", 0)); regen = float(effects.get("regen_per_sec", 0))
        now = time.time()
        for player in self.human_players():
            try:
                if not player.is_alive:
                    self.airborne.discard(player.id); self.dash_used[player.id] = 0; continue
                v = player.velocity(); vz = float(v.z); prev = self.prev_vz.get(player.id, vz)
                if abs(vz) > 40:
                    self.airborne.add(player.id); self.ground_ticks[player.id] = 0
                elif player.id in self.airborne and abs(vz) < 6:
                    ticks = self.ground_ticks.get(player.id, 0) + 1; self.ground_ticks[player.id] = ticks
                    if ticks >= 3:
                        self.airborne.discard(player.id); self.dash_used[player.id] = 0; self.ground_ticks[player.id] = 0
                elif player.id not in self.airborne:
                    self.ground_ticks[player.id] = 0
                if jump and vz > 120 and prev <= 80:
                    vz *= 1.0 + jump; player.velocity(z=vz); self.airborne.add(player.id)
                self.prev_vz[player.id] = vz
                if regen and self.mode == "arena_run" and self.run:
                    if now - self.last_hurt_time.get(player.id, 0) >= 4.0 and now - self.last_regen_tick.get(player.id, 0) >= 1.0:
                        if int(player.health) < self._arena_max_health():
                            player.health = min(self._arena_max_health(), int(player.health) + int(regen))
                        self.last_regen_tick[player.id] = now
            except Exception:
                continue

    # ---------- commands ----------
    def cmd_run(self, player, msg, channel):
        extra = ""
        if self.mode == "horde" and self.horde: extra = f" wave={self.horde.wave}"
        if self.mode == "arena_run" and self.run: extra = f" round={self.run.round} lives={self.run.lives}"
        if self.mode == "gun_game" and self.gun_game: extra = f" weapon={self.gun_game.weapon_name}"
        player.tell(
            f"^6Solo v5:^7 mode={self.mode} phase={self.controller.phase.value}{extra} "
            f"alive={len(self.controller.enemy_ids)} spawns={self.controller.fulfilled_spawns}/{self.controller.expected_spawns}"
        )

    def cmd_help(self, player, msg, channel):
        player.tell("^6Solo Engine v5:^7 !run shows lifecycle state; !dash left/right is a movement fallback.")
        if self.mode == "arena_run":
            player.tell("^7Arena Run: use ^3!pick 1/2/3 ^7and ^3!upgrades^7 between rounds.")
