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
    from .director_runtime import DirectorRuntime
    from .spawn_director import PlacementRequest, SpawnPointBook, as_vec, choose_spawn
    from .solo_core import (
        BOT_ROSTER, RARITY_COLOR, UPGRADE_BY_ID, advance_round, load_state,
        new_state, pick_upgrade, roll_upgrade_choices, round_plan, save_state,
        upgrade_effects,
    )
except ImportError:  # Direct local/unit-test import fallback.
    from modes.gun_game import GunGameState
    from modes.horde import HordeState
    from solo_controller import Phase, SoloController
    from director_runtime import DirectorRuntime
    from spawn_director import PlacementRequest, SpawnPointBook, as_vec, choose_spawn
    from solo_core import (
        BOT_ROSTER, RARITY_COLOR, UPGRADE_BY_ID, advance_round, load_state,
        new_state, pick_upgrade, roll_upgrade_choices, round_plan, save_state,
        upgrade_effects,
    )

SESSION_FILE = Path.home() / ".config/quake-live-launcher/solo_session.json"
STATE_FILE = Path.home() / ".config/quake-live-launcher/arena_run_state_v5.json"
RUNTIME_DIR = Path.home() / ".local/share/quake-live-launcher/solo_runtime"
PLUGIN_READY_FILE = RUNTIME_DIR / "plugin_ready.json"
CONTROLS_FILE = RUNTIME_DIR / "controls.json"  # written by the launcher: {"dash_key": ...}
PLUGIN_VERSION = "5.0-alpha-ports1"

# Forfeit root cause (v5.0-alpha-warmup1):
#
# Quake Live only runs its multiplayer forfeit rules once a match has left
# warmup.  The old contract forced the match straight to IN_PROGRESS
# (g_doWarmup 0 + sv_warmupReadyPercentage 0) and then every mode starts by
# clearing BLUE and adding bots on later frames.  A live TDM match with an
# empty team is a forfeit, and allow_single_player()/mapIsTrainingMap only
# relaxes the "fewer than two players" rule used by race - it does not stop
# the empty-team rule.  Enemies are also kicked on death, so BLUE empties again
# at every wave clear.
#
# The scripted modes never need Quake Live's match layer: the plugin owns
# objectives, lives, scoring and completion.  So the combat sandbox now stays
# in warmup permanently, where combat, bots, spawns and minqlx death/damage
# events all run normally but no match can be forfeited.
WARMUP_SANDBOX_CVARS = {
    "g_doWarmup": "1",
    "sv_warmupReadyPercentage": "1",
    "g_warmupReadyDelay": "0",
    "g_warmupDelay": "0",
}
READY_COMMANDS = {"readyup", "ready", "notready"}

RECORDS_FILE = Path.home() / ".config/quake-live-launcher/solo_records.json"

# Every bot in play must have a unique name: shinqlx/minqlx resolve bot deaths
# by NAME (bots have no Steam ID in the stats stream), so two "Keel"s make a
# death resolve to whichever Keel is listed first.  Nine names covers the
# largest squad (Horde caps at nine).
BOT_NAME_POOL = tuple(BOT_ROSTER)

# Modes where a successful finish records a completion time (lower is better).
TIMED_GOAL_MODES = {
    "arena_run", "gun_game", "boss_rush", "wipeout_solo", "gauntlet_run",
    "one_life", "bounty_hunt", "rocket_tag", "predator", "accuracy_trial",
    "speedrun_combat", "random_loadout",
}
# Modes where a human death ends the run.
FATAL_DEATH_MODES = {
    "horde", "boss_rush", "gauntlet_run", "last_stand", "one_life",
    "movement_hunter", "predator", "duel_2v1",
}

WIPEOUT_LIVES = 3
GUN_GAME_KILLS_PER_TIER = 2
MOVEMENT_HUNTER_SECONDS = 90
MOVEMENT_HUNTER_CALLOUTS = (60, 30, 10, 5, 4, 3, 2, 1)
LAST_STAND_BASE_BOTS = 5
LAST_STAND_MAX_BOTS = 8
LAST_STAND_MAX_THREAT = 8
PREDATOR_HUNGER_DELAY = 8.0     # seconds without a kill before hunger starts
PREDATOR_HUNGER_DRAIN = 4       # health lost per second while hungry
PREDATOR_HUNGER_FLOOR = 10      # hunger never kills on its own
SPEEDRUN_SPLITS = (5, 10)
TARGET_HASTE_SECONDS = 600      # Haste smoke trail marks bounty targets
BOT_NAME_RESERVATION_SECONDS = 30.0

SPAWN_POINTS_FILE = RUNTIME_DIR / "spawn_points.json"
# Squads of 4+ in these modes send a third of the squad as flankers that
# arrive a few seconds after the front line (learned spawn points let the
# Director bring them in from the player's sides/rear when moving).
FLANK_MODES = {"horde", "arena_run", "gauntlet_run", "wipeout_solo"}
FLANK_MIN_SQUAD = 4
FLANK_DELAY = {"easy": 6.0, "normal": 4.5, "hard": 3.5, "nightmare": 3.0}
FLANK_ARRIVAL_TIMEOUT = 8.0     # an addbot that never spawns stops blocking clears


def format_duration(seconds) -> str:
    seconds = max(0.0, float(seconds))
    minutes, rest = divmod(seconds, 60.0)
    if minutes >= 1:
        return f"{int(minutes)}:{rest:05.2f}"
    return f"{rest:.2f}s"

