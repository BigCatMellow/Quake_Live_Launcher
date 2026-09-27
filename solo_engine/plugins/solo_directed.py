#!/usr/bin/env python3
"""Director-managed Solo runtime with in-place match switching.

Quake Live remains connected to one local QLDS process. New Solo selections
arrive through a tiny file-backed handoff so the plugin can reset its scripted
state, change map, and start the next mode without restarting the client.
"""
from __future__ import annotations

import json
import os
import random
import re
import time

import minqlx

try:
    from .solo_arcade import (
        RUNTIME_DIR,
        SUPPORTED_MODES,
        DirectorRuntime,
        GunGameState,
        HordeState,
        PLUGIN_VERSION,
        Phase,
        SoloController,
        clamp,
        is_bot,
        is_player_object,
        solo_arcade,
    )
except ImportError:
    from solo_arcade import (
        RUNTIME_DIR,
        SUPPORTED_MODES,
        DirectorRuntime,
        GunGameState,
        HordeState,
        PLUGIN_VERSION,
        Phase,
        SoloController,
        clamp,
        is_bot,
        is_player_object,
        solo_arcade,
    )

MATCH_REQUEST_FILE = RUNTIME_DIR / "match_request.json"
MATCH_STATUS_FILE = RUNTIME_DIR / "match_status.json"
HOTLOAD_READY_FILE = RUNTIME_DIR / "hotload_ready.json"
# Protocol 2 = permanent-warmup sandbox; 3 = mode overhaul (!again, F5-F7
# picks, records); 4 = spawn Director; 5 = no training mode / flood protection
# off. A still-running older server must be restarted, never hot-loaded into.
HOTLOAD_PROTOCOL = 5
MATCH_STATES = {"countdown", "in_progress"}


class solo_directed(solo_arcade):
    """Solo runtime with one authoritative adaptive Director and hot-load bridge."""

    def __init__(self):
        super().__init__()
        self.last_match_request_id = self._existing_match_request_id()
        self.active_match_request_id = None
        self.next_match_request_poll = 0.0
        self.next_warmup_check = 0.0
        self._write_hotload_ready()
        self.add_command("director", self.cmd_director)
        self.add_command(("again", "restart", "replay"), self.cmd_again)

    # ---------- hot-load capability handshake ----------
    def _write_hotload_ready(self):
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "ready": True,
                "protocol": HOTLOAD_PROTOCOL,
                "pid": os.getpid(),
                "mode": self.mode,
                "plugin_version": PLUGIN_VERSION,
                "time": time.time(),
            }
            temp = HOTLOAD_READY_FILE.with_name(HOTLOAD_READY_FILE.name + ".tmp")
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temp.replace(HOTLOAD_READY_FILE)
        except Exception as exc:
            self._log(f"could not write hot-load readiness: {exc}")

    def handle_unload(self, plugin):
        try:
            HOTLOAD_READY_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        return super().handle_unload(plugin)

    # ---------- lifecycle ----------
    # The old anti-forfeit "training contract" (g_training 1 plus
    # allow_single_player(True), re-applied every second) is gone: it switched
    # on Quake Live's training-match HUD ("This match will determine ..."),
    # which flashed on every re-application. Permanent warmup (solo_arcade)
    # is what prevents the forfeit.
    def handle_map(self, map_name, factory):
        result = super().handle_map(map_name, factory)
        self.next_warmup_check = 0.0
        return result

    def handle_player_spawn(self, player):
        result = super().handle_player_spawn(player)
        if not is_player_object(player):
            return result
        if is_bot(player):
            if self.active_match_request_id and self.controller.phase == Phase.ACTIVE:
                self._write_match_status("active", self.active_match_request_id)
        elif self.active_match_request_id and self.mode_started:
            self._write_match_status("started", self.active_match_request_id)
        return result

    # ---------- file-backed in-place match handoff ----------
    def _existing_match_request_id(self):
        try:
            payload = json.loads(MATCH_REQUEST_FILE.read_text(encoding="utf-8"))
            return str(payload.get("request_id") or "")
        except Exception:
            return ""

    def _write_match_status(self, state, request_id=None, error=None):
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "state": str(state),
                "request_id": request_id or self.active_match_request_id,
                "mode": self.mode,
                "map": self.current_map_name(),
                "phase": self.controller.phase.value,
                "game_state": self.game_state(),
                "time": time.time(),
                "error": error,
            }
            temp = MATCH_STATUS_FILE.with_name(MATCH_STATUS_FILE.name + ".tmp")
            temp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            temp.replace(MATCH_STATUS_FILE)
        except Exception as exc:
            self._log(f"could not write match status: {exc}")

    def _poll_match_request(self, now):
        if now < self.next_match_request_poll:
            return
        self.next_match_request_poll = now + 0.20
        try:
            payload = json.loads(MATCH_REQUEST_FILE.read_text(encoding="utf-8"))
        except Exception:
            return
        request_id = str(payload.get("request_id") or "")
        if not request_id or request_id == self.last_match_request_id:
            return
        self.last_match_request_id = request_id
        try:
            session = self._load_session()
            if str(session.get("mode", "")) != str(payload.get("mode", "")):
                raise RuntimeError("session/request mode mismatch")
            self._hot_switch_session(session, request_id)
        except Exception as exc:
            self._log(f"hot match switch failed id={request_id}: {exc}")
            self._write_match_status("failed", request_id, str(exc))

    def _hot_switch_session(self, session, request_id):
        new_mode = str(session.get("mode", ""))
        if new_mode not in SUPPORTED_MODES:
            raise RuntimeError(f"unsupported hot-switch mode: {new_mode}")
        map_name = str(session.get("map", "campgrounds")).lower()
        if not re.fullmatch(r"[a-zA-Z0-9._-]+", map_name):
            raise RuntimeError(f"unsafe map name in hot-switch request: {map_name!r}")

        human = self.primary_player()
        try:
            self.director_runtime.finish_session(
                "switched",
                kills=self.kills,
                deaths=self.player_deaths,
                duration=max(0.0, time.time() - self.start_time),
            )
        except Exception:
            pass
        # Bump the existing controller generation before clearing the old
        # encounter. Reusing this controller keeps generations monotonic across
        # hot-loaded modes, so a delayed callback from Horde can never collide
        # with a freshly-created controller generation in Gun Game (or vice
        # versa).
        self.controller.finish()
        self.clear_all_bots()

        self.session = dict(session)
        self._read_director_settings()
        self.mode = new_mode
        self.seed = int(session.get("seed", int(time.time())))
        self.skill = clamp(int(session.get("skill", 3)), 1, 5)
        self.difficulty = str(session.get("difficulty", "normal"))
        self.length = int(session.get("length", 20))
        self.maps = list(session.get("maps") or [map_name])
        self.map_pools = dict(session.get("map_pools") or {})
        self.movement = dict(session.get("movement") or {})
        self.air_control = str(self.movement.get("air_control", "enhanced")).lower()
        self.side_thrusters = bool(self.movement.get("side_thrusters", True))
        self.dash_strength = float(self.movement.get("dash_strength", 340))
        self.ground_dash_hop = float(self.movement.get("ground_dash_hop", 155))
        self.base_dash_charges = max(1, min(3, int(self.movement.get("dash_charges", 1))))

        self.controller.mode = self.mode
        # One shared reset keeps hot-load in lockstep with a fresh plugin.
        self._reset_mode_state()
        self.director_runtime = DirectorRuntime(self, self.mode, self.difficulty, self.seed, RUNTIME_DIR)
        self.placement_rng.seed(self.seed ^ 0x5A17)
        self.active_match_request_id = str(request_id)

        self.controller.wait_for_player()
        if human is not None:
            self.player_id = human.id
            self.controller.player_loaded(human.id)
            self._put_team(human, "red")
        else:
            self.player_id = None

        self._configure_engine()
        self.next_warmup_check = 0.0
        self._write_ready(True)
        self._write_hotload_ready()
        self._write_match_status("loading", request_id)
        self.msg(f"^6SOLO:^7 loading {self.mode.replace('_', ' ').upper()}…")
        self._log(f"hot match switch id={request_id} mode={self.mode} map={map_name}")
        minqlx.console_command(f"map {map_name} tdm")

    def handle_frame(self):
        now = time.time()
        if now >= self.next_warmup_check:
            self.next_warmup_check = now + 1.0
            # Backstop for the game_countdown/game_start hooks: if the engine
            # has left warmup by any route, put it back before it can forfeit.
            if self.game_state() in MATCH_STATES:
                self.hold_warmup("frame check found a live match")
        self._poll_match_request(now)
        return super().handle_frame()

    # ---------- replay ----------
    def cmd_again(self, player, msg, channel):
        """Replay the current mode in place with a fresh seed."""
        session = dict(self.session)
        session["mode"] = self.mode
        session["seed"] = random.SystemRandom().randint(1, 2**31 - 1)
        session["continue_run"] = False
        session.setdefault("map", self.current_map_name() or "campgrounds")
        request_id = f"again-{time.time_ns()}"
        try:
            self._hot_switch_session(session, request_id)
        except Exception as exc:
            self._log(f"!again failed: {exc}")
            try: player.tell(f"^1Could not restart: {exc}")
            except Exception: pass

    # ---------- diagnostics ----------
    def cmd_director(self, player, msg, channel):
        runtime = self.director_runtime
        snapshot = runtime.director.last_snapshot
        if snapshot is None:
            player.tell(f"^6DIRECTOR:^7 {runtime.director.profile.name} — waiting for encounter data.")
            return
        player.tell("^6DIRECTOR:^7 " + runtime.summary())
        roles = {}
        for track in runtime.director.tracks.values():
            roles[track.role] = roles.get(track.role, 0) + 1
        if roles:
            player.tell("^7Roles: " + ", ".join(f"{name} x{count}" for name, count in sorted(roles.items())))
        if runtime.director.should_hold_reinforcements(runtime.now()):
            player.tell("^3Director recovery window:^7 holding future reinforcements briefly.")
        player.tell("^7Spawns: " + self.spawn_summary())
        player.tell(
            "^7Learning: pressure shift "
            f"{runtime.learning.pressure_shift():+.1f}; actions and outcomes are logged for review."
        )

    def cmd_help(self, player, msg, channel):
        super().cmd_help(player, msg, channel)
        player.tell("^7Director: ^3!director ^7shows pressure, role mix, and learned pressure shift.")