SUPPORTED_MODES = {
    "arena_run", "horde", "gun_game", "boss_rush", "wipeout_solo",
    "gauntlet_run", "last_stand", "one_life", "bounty_hunt", "rocket_tag",
    "movement_hunter", "predator", "accuracy_trial", "speedrun_combat",
    "random_loadout", "duel_2v1",
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
        self.player_id = None
        self.spawn_book = SpawnPointBook(SPAWN_POINTS_FILE)
        self.placement_rng = random.Random(self.seed ^ 0x5A17)
        self._read_director_settings()
        self._reset_mode_state()

        self.director_runtime = DirectorRuntime(self, self.mode, self.difficulty, self.seed, RUNTIME_DIR)

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
        self.last_warmup_hold = 0.0
        for event, handler in (
            ("game_countdown", self.handle_game_countdown),
            ("game_start", self.handle_game_start),
            ("game_end", self.handle_game_end),
        ):
            try:
                self.add_hook(event, handler)
            except Exception as exc:
                self._log(f"warmup guard hook {event} unavailable: {exc}")

        self.add_command(("run", "solo"), self.cmd_run)
        self.add_command("solohelp", self.cmd_help)
        self.add_command(("pick", "choose"), self.cmd_pick)
        self.add_command(("upgrades", "build"), self.cmd_upgrades)
        self.add_command("dash", self.cmd_dash)
        self.add_command(("best", "records"), self.cmd_best)

        self.controller.wait_for_player()
        self._write_ready(True)
        self._log(f"plugin ready mode={self.mode} seed={self.seed} skill={self.skill}")

    # ---------- runtime/bootstrap ----------
    def _reset_mode_state(self):
        """Reset every per-run field. Shared by construction and hot-load."""
        self.horde = HordeState(self.seed) if self.mode == "horde" else None
        self.gun_game = GunGameState(kills_per_tier=GUN_GAME_KILLS_PER_TIER) if self.mode == "gun_game" else None
        self.run = None
        self.current_plan = None
        self.mode_started = False
        self.pending_resume_payload = None
        self.preactive_dead_ids = set()
        self.pending_replacements = 0
        self.pending_bot_names = []  # [(name, reserved_at)]
        self.start_time = time.time()
        self.objective_live_at = None
        self.kills = 0
        self.player_deaths = 0
        self.result_recorded = False
        self.anomalous_bot_kills = 0

        self.boss_round = 1
        self.gauntlet_stage = 1
        self.gauntlet_kind = "survival"
        self.wipeout_round = 1
        self.wipeout_respawn_level = 0
        self.wipeout_generation = 0
        self.wipeout_lives = WIPEOUT_LIVES
        self.target_bot_id = None
        self.target_name = None
        self.target_score = 0
        self.challenge_goal = 0
        self.random_round = 1

        self.threat_level = 1
        self.next_threat_check = 0.0
        self.last_kill_time = None
        self.next_hunger_tick = 0.0
        self.hunger_announced = False
        self.acc_hits = 0
        self.acc_damage = 0
        self.acc_first_hit = {}
        self.acc_ttk = []
        self.pending_splits = {}
        self.flank_names = {}
        self.flank_announced = False
        self.placement_stats = {"moved": 0, "kept": 0, "no_fair_point": 0, "too_few_points": 0, "learned": 0}

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

    def _read_director_settings(self):
        settings = self.session.get("director") if isinstance(self.session.get("director"), dict) else {}
        self.spawn_placement_enabled = bool(settings.get("spawn_placement", True))
        self.flankers_enabled = bool(settings.get("flankers", True))

    def _console_kick(self, client_id):
        minqlx.console_command(f"kick {int(client_id)}")

    def _load_session(self):
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _require_runtime_contract(self):
        if self.mode not in SUPPORTED_MODES:
            raise RuntimeError(f"unsupported scripted Solo mode: {self.mode}")
        try:
            zmq_enabled = int(self.get_cvar("zmq_stats_enable") or 0)
        except Exception:
            zmq_enabled = 0
        if zmq_enabled != 1:
            raise RuntimeError("zmq_stats_enable must be 1 before solo_arcade loads")
        # Deaths/kills arrive only through shinqlx's ZMQ stats listener, which
        # uses PLAIN auth; libzmq rejects an empty password, so the listener
        # (and every death event) would silently never exist.
        if not str(self.get_cvar("zmq_stats_password") or "").strip():
            raise RuntimeError("zmq_stats_password must be non-empty: shinqlx's stats listener cannot connect without it")

    def _configure_engine(self):
        # Clear Quake Live's training-match state; earlier builds turned it on
        # as an anti-forfeit attempt and it drives the training HUD message.
        try:
            minqlx.allow_single_player(False)
        except Exception:
            pass
        cvars = {
            "g_training": "0",
            # Local-only server: never drop/kick the player for sending
            # client commands (dash, upgrade picks) faster than 1/second.
            "sv_floodProtect": "0",
            "sv_hostname": "Quake Live // Solo Engine v5",
            "bot_enable": "1", "bot_thinktime": "0", "bot_challenge": "1",
            "bot_aasoptimize": "1", "bot_rocketjump": "1", "bot_nochat": "1",
            "bot_dynamicskill": "0", "bot_minplayers": "0",
            "fraglimit": "0", "timelimit": "0",
            "capturelimit": "0", "roundlimit": "0", "scorelimit": "0",
            "g_friendlyFire": "0", "g_teamForceBalance": "0",
            "g_teamSizeMin": "0", "g_teamSizeMax": "0",
        }
        cvars.update(WARMUP_SANDBOX_CVARS)
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
            self.spawn_book.save(force=True)
        except Exception:
            pass
        try:
            PLUGIN_READY_FILE.unlink(missing_ok=True)
        except Exception:
            pass

    def handle_new_game(self):
        self._configure_engine()

    def handle_map(self, map_name, factory):
        self._configure_engine()
        self.airborne.clear(); self.dash_used.clear(); self.ground_ticks.clear()
        self.spawn_book.save(force=True)
        self._log(f"map={map_name} factory={factory} phase={self.controller.phase.value} game_state={self.game_state()}")

    # ---------- permanent-warmup sandbox (anti-forfeit) ----------
    def game_state(self):
        try:
            return str(self.game.state)
        except Exception:
            return "unknown"

    def hold_warmup(self, reason):
        """Return the server to warmup if Quake Live ever tries to start a match."""
        now = time.time()
        if now - self.last_warmup_hold < 1.0:
            return False
        self.last_warmup_hold = now
        self._log(f"warmup guard: {reason}; game_state={self.game_state()}; aborting back to warmup")
        for name, value in WARMUP_SANDBOX_CVARS.items():
            try:
                self.set_cvar(name, value)
            except Exception:
                pass
        try:
            minqlx.console_command("abort")
        except Exception as exc:
            self._log(f"warmup guard abort failed: {exc}")
        return True

    def handle_game_countdown(self, *args):
        self.hold_warmup("match countdown started")

    def handle_game_start(self, *args):
        self.hold_warmup("match started")

    def handle_game_end(self, data=None, *args):
        summary = {}
        if isinstance(data, dict):
            for key in ("ABORTED", "EXIT_MSG", "GAME_TYPE", "MAP", "TSCORE0", "TSCORE1"):
                if key in data:
                    summary[key] = data[key]
        self._log(f"game_end observed phase={self.controller.phase.value} data={summary}")

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
        self.pending_bot_names = []
        self.flank_names = {}
        if hasattr(self, "director_runtime"):
            self.director_runtime.reset()

    # ---------- unique bot names ----------
    def _bot_names_in_use(self):
        # A reservation whose addbot never produced a spawn (rejected bot,
        # map change) expires instead of shrinking the pool forever.
        now = time.time()
        self.pending_bot_names = [
            (name, at) for name, at in self.pending_bot_names if now - at < BOT_NAME_RESERVATION_SECONDS
        ]
        names = {clean_name(bot).lower() for bot in self.bot_players()}
        names.update(name for name, _at in self.pending_bot_names)
        return names

    def _pending_bot_count(self):
        return len(self.pending_bot_names)

    def _reserve_bot_name(self, preferred=None):
        """Reserve a bot name no living or pending bot uses.

        Deaths are resolved by name, so a duplicate would let a kill land on
        the wrong bot (a living namesake gets kicked while the real victim
        stays counted, stalling wave clears and mis-crediting bounty targets).
        """
        used = self._bot_names_in_use()
        wanted = str(preferred).lower() if preferred else None
        if wanted and wanted not in used:
            name = wanted
        else:
            free = [candidate for candidate in BOT_NAME_POOL if candidate not in used]
            if free:
                name = free[0] if wanted else random.choice(free)
            else:
                name = wanted or random.choice(BOT_NAME_POOL)
        self.pending_bot_names.append((name, time.time()))
        return name

    def _release_bot_name(self, name):
        wanted = str(name).lower()
        for index, (pending, _at) in enumerate(self.pending_bot_names):
            if pending == wanted:
                del self.pending_bot_names[index]
                return

    def _spawn_objective_bots(self, names, skill, *, auto_clear=True):
        names = list(names)
        self.clear_all_bots()
        self.director_runtime.begin_objective()
        self.pending_replacements = 0
        names = [self._reserve_bot_name(name) for name in names]
        front, flank = self._split_squad(names)
        token = self.controller.begin_objective(len(front), auto_clear=auto_clear)
        self.flank_names = {}
        self.flank_announced = False
        if flank:
            self.controller.expect_reinforcements(len(flank))
        for index, name in enumerate(front):
            self._add_bot_later(name, skill, index * 0.15, token)
        delay = FLANK_DELAY.get(self.difficulty, FLANK_DELAY["normal"])
        for index, name in enumerate(flank):
            self.flank_names[name] = token
            self._add_flanker_later(name, skill, delay + index * 0.4, token)
        return token

    def _split_squad(self, names):
        """Front line activates the objective; the rest arrive later as flankers."""
        if (
            not self.flankers_enabled
            or self.mode not in FLANK_MODES
            or len(names) < FLANK_MIN_SQUAD
            or (self.current_plan or {}).get("boss")
        ):
            return list(names), []
        count = max(1, len(names) // 3)
        return list(names[:-count]), list(names[-count:])

    def _add_flanker_later(self, name, skill, delay, token):
        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.PREPARING, Phase.ACTIVE) and name in self.flank_names:
                minqlx.console_command(f"addbot {name} {clamp(int(skill), 1, 5)} blue")
                self._expire_flanker_later(name, token)
            else:
                self._drop_flanker(name, token)
        _add()

    def _expire_flanker_later(self, name, token):
        @minqlx.delay(FLANK_ARRIVAL_TIMEOUT)
        def _expire():
            if name in self.flank_names and self.flank_names.get(name) == token:
                self._log(f"flanker {name} never spawned; releasing its slot")
                self._drop_flanker(name, token)
        _expire()

    def _drop_flanker(self, name, token):
        """Forget a flanker that will never arrive, clearing the objective if it was the last."""
        if self.flank_names.get(name) != token:
            self._release_bot_name(name)
            return
        self.flank_names.pop(name, None)
        self._release_bot_name(name)
        if self.controller.token() == token and self.controller.reinforcement_cancelled():
            self._objective_cleared()

    def _add_bot_later(self, name, skill, delay, token):
        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.PREPARING, Phase.ACTIVE):
                minqlx.console_command(f"addbot {name} {clamp(int(skill), 1, 5)} blue")
            else:
                self._release_bot_name(name)
        _add()

    def _add_replacement_bot(self, name=None, skill=None, delay=0.35):
        token = self.controller.token()
        name = self._reserve_bot_name(name)
        skill = self._reinforcement_skill() if skill is None else skill
        delay = self.director_runtime.reinforcement_delay(delay)
        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.ACTIVE):
                minqlx.console_command(f"addbot {name} {clamp(int(skill), 1, 5)} blue")
            else:
                self._release_bot_name(name)
        _add()

    def _reinforcement_skill(self):
        if self.mode == "last_stand":
            return min(5, self.skill + (self.threat_level - 1) // 3)
        return self.skill

    def _kick_bot_id(self, client_id):
        # Resolve the client directly instead of filtering bot_players()'s
        # teams()-derived roster: a bot mid-death-transition can be briefly
        # absent from that snapshot, which used to make this whole method
        # silently no-op (no match -> loop ends -> nothing kicked, no
        # exception, no log). The defeated "enemy" then just respawned via
        # ordinary bot behavior and the round's all-dead clear condition
        # became unreachable. Confirmed live: two enemies died and respawned
        # repeatedly with zero kick/disconnect events in the log.
        try:
            bot = minqlx.Player(client_id)
        except Exception as exc:
            self._log(f"_kick_bot_id: could not resolve client {client_id}: {exc}")
            bot = None
        if bot is not None:
            try:
                bot.kick("solo enemy defeated")
                return
            except Exception as exc:
                self._log(f"_kick_bot_id: bot.kick failed for {client_id}: {exc}")
        try:
            self._console_kick(client_id)
        except Exception as exc:
            self._log(f"_kick_bot_id: console kick failed for {client_id}: {exc}")

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
        self._put_team(player, "red")
        if self.controller.pending_map:
            payload = self.controller.resume_map_if_ready(self.current_map_name(), player.id)
            if payload is not None:
                self.pending_resume_payload = payload
                return
        entered_preparing = self.controller.player_loaded(player.id)
        # The client can fire player_spawn (which is what normally starts the
        # mode, below) before player_loaded fires — observed live: two
        # spawns, then loaded, same second. player_loaded is what flips the
        # phase to PREPARING, so if the spawns already ran and failed that
        # check, nothing was left to catch it: the player just sits alive in
        # an unstarted round forever. Start it here too the moment we enter
        # PREPARING, not only from a future spawn event.
        if entered_preparing and not self.mode_started:
            self.mode_started = True
            self._start_selected_mode()

    def handle_player_spawn(self, player):
        if not is_player_object(player):
            return
        # Learn from the engine's own choice before anything relocates it.
        self._learn_spawn(player)
        if is_bot(player):
            self._put_team(player, "blue")
            name = clean_name(player).lower()
            self._release_bot_name(name)
            if player.id in self.preactive_dead_ids:
                self._kick_bot_id(player.id)
                return
            flanker = name in self.flank_names
            if flanker:
                self.flank_names.pop(name, None)
                activated = False
                if not self.controller.reinforcement_arrived(player.id):
                    self._log(f"late flanker {name} id={player.id} arrived after its objective; removing")
                    self._kick_bot_id(player.id)
                    return
            else:
                activated = self.controller.enemy_spawned(player.id)
            role = self.director_runtime.bot_spawned(player)
            self._apply_bot_loadout(player)
            self._place_bot(player, flank=flanker)
            if flanker and not self.flank_announced:
                self.flank_announced = True
                self.msg("^3FLANKERS INBOUND")
            self._log(
                f"enemy spawn id={player.id} fulfilled={self.controller.fulfilled_spawns}/"
                f"{self.controller.expected_spawns} alive={len(self.controller.enemy_ids)} "
                f"phase={self.controller.phase.value} role={role}"
            )
            if activated:
                self._retire_preactive_dead()
                self.msg("^2OBJECTIVE LIVE")
                self._on_objective_activated()
            return

        self.player_id = player.id
        self._put_team(player, "red")
        self._apply_human_loadout(player)
        self._show_controls_hint(player)
        if self.pending_resume_payload is not None:
            payload = self.pending_resume_payload
            self.pending_resume_payload = None
            self.mode_started = True
            self._resume_payload(payload)
            return
        if not self.mode_started and self.controller.phase == Phase.PREPARING:
            self.mode_started = True
            self._start_selected_mode()

    # ---------- controls hint ----------
    def _show_controls_hint(self, player):
        if getattr(self, "controls_hint_shown", False):
            return
        self.controls_hint_shown = True
        if not self.side_thrusters:
            return
        try:
            data = json.loads(CONTROLS_FILE.read_text(encoding="utf-8"))
            key = data.get("dash_key") if isinstance(data, dict) else None
        except Exception:
            key = None
        if key:
            player.tell(f"^6Side thrusters:^7 hold a strafe key and press ^3{key}^7 to dodge (ground) or dash (air).")
        else:
            player.tell("^6Side thrusters:^7 no free key was found for dash; use ^3!dash left^7 / ^3!dash right^7.")

    # ---------- spawn Director ----------
    def _learn_spawn(self, player):
        try:
            if self.spawn_book.learn(self.current_map_name(), player.position()):
                self.placement_stats["learned"] += 1
                self.spawn_book.save()
        except Exception as exc:
            self._log(f"spawn learning failed: {exc}")

    def _place_bot(self, bot, *, flank=False):
        """Move a fresh enemy spawn to a fair, useful learned spawn point.

        Keeps the engine's choice when it is already in the Director's distance
        band and clear of other players. Never within MIN_PLAYER_DISTANCE of the
        player, never overlapping anyone (the engine's KillBox does not re-run).
        """
        if not self.spawn_placement_enabled or self.controller.phase not in (Phase.PREPARING, Phase.ACTIVE):
            return None
        human = self.primary_player()
        if human is None or not getattr(human, "is_alive", False):
            return None
        try:
            human_pos = as_vec(human.position())
            human_vel = as_vec(human.velocity())
            engine_pos = as_vec(bot.position())
            occupants = []
            for other in self.human_players() + self.bot_players():
                if other.id == bot.id:
                    continue
                pos = as_vec(other.position())
                if pos is not None:
                    occupants.append(pos)
        except Exception as exc:
            self._log(f"spawn placement skipped: {exc}")
            return None
        if human_pos is None:
            return None
        profile = self.director_runtime.director.profile
        max_distance = max(float(profile.far_distance), float(profile.engage_distance) * 1.5)
        preferred = float(profile.engage_distance) * 1.05
        holding = False
        try:
            holding = bool(self.director_runtime.director.should_hold_reinforcements(time.time()))
        except Exception:
            pass
        if holding:
            # Recovery window: new enemies enter at the far edge of the band.
            preferred = max_distance * 0.9
        request = PlacementRequest(
            human_pos=human_pos, human_vel=human_vel, occupants=occupants,
            preferred=preferred, max_distance=max_distance, flank=bool(flank),
        )
        decision = choose_spawn(self.spawn_book.points(self.current_map_name()), engine_pos, request, rng=self.placement_rng)
        if decision.moved and decision.point is not None:
            try:
                bot.position(x=decision.point.x, y=decision.point.y, z=decision.point.z)
                bot.velocity(reset=True)
            except Exception as exc:
                self._log(f"spawn placement failed for id={bot.id}: {exc}")
                return None
            self.placement_stats["moved"] += 1
        elif decision.reason == "engine_choice_ok":
            self.placement_stats["kept"] += 1
        else:
            self.placement_stats[decision.reason] = self.placement_stats.get(decision.reason, 0) + 1
        try:
            self.director_runtime.record_spawn_placement(bot, decision, flank=bool(flank), holding=holding)
        except Exception:
            pass
        return decision

    def spawn_summary(self):
        points = len(self.spawn_book.points(self.current_map_name()))
        stats = self.placement_stats
        return (
            f"spawn points on this map {points}; placed {stats['moved']}, kept engine choice {stats['kept']}, "
            f"no fair point {stats.get('no_fair_point', 0)}, still learning {stats.get('too_few_points', 0)}"
        )

    def handle_player_disconnect(self, player, reason):
        if is_player_object(player) and not is_bot(player) and player.id == self.player_id:
            self.controller.finish()
            self.clear_all_bots()

    def _on_objective_activated(self):
        now = time.time()
        first_activation = self.objective_live_at is None
        self.objective_live_at = now
        if self.last_kill_time is None:
            self.last_kill_time = now
        if first_activation and self.mode == "speedrun_combat":
            # The clock starts when the enemies are actually in play, not when
            # the first of the staggered bot spawns was requested.
            self.start_time = now
            self.msg("^2GO!^7 Clock is running.")
        if first_activation and self.mode == "movement_hunter":
            self._arm_movement_hunter_timer()
        if self.mode in ("bounty_hunt", "rocket_tag") and self.target_bot_id is None:
            self._choose_target()

    # ---------- between-round resupply ----------
    def _refresh_human_for_round(self):
        """Re-arm the player for a new round/wave/stage.

        Map weapon/ammo pickups are disabled in the scripted sandbox, so without
        this a multi-round run slowly starves: Horde/Boss Rush/Gauntlet/Wipeout
        used to hand out ammo only on spawn. It also applies the correct trial
        weapon for the round that is about to start. Health and armor are never
        lowered (except Arena Run, whose upgrade caps define max health).
        """
        player = self.primary_player()
        if player is None or not getattr(player, "is_alive", True):
            return
        try:
            prev_health, prev_armor = int(player.health), int(player.armor)
        except Exception:
            prev_health, prev_armor = 0, 0
        self._apply_human_loadout(player)
        if self.mode == "arena_run":
            return
        try:
            if int(player.health) < prev_health:
                player.health = prev_health
            if int(player.armor) < prev_armor:
                player.armor = prev_armor
        except Exception:
            pass

    # ---------- mode bootstrap ----------
    def _start_selected_mode(self):
        self.start_time = time.time()
        if self.mode == "arena_run": self._start_arena_run()
        elif self.mode == "horde": self._start_horde_wave()
        elif self.mode == "gun_game": self._start_gun_game()
        elif self.mode == "boss_rush": self._start_boss()
        elif self.mode == "wipeout_solo": self._start_wipeout()
        elif self.mode == "gauntlet_run": self._start_gauntlet_stage()
        elif self.mode == "last_stand": self._start_last_stand()
        elif self.mode == "one_life": self._start_continuous(5, "^6ONE LIFE^7 — reach 12 kills without dying.", goal=12)
        elif self.mode == "bounty_hunt": self._start_bounty_hunt()
        elif self.mode == "rocket_tag": self._start_rocket_tag()
        elif self.mode == "movement_hunter": self._start_movement_hunter()
        elif self.mode == "predator": self._start_continuous(5, f"^6PREDATOR^7 — reach 25 kills. Kills heal you; go {PREDATOR_HUNGER_DELAY:.0f}s without one and you starve.", goal=25)
        elif self.mode == "accuracy_trial": self._start_continuous(4, "^6ACCURACY TRIAL^7 — 20 Lightning Gun kills. Results: hits, damage and time-to-kill.", goal=20)
        elif self.mode == "speedrun_combat": self._start_continuous(5, "^6SPEEDRUN COMBAT^7 — clear 15 kills as fast as possible.", goal=15)
        elif self.mode == "random_loadout": self._start_continuous(5, "^6RANDOM LOADOUT^7 — reach 20 kills; loadout rerolls every 4 kills and death.", goal=20)
        elif self.mode == "duel_2v1": self._start_continuous(2, "^6DUEL 2v1^7 — two opponents, Director-tuned pressure. One life.", goal=0)

    # ---------- Horde ----------
    def _start_horde_wave(self):
        if not self.horde or self.horde.complete:
            return
        plan = self.horde.plan()
        elite = " ^1ELITE" if plan["elite"] else ""
        self.msg(f"^6HORDE WAVE {plan['wave']}^7 — {plan['count']} enemies{elite}")
        if plan["wave"] > 1:
            self._refresh_human_for_round()
            self.msg("^5RESUPPLIED.")
        self._spawn_objective_bots(plan["bots"], plan["skill"], auto_clear=True)

    def _horde_clear(self):
        if not self.horde or self.horde.complete:
            return
        self.horde.clear_wave()
        self._schedule(1.0, self._start_horde_wave, Phase.BETWEEN_ROUNDS)

    # ---------- Gun Game ----------
    def _start_gun_game(self):
        self.msg(
            f"^6GUN GAME^7 — {self.gun_game.kills_per_tier} kills per weapon, finish with a Gauntlet kill. "
            f"Start: {self.gun_game.weapon_name}. ^1Bot Gauntlet kills demote you."
        )
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)

    def _gun_game_kill(self, killer):
        if not self.gun_game or self.gun_game.complete:
            return
        result = self.gun_game.scored_kill()
        if result == "complete":
            self._finish_mode("^2GUN GAME COMPLETE!^7 Gauntlet kill finished the ladder.", success=True)
            return
        if result == "advance":
            self._give_single_weapon(killer, self.gun_game.weapon)
            suffix = " ^1— FINAL: Gauntlet kill wins" if self.gun_game.is_final_tier else ""
            killer.center_print(f"^2ADVANCE:^7 {self.gun_game.weapon_name}{suffix}")
            killer.tell(f"^2ADVANCE:^7 {self.gun_game.weapon_name} (tier {self.gun_game.index + 1}/8){suffix}")
        else:
            killer.center_print(f"^7{self.gun_game.weapon_name} {self.gun_game.tier_kills}/{self.gun_game.tier_goal}")

    def _gun_game_humiliated(self, victim):
        if not self.gun_game or self.gun_game.complete:
            return
        if self.gun_game.demote():
            self.msg(f"^1HUMILIATED!^7 Demoted to {self.gun_game.weapon_name}.")
        else:
            self.msg("^1HUMILIATED!^7 Tier progress lost.")

    # ---------- Boss Rush ----------
    def _start_boss(self):
        if self.boss_round > 10:
            self._finish_mode("^2BOSS RUSH COMPLETE!^7 Ten bosses defeated.", success=True)
            return
        bosses = ["keel", "slash", "doom", "xaero"]
        name = bosses[(self.boss_round - 1) % len(bosses)]
        self.current_plan = {
            "boss": True, "theme": "boss", "health": 500 + self.boss_round * 250,
            "armor": 150 + self.boss_round * 125, "damage_mult": 1.0 + self.boss_round * 0.08,
        }
        self.msg(f"^1BOSS {self.boss_round}/10:^7 {name.upper()} ^7(x{self.current_plan['damage_mult']:.2f} damage)")
        if self.boss_round > 1:
            self._refresh_human_for_round()
        self._spawn_objective_bots([name], min(5, 3 + self.boss_round // 2), auto_clear=True)

    # ---------- Wipeout ----------
    def _start_wipeout(self):
        if self.wipeout_round > 5:
            self._finish_mode("^2WIPEOUT SOLO COMPLETE!^7 Five squads wiped simultaneously.", success=True)
            return
        self.wipeout_generation += 1
        self.wipeout_respawn_level = 0
        self.msg(
            f"^6WIPEOUT ROUND {self.wipeout_round}/5:^7 eliminate the whole squad at once. "
            f"Lives: ^3{self.wipeout_lives}"
        )
        if self.wipeout_round > 1:
            self._refresh_human_for_round()
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki"], min(5, self.skill + (self.wipeout_round - 1) // 2), auto_clear=True)

    def _schedule_wipeout_respawn(self, name):
        self.wipeout_respawn_level += 1
        delay = min(25.0, 2.0 + self.wipeout_respawn_level * 2.0)
        generation = self.wipeout_generation
        token = self.controller.token()
        name = self._reserve_bot_name(name)
        @minqlx.delay(delay)
        def _respawn():
            if generation != self.wipeout_generation or not self.controller.token_valid(token, Phase.ACTIVE):
                self._release_bot_name(name)
                return
            minqlx.console_command(f"addbot {name} {min(5, self.skill + (self.wipeout_round - 1) // 2)} blue")
        _respawn()

    # ---------- Gauntlet ----------
    def _start_gauntlet_stage(self):
        if self.gauntlet_stage > 10:
            self._finish_mode("^2THE GAUNTLET COMPLETE!^7 Ten stages cleared.", success=True)
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
            # Loadout after the plan/kind is set, so the trial weapon matches
            # the stage even when no map change (and so no respawn) happens.
            self._refresh_human_for_round()
            self._spawn_objective_bots([name], min(5, self.skill + 1), auto_clear=True)
            return
        count = 3 if kind == "duel" else 6
        rng = random.Random(self.seed + self.gauntlet_stage * 53)
        names = rng.sample(list(BOT_ROSTER_RUNTIME), k=min(count, len(BOT_ROSTER_RUNTIME)))
        self.current_plan = {"theme": kind, "health": 120, "armor": 25, "damage_mult": 1.0}
        self._refresh_human_for_round()
        self._spawn_objective_bots(names, min(5, self.skill + self.gauntlet_stage // 4), auto_clear=True)

    # ---------- Continuous challenge modes ----------
    def _start_continuous(self, count, message, *, goal):
        self.challenge_goal = int(goal)
        self.msg(message)
        names = list(BOT_ROSTER_RUNTIME[:count])
        self._spawn_objective_bots(names, self.skill, auto_clear=False)

    # ---------- Last Stand escalation ----------
    def _start_last_stand(self):
        self.challenge_goal = 0
        self.threat_level = 1
        self.current_plan = self._last_stand_plan(1)
        self.msg("^6LAST STAND^7 — one life. The threat level rises every 5 kills and every minute.")
        names = list(BOT_ROSTER_RUNTIME[:LAST_STAND_BASE_BOTS])
        self._spawn_objective_bots(names, self.skill, auto_clear=False)

    @staticmethod
    def _last_stand_plan(level):
        level = max(1, int(level))
        return {
            "theme": "last_stand",
            "health": 100 + (level - 1) * 10,
            "armor": (level - 1) * 10,
            "damage_mult": 1.0 + (level - 1) * 0.05,
        }

    def _last_stand_target_level(self, now=None):
        now = time.time() if now is None else now
        live_at = self.objective_live_at or now
        minutes = int(max(0.0, now - live_at) // 60)
        return min(LAST_STAND_MAX_THREAT, 1 + self.kills // 5 + minutes)

    def _update_last_stand_threat(self, now=None):
        if self.mode != "last_stand" or self.controller.phase != Phase.ACTIVE:
            return
        level = self._last_stand_target_level(now)
        if level <= self.threat_level:
            return
        self.threat_level = level
        self.current_plan = self._last_stand_plan(level)
        desired = min(LAST_STAND_MAX_BOTS, LAST_STAND_BASE_BOTS + (level - 1) // 2)
        present = len(self.controller.enemy_ids) + self._pending_bot_count()
        extra = max(0, desired - present)
        self.msg(
            f"^1THREAT LEVEL {level}^7 — enemies {desired}, skill {self._reinforcement_skill()}, "
            f"+{(level - 1) * 10} hp"
        )
        for index in range(extra):
            self._add_replacement_bot(delay=0.3 + index * 0.2)

    # ---------- Predator hunger ----------
    def _tick_predator_hunger(self, now):
        if self.mode != "predator" or self.controller.phase != Phase.ACTIVE:
            return
        last = self.last_kill_time or self.objective_live_at
        if last is None or now - last < PREDATOR_HUNGER_DELAY or now < self.next_hunger_tick:
            return
        self.next_hunger_tick = now + 1.0
        player = self.primary_player()
        if player is None or not getattr(player, "is_alive", False):
            return
        try:
            health = int(player.health)
            if health > PREDATOR_HUNGER_FLOOR:
                player.health = max(PREDATOR_HUNGER_FLOOR, health - PREDATOR_HUNGER_DRAIN)
        except Exception:
            return
        if not self.hunger_announced:
            self.hunger_announced = True
            try: player.center_print("^1STARVING^7 — kill to feed")
            except Exception: pass

    # ---------- Accuracy Trial stats ----------
    def _accuracy_note_hit(self, target, damage, mod):
        lightning_mods = {getattr(minqlx, "MOD_LIGHTNING", -102), getattr(minqlx, "MOD_LIGHTNING_DISCHARGE", -103)}
        if mod not in lightning_mods:
            return
        self.acc_hits += 1
        self.acc_damage += max(0, int(damage))
        self.acc_first_hit.setdefault(target.id, time.time())

    def _accuracy_note_kill(self, victim):
        first = self.acc_first_hit.pop(victim.id, None)
        if first is not None:
            self.acc_ttk.append(max(0.0, time.time() - first))

    def _accuracy_summary(self):
        if not self.acc_ttk and not self.acc_hits:
            return None
        avg_ttk = sum(self.acc_ttk) / len(self.acc_ttk) if self.acc_ttk else 0.0
        best_ttk = min(self.acc_ttk) if self.acc_ttk else 0.0
        per_kill = self.acc_damage / max(1, self.kills)
        return {
            "hits": self.acc_hits, "damage": self.acc_damage, "damage_per_kill": per_kill,
            "avg_ttk": avg_ttk, "best_ttk": best_ttk,
        }

    def _start_bounty_hunt(self):
        self.target_score = 0
        self.challenge_goal = 8
        self.msg("^6BOUNTY HUNT^7 — eliminate 8 marked targets. Targets leave a Haste smoke trail.")
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)

    def _start_rocket_tag(self):
        self.target_score = 0
        self.challenge_goal = 10
        self.msg("^6ROCKET TAG^7 — rocket-only; eliminate 10 marked targets. Targets leave a Haste smoke trail.")
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)

    def _start_movement_hunter(self):
        self.msg(f"^6MOVEMENT HUNTER^7 — survive {MOVEMENT_HUNTER_SECONDS} seconds against five armed bots.")
        self._spawn_objective_bots(["slash", "keel", "visor", "anarki", "sarge"], self.skill, auto_clear=False)

    def _arm_movement_hunter_timer(self):
        """Start the survival clock when the bots are live, with on-screen callouts."""
        token = self.controller.token()
        total = float(MOVEMENT_HUNTER_SECONDS)
        for remaining in MOVEMENT_HUNTER_CALLOUTS:
            if remaining >= total:
                continue
            self._movement_hunter_callout(total - remaining, remaining, token)

        @minqlx.delay(total)
        def _finish():
            if self.controller.token_valid(token, Phase.ACTIVE):
                self._finish_mode(
                    f"^2MOVEMENT HUNTER CLEAR!^7 Survived {MOVEMENT_HUNTER_SECONDS} seconds with {self.kills} kills.",
                    success=True,
                )
        _finish()

    def _movement_hunter_callout(self, delay, remaining, token):
        @minqlx.delay(delay)
        def _callout():
            if not self.controller.token_valid(token, Phase.ACTIVE):
                return
            player = self.primary_player()
            if player is None:
                return
            color = "^1" if remaining <= 10 else "^3"
            try: player.center_print(f"{color}{remaining}^7 seconds left")
            except Exception: pass
        _callout()

    def _choose_target(self):
        bots = [bot for bot in self.bot_players() if bot.id in self.controller.enemy_ids]
        if not bots:
            self.target_bot_id = None; self.target_name = None
            return
        rng = random.Random(self.seed + self.target_score * 97 + self.kills * 13)
        target = rng.choice(bots)
        self.target_bot_id = target.id
        self.target_name = clean_name(target)
        # Haste's smoke trail makes the target findable in a crowd.
        try: target.powerups(haste=TARGET_HASTE_SECONDS)
        except Exception as exc: self._log(f"target marker failed: {exc}")
        self.msg(f"^3TARGET:^7 {self.target_name} ^7(follow the smoke trail)")
        player = self.primary_player()
        if player is not None:
            try: player.center_print(f"^3TARGET: ^7{self.target_name}")
            except Exception: pass

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
        # Applied here (not in !pick) so the loadout matches THIS round's trial.
        self._refresh_human_for_round()
        self._spawn_objective_bots(plan["bots"], plan["skill"], auto_clear=True)

    def _arena_clear(self):
        if not self.run:
            return
        if advance_round(self.run):
            save_state(STATE_FILE, self.run)
            self._finish_mode(f"^2ARENA RUN COMPLETE!^7 Rounds cleared: ^3{self.run.round}", success=True)
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
            target(f"^3F{index + 4} ^7/ ^3!pick {index} ^7— {color}{upgrade['name']} ^7[{upgrade['rarity'].upper()}] — {upgrade['text']}")

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
        # _launch_arena_plan re-arms the player once the next round's plan
        # (and trial weapon) exists; doing it here used the previous plan.
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
        data = data if isinstance(data, dict) else {}
        if is_bot(victim):
            self._handle_bot_death(victim, killer, data)
            return
        self._handle_human_death(victim, killer, data)

    @staticmethod
    def _death_mod(data):
        mod = data.get("MOD") if isinstance(data, dict) else None
        return str(mod or "").upper()

    def _handle_bot_death(self, victim, killer, data):
        if victim.id not in self.controller.enemy_ids:
            self._log(f"ignored death of unowned bot id={victim.id}")
            return
        phase_before = self.controller.phase
        self.director_runtime.bot_died(victim, killer)
        mod = self._death_mod(data)
        suicide = bool(data.get("SUICIDE")) or (is_player_object(killer) and killer.id == victim.id)
        killer_human = is_player_object(killer) and not is_bot(killer)
        killer_bot = is_player_object(killer) and is_bot(killer) and not suicide
        if suicide:
            # Own rocket/grenade splash or a fall credited to itself: an ordinary
            # enemy death, not an infighting contract failure.
            self._log(f"bot self-kill id={victim.id} mod={mod or '?'}")
            killer = None
        elif killer_bot and mod == "TELEFRAG":
            # Telefrags ignore friendly fire; staggered spawns on small maps can
            # cause them without the team sandbox being broken.
            self._log(f"bot telefrag {killer.id}->{victim.id}; treated as a neutral death")
            killer = None
        elif killer_bot:
            self.controller.fail(f"bot-vs-bot kill detected ({killer.id}->{victim.id} mod={mod or '?'}); team sandbox contract failed")
            self.clear_all_bots()
            self.msg("^1SOLO ENGINE CONTRACT FAILURE:^7 bots damaged each other; see diagnostics.")
            return

        if killer_human:
            self.kills += 1
            self.last_kill_time = time.time()
            self.hunger_announced = False
            if self.mode == "accuracy_trial":
                self._accuracy_note_kill(victim)
            self._on_human_kill(killer, victim)
            if self.controller.phase in (Phase.COMPLETE, Phase.FAILED):
                return
        elif self.mode in ("bounty_hunt", "rocket_tag") and victim.id == self.target_bot_id:
            self.msg(f"^3TARGET LOST:^7 {self.target_name or 'bounty'} died without your help — new target incoming.")
            self.target_bot_id = None; self.target_name = None
            self._schedule_target_refresh()

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
            if self.mode == "last_stand":
                # After the 1:1 replacement is queued, so extra bots are
                # computed against the real squad size.
                self._update_last_stand_threat()

    def _continuous_modes(self):
        return {
            "gun_game", "last_stand", "one_life", "bounty_hunt", "rocket_tag",
            "movement_hunter", "predator", "accuracy_trial", "speedrun_combat",
            "random_loadout", "duel_2v1",
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
                self._finish_mode("^2TARGET CHALLENGE COMPLETE!", success=True)
                return
            self._schedule_target_refresh()

        if self.mode == "one_life" and self.kills >= 12:
            self._finish_mode("^2ONE LIFE CLEAR!^7 12 kills without dying.", success=True)
        elif self.mode == "predator" and self.kills >= 25:
            self._finish_mode("^2PREDATOR COMPLETE!^7 25-kill streak reached.", success=True)
        elif self.mode == "accuracy_trial" and self.kills >= 20:
            self._finish_mode("^2ACCURACY TRIAL COMPLETE!^7 20 Lightning Gun kills.", success=True)
        elif self.mode == "speedrun_combat":
            if self.kills >= 15:
                self._finish_mode(f"^2SPEEDRUN COMPLETE!^7 {format_duration(time.time() - self.start_time)}", success=True)
            elif self.kills in SPEEDRUN_SPLITS:
                self._speedrun_split(killer)
        elif self.mode == "random_loadout":
            if self.kills >= 20:
                self._finish_mode("^2RANDOM LOADOUT COMPLETE!^7 20 kills cleared.", success=True)
            elif self.kills % 4 == 0:
                self.random_round += 1
                self._apply_human_loadout(killer)

    def _speedrun_split(self, player):
        elapsed = time.time() - self.start_time
        record = self._records_entry().get(f"split_{self.kills}")
        line = f"^5SPLIT {self.kills}/15:^7 {format_duration(elapsed)}"
        if isinstance(record, (int, float)):
            delta = elapsed - float(record)
            line += f" ({'^2' if delta <= 0 else '^1'}{delta:+.2f}s^7 vs best)"
        self.pending_splits[self.kills] = elapsed
        try: player.center_print(line)
        except Exception: pass

    def _schedule_target_refresh(self):
        token = self.controller.token()
        @minqlx.delay(0.8)
        def _pick():
            if self.controller.token_valid(token, Phase.ACTIVE) and self.target_bot_id is None:
                self._choose_target()
        _pick()

    def _handle_human_death(self, victim, killer=None, data=None):
        data = data if isinstance(data, dict) else {}
        if self.controller.phase in (Phase.COMPLETE, Phase.FAILED):
            return
        # Quake Live can emit a death while the joining client is moved onto
        # RED or while a new objective/map is still PREPARING. That transition
        # is not a gameplay loss; deaths only count once the objective is live.
        if self.controller.phase != Phase.ACTIVE:
            self._log(f"ignored pre-active human death phase={self.controller.phase.value}")
            return
        self.director_runtime.human_died()
        self.player_deaths += 1
        if self.mode == "arena_run" and self.run:
            self.run.lives -= 1
            save_state(STATE_FILE, self.run)
            self.msg(f"^1LIFE LOST.^7 {max(0, self.run.lives)} lives remaining.")
            if self.run.lives <= 0:
                self.run.complete = True; save_state(STATE_FILE, self.run)
                self._finish_mode(f"^1ARENA RUN OVER.^7 Reached round {self.run.round}.")
            return
        if self.mode == "wipeout_solo":
            self.wipeout_lives -= 1
            if self.wipeout_lives > 0:
                self.msg(f"^1LIFE LOST.^7 {self.wipeout_lives} lives remaining.")
                return
            self._finish_mode(f"^1WIPEOUT OVER.^7 Squads wiped: {self.wipeout_round - 1}/5.")
            self._spectate_after_death(victim)
            return
        if self.mode == "gun_game":
            if is_player_object(killer) and is_bot(killer) and self._death_mod(data) == "GAUNTLET":
                self._gun_game_humiliated(victim)
            return
        if self.mode in FATAL_DEATH_MODES:
            if self.mode == "horde" and self.horde: self.horde.player_died()
            self._finish_mode("^1RUN OVER.")
            self._spectate_after_death(victim)
        elif self.mode == "random_loadout":
            self.random_round += 1

    # ---------- run results, personal bests ----------
    def _records_key(self):
        key = f"{self.mode}:{self.difficulty}"
        if self.mode == "arena_run":
            key += f":len{self.length}"
        return key

    def _load_records(self):
        try:
            data = json.loads(RECORDS_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _records_entry(self):
        entry = self._load_records().get(self._records_key())
        return entry if isinstance(entry, dict) else {}

    def _save_records(self, records):
        try:
            RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
            temp = RECORDS_FILE.with_name(RECORDS_FILE.name + ".tmp")
            temp.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            temp.replace(RECORDS_FILE)
        except Exception as exc:
            self._log(f"could not save records: {exc}")

    def _progress(self):
        """(label, value) describing how far this run got."""
        elapsed = time.time() - self.start_time
        if self.mode == "horde" and self.horde:
            return "waves cleared", self.horde.wave - 1
        if self.mode == "arena_run" and self.run:
            return "rounds cleared", int(self.run.score)
        if self.mode == "boss_rush":
            return "bosses defeated", self.boss_round - 1
        if self.mode == "wipeout_solo":
            return "squads wiped", self.wipeout_round - 1
        if self.mode == "gauntlet_run":
            return "stages cleared", self.gauntlet_stage - 1
        if self.mode == "gun_game" and self.gun_game:
            return "weapon tiers", self.gun_game.index + (1 if self.gun_game.complete else 0)
        if self.mode in ("bounty_hunt", "rocket_tag"):
            return "targets", self.target_score
        if self.mode == "movement_hunter":
            return "seconds survived", int(min(MOVEMENT_HUNTER_SECONDS, elapsed if self.objective_live_at is None else time.time() - self.objective_live_at))
        return "kills", self.kills

    def _record_result(self, success):
        """Update personal bests for this mode; returns summary lines."""
        now = time.time()
        elapsed = max(0.0, now - self.start_time)
        label, value = self._progress()
        records = self._load_records()
        key = self._records_key()
        entry = records.get(key) if isinstance(records.get(key), dict) else {}
        entry["runs"] = int(entry.get("runs", 0)) + 1
        if success:
            entry["clears"] = int(entry.get("clears", 0)) + 1
        notes = []

        def better(stat, current, higher):
            previous = entry.get(stat)
            improved = previous is None or (current > previous if higher else current < previous)
            if improved:
                entry[stat] = current
            return improved, previous

        improved, previous = better("best_progress", value, True)
        progress_record = improved and previous is not None
        if progress_record:
            notes.append(f"^2NEW BEST^7 {label} (was {previous})")
        if success and self.mode in TIMED_GOAL_MODES:
            improved, previous = better("best_time", round(elapsed, 2), False)
            if improved:
                notes.append(
                    f"^2NEW BEST TIME^7 {format_duration(elapsed)}"
                    + (f" (was {format_duration(previous)})" if previous is not None else "")
                )
            elif previous is not None:
                notes.append(f"^7Best time: {format_duration(previous)}")
            if self.mode == "speedrun_combat":
                for kills, split in self.pending_splits.items():
                    stat = f"split_{kills}"
                    if entry.get(stat) is None or split < entry[stat]:
                        entry[stat] = round(split, 2)
        if self.mode == "last_stand":
            survived = now - (self.objective_live_at or self.start_time)
            improved, previous = better("best_survival", round(survived, 1), True)
            if improved and previous is not None:
                notes.append(f"^2NEW BEST^7 survival {format_duration(survived)}")
        accuracy = self._accuracy_summary() if self.mode == "accuracy_trial" else None
        if accuracy and success and accuracy["avg_ttk"]:
            improved, previous = better("best_avg_ttk", round(accuracy["avg_ttk"], 3), False)
            if improved and previous is not None:
                notes.append(f"^2NEW BEST^7 average time-to-kill (was {previous:.2f}s)")
        entry["last"] = {"success": bool(success), "progress": value, "kills": self.kills, "time": round(elapsed, 2), "at": now}
        records[key] = entry
        self._save_records(records)

        headline = f"^6RESULT:^7 {value} {label}" if label != "kills" else "^6RESULT:^7"
        headline += f" ^7| {self.kills} kills | {format_duration(elapsed)}"
        if self.mode == "last_stand":
            headline += f" | threat level {self.threat_level}"
        if self.mode == "gun_game" and self.gun_game and self.gun_game.demotions:
            headline += f" | {self.gun_game.demotions} demotions"
        lines = [headline]
        if accuracy:
            lines.append(
                f"^6LG:^7 {accuracy['hits']} hits, {accuracy['damage']} damage "
                f"({accuracy['damage_per_kill']:.0f}/kill) | time-to-kill avg {accuracy['avg_ttk']:.2f}s, best {accuracy['best_ttk']:.2f}s"
            )
        if entry.get("best_progress") is not None and not progress_record:
            lines.append(f"^7Personal best: {entry['best_progress']} {label} over {entry['runs']} runs")
        lines.extend(notes)
        return lines

    def _finish_mode(self, message, success=False):
        if self.controller.phase in (Phase.COMPLETE, Phase.FAILED):
            return
        lines = []
        if not self.result_recorded:
            self.result_recorded = True
            try:
                lines = self._record_result(bool(success))
            except Exception as exc:
                self._log(f"result recording failed: {exc}")
        self.controller.finish()
        self.clear_all_bots()
        self.msg(message)
        for line in lines:
            self.msg(line)
        self.msg("^7Type ^3!again^7 to replay with a new seed.")
        player = self.primary_player()
        if player is not None:
            try: player.center_print(message + ("\n" + lines[0] if lines else ""))
            except Exception: pass

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
        if self.director_runtime.apply_bot_loadout(player, plan):
            return
        try:
            # Scripted servers disable map weapon/ammo pickups. Give every bot
            # a combat-ready loadout so its AI can immediately hunt the human
            # instead of spending the opening of a wave searching for gear.
            player.health = int(plan.get("health", 100)) if plan else 100
            player.armor = int(plan.get("armor", 25)) if plan else 25
            player.weapons(
                reset=True,
                g=True, mg=True, sg=True, gl=True,
                rl=True, lg=True, rg=True, pg=True,
            )
            player.ammo(
                reset=True,
                mg=160, sg=40, gl=30, rl=40,
                lg=120, rg=25, pg=120,
            )

            if self.mode in ("arena_run", "boss_rush", "gauntlet_run") and plan:
                trial = {"rocket": 5, "lg": 6, "rail": 7, "plasma": 8}.get(plan.get("theme"))
                if trial:
                    self._give_single_weapon(player, trial)
                elif plan.get("boss"):
                    self._give_single_weapon(player, 5)
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
        try:
            self.director_runtime.note_damage(target, attacker, damage)
        except Exception as exc:
            self._log(f"director damage observation failed: {exc}")
        if not is_player_object(target) or not is_player_object(attacker):
            return
        if attacker.id == target.id:
            return
        if is_bot(attacker) and not is_bot(target):
            # Plan damage multipliers apply to every mode that authors one
            # (Arena Run rounds, Boss Rush/Gauntlet bosses, Last Stand threat);
            # previously only Arena Run consumed them.
            self.last_hurt_time[target.id] = time.time()
            multiplier = max(0.0, float((self.current_plan or {}).get("damage_mult", 1.0)) - 1.0)
            bonus = max(0, int(round(damage * multiplier)))
            if bonus and getattr(target, "is_alive", False):
                try: target.health = max(1, int(target.health) - bonus)
                except Exception: pass
            return
        if is_bot(attacker) or is_bot(target) is False:
            return
        try: mod = int(means_of_death)
        except Exception: return
        if self.mode == "accuracy_trial":
            self._accuracy_note_hit(target, damage, mod)
            return
        if self.mode != "arena_run" or not self.run:
            return
        effects = upgrade_effects(self.run)
        multiplier = float(effects.get("damage_mult", 0))
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
            if not parts:
                return
            verb = parts[0].lower()
            if verb in READY_COMMANDS:
                # Readying up would take the sandbox out of warmup and back into
                # Quake Live's match rules, where an empty BLUE team forfeits.
                try:
                    player.center_print("^7Solo runs stay in warmup; ready-up is not needed.")
                except Exception:
                    pass
                return minqlx.RET_STOP_ALL
            if verb == "qlpick":
                if len(parts) >= 2:
                    self.cmd_pick(player, ["!pick", parts[1]], None)
                return minqlx.RET_STOP_ALL
            if verb != "qldash":
                return
            if len(parts) >= 2 and parts[1].lower() in ("left", "right", "auto"):
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
                elif direction == "right": nx, ny = vy / speed, -vx / speed
                else: nx, ny = vx / speed, vy / speed  # "auto" with no strafe input: boost along travel
            impulse = self._dash_power(); nvx, nvy = vx + nx * impulse, vy + ny * impulse
            speed = math.hypot(nvx, nvy)
            if speed > 950:
                scale = 950 / speed; nvx *= scale; nvy *= scale
            nz = float(v.z) if was_airborne else max(float(v.z), self.ground_dash_hop)
            # player.velocity()'s setter requires int coordinates on the real
            # engine binding ("'float' object cannot be interpreted as an
            # integer" otherwise); the fake test harness casts silently to
            # float and can't catch this. Confirmed live: every dash crashed
            # here, so nothing ever applied — the player mashing the dead key
            # sent enough qldash commands in one second to trip Quake Live's
            # flood protection on its own, independent of the dedicated-key
            # fix above.
            player.velocity(x=int(round(nvx)), y=int(round(nvy)), z=int(round(nz)))
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
                    vz *= 1.0 + jump; player.velocity(z=int(round(vz))); self.airborne.add(player.id)
                self.prev_vz[player.id] = vz
                if regen and self.mode == "arena_run" and self.run:
                    if now - self.last_hurt_time.get(player.id, 0) >= 4.0 and now - self.last_regen_tick.get(player.id, 0) >= 1.0:
                        if int(player.health) < self._arena_max_health():
                            player.health = min(self._arena_max_health(), int(player.health) + int(regen))
                        self.last_regen_tick[player.id] = now
            except Exception:
                continue
        if now >= self.next_threat_check:
            self.next_threat_check = now + 1.0
            self._update_last_stand_threat(now)
        self._tick_predator_hunger(now)
        try:
            self.director_runtime.tick()
        except Exception as exc:
            self._log(f"director tick failed: {exc}")

    # ---------- commands ----------
    def cmd_run(self, player, msg, channel):
        extra = ""
        if self.mode == "horde" and self.horde: extra = f" wave={self.horde.wave}"
        if self.mode == "arena_run" and self.run: extra = f" round={self.run.round} lives={self.run.lives}"
        if self.mode == "gun_game" and self.gun_game: extra = f" weapon={self.gun_game.weapon_name}"
        player.tell(
            f"^6Solo v5:^7 mode={self.mode} phase={self.controller.phase.value}{extra} "
            f"alive={len(self.controller.enemy_ids)} spawns={self.controller.fulfilled_spawns}/{self.controller.expected_spawns} "
            f"director=[{self.director_runtime.summary()}]"
        )

    def cmd_best(self, player, msg, channel):
        entry = self._records_entry()
        if not entry:
            player.tell(f"^7No records yet for {self.mode.replace('_', ' ')} ({self.difficulty})."); return
        label, _value = self._progress()
        parts = [f"best {entry.get('best_progress', 0)} {label}", f"{entry.get('runs', 0)} runs"]
        if entry.get("clears"):
            parts.append(f"{entry['clears']} clears")
        if isinstance(entry.get("best_time"), (int, float)):
            parts.append(f"best time {format_duration(entry['best_time'])}")
        if isinstance(entry.get("best_survival"), (int, float)):
            parts.append(f"best survival {format_duration(entry['best_survival'])}")
        if isinstance(entry.get("best_avg_ttk"), (int, float)):
            parts.append(f"best avg TTK {entry['best_avg_ttk']:.2f}s")
        player.tell(f"^6RECORDS ({self.mode.replace('_', ' ')}, {self.difficulty}):^7 " + ", ".join(parts))

    def cmd_help(self, player, msg, channel):
        player.tell("^6Solo Engine v5:^7 !run shows lifecycle state; !again replays with a new seed; !best shows your records; your dash key (or !dash left/right) fires the side thrusters.")
        if self.mode == "arena_run":
            player.tell("^7Arena Run: press ^3F5/F6/F7^7 (or ^3!pick 1/2/3^7) and ^3!upgrades^7 between rounds.")
