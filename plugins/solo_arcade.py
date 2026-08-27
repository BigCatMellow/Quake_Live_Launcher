#!/usr/bin/env python3
"""v5 Solo Engine adapter for shinqlx/minqlx.

The base Quake Live FFA match is deliberately boring: no frag/time limit,
no ready-up and no automatic bots. The plugin owns the actual solo objective.

v5-alpha intentionally enables only Horde and Gun Game while the shared
controller is proven in-game. Other scripted modes should be ported onto the
same controller rather than copying the v4.11 lifecycle.
"""
from __future__ import annotations

import json
import random
import time
from pathlib import Path

import minqlx

from modes.gun_game import GunGameState
from modes.horde import HordeState
from solo_controller import Phase, SoloController

SESSION_FILE = Path.home() / ".config/quake-live-launcher/solo_session.json"
BOT_ROSTER = ("slash", "keel", "visor", "anarki", "sarge", "ranger", "doom", "bones")
SUPPORTED_MODES = {"horde", "gun_game"}


def is_bot(player) -> bool:
    """QL bots use synthetic Steam IDs in the 9... namespace.

    Keep this in one function so it can be replaced if live diagnostics show a
    different identifier on a particular QLDS build.
    """
    try:
        return str(int(player.steam_id)).startswith("9")
    except Exception:
        return False


def clamp(value, low, high):
    return max(low, min(high, value))


class solo_arcade(minqlx.Plugin):
    def __init__(self):
        self.session = self._load_session()
        self.mode = str(self.session.get("mode", "horde"))
        self.seed = int(self.session.get("seed", int(time.time())))
        self.skill = clamp(int(self.session.get("skill", 3)), 1, 5)
        self.controller = SoloController(self.mode)
        self.horde = HordeState(self.seed) if self.mode == "horde" else None
        self.gun_game = GunGameState() if self.mode == "gun_game" else None
        self.player_id = None
        self._pending_adds = 0

        self._require_runtime_contract()
        self._configure_engine()

        self.add_hook("player_loaded", self.handle_player_loaded)
        self.add_hook("player_spawn", self.handle_player_spawn)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("death", self.handle_death)
        self.add_hook("map", self.handle_map)
        self.add_hook("new_game", self.handle_new_game)

        self.add_command(("run", "solo"), self.cmd_run)
        self.add_command("solohelp", self.cmd_help)

        self.controller.wait_for_player()
        self._log(f"loaded mode={self.mode} seed={self.seed} skill={self.skill}")

    def _load_session(self):
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _require_runtime_contract(self):
        if self.mode not in SUPPORTED_MODES:
            raise RuntimeError(
                f"v5-alpha scripted mode '{self.mode}' is not ported yet; "
                "use Horde or Gun Game while the shared controller is stabilized"
            )
        if not hasattr(minqlx, "allow_single_player"):
            raise RuntimeError("shinqlx/minqlx build does not expose allow_single_player()")
        try:
            zmq_enabled = int(self.get_cvar("zmq_stats_enable") or 0)
        except Exception:
            zmq_enabled = 0
        if zmq_enabled != 1:
            raise RuntimeError(
                "zmq_stats_enable must be 1 before solo_arcade loads; "
                "death/game lifecycle hooks depend on the stats listener"
            )

    def _configure_engine(self):
        minqlx.allow_single_player(True)
        values = {
            "sv_hostname": "Quake Live // Solo Engine v5",
            "bot_enable": "1",
            "bot_thinktime": "0",
            "bot_challenge": "1",
            "bot_minplayers": "0",
            "fraglimit": "0",
            "timelimit": "0",
            "g_doWarmup": "0",
            "g_warmup": "0",
            "sv_warmupReadyPercentage": "0",
            "g_warmupDelay": "0",
        }
        for name, value in values.items():
            self.set_cvar(name, value)

    def _log(self, message):
        minqlx.console_print(f"[solo_arcade:v5] {message}")

    def human_players(self):
        players = []
        teams = self.teams()
        for team in ("free", "red", "blue"):
            players.extend(teams.get(team, []))
        return [player for player in players if not is_bot(player)]

    def bot_players(self):
        players = []
        teams = self.teams()
        for team in ("free", "red", "blue"):
            players.extend(teams.get(team, []))
        return [player for player in players if is_bot(player)]

    def primary_player(self):
        if self.player_id is not None:
            for player in self.human_players():
                if player.id == self.player_id:
                    return player
        humans = self.human_players()
        return humans[0] if humans else None

    def clear_all_bots(self):
        for bot in list(self.bot_players()):
            try:
                bot.kick("solo objective reset")
            except Exception:
                minqlx.console_command(f"kick {bot.id}")
        self.controller.enemy_ids.clear()

    def _spawn_objective_bots(self, names, skill):
        names = list(names)
        token = self.controller.begin_objective(len(names))
        self.clear_all_bots()
        self._pending_adds = len(names)
        for index, name in enumerate(names):
            self._add_bot_later(name, skill, index * 0.15, token)

    def _add_bot_later(self, name, skill, delay, token):
        @minqlx.delay(delay)
        def _add():
            if not self.controller.token_valid(token, Phase.PREPARING):
                return
            minqlx.console_command(f"addbot {name} {clamp(int(skill), 1, 5)} free")
        _add()

    @minqlx.delay(0.05)
    def _kick_dead_bot(self, client_id):
        for bot in self.bot_players():
            if bot.id == client_id:
                try:
                    bot.kick("solo enemy defeated")
                except Exception:
                    minqlx.console_command(f"kick {client_id}")
                return

    def handle_new_game(self):
        minqlx.allow_single_player(True)
        self._configure_engine()

    def handle_map(self, map_name, factory):
        minqlx.allow_single_player(True)
        self._log(f"map={map_name} factory={factory} phase={self.controller.phase.value}")

    def handle_player_loaded(self, player):
        if not player:
            return
        if is_bot(player):
            return
        self.player_id = player.id
        minqlx.allow_single_player(True)
        if self.controller.player_loaded(player.id):
            self._start_selected_mode()

    def handle_player_spawn(self, player):
        if not player:
            return
        if is_bot(player):
            activated = self.controller.enemy_spawned(player.id)
            self._pending_adds = max(0, self._pending_adds - 1)
            self._log(
                f"enemy spawned id={player.id} owned={player.id in self.controller.enemy_ids} "
                f"count={len(self.controller.enemy_ids)}/{self.controller.expected_enemies} "
                f"phase={self.controller.phase.value}"
            )
            if activated:
                self.msg("^2OBJECTIVE LIVE")
            return
        self.player_id = player.id
        self._apply_human_loadout(player)

    def handle_player_disconnect(self, player, reason):
        if player and not is_bot(player) and player.id == self.player_id:
            self.controller.finish()
            self.clear_all_bots()

    def _start_selected_mode(self):
        self.clear_all_bots()
        if self.mode == "horde":
            self._start_horde_wave()
        elif self.mode == "gun_game":
            self._start_gun_game()

    def _start_horde_wave(self):
        if not self.horde or self.horde.complete:
            return
        plan = self.horde.plan()
        elite = " ^1ELITE" if plan["elite"] else ""
        self.msg(f"^6HORDE WAVE {plan['wave']}^7 — {plan['count']} enemies{elite}")
        self._spawn_objective_bots(plan["bots"], plan["skill"])

    def _horde_objective_clear(self):
        if not self.horde or self.horde.complete:
            return
        self.horde.clear_wave()
        token = self.controller.token()

        @minqlx.delay(1.0)
        def _next():
            if not self.controller.token_valid(token, Phase.BETWEEN_ROUNDS):
                return
            self._start_horde_wave()
        _next()

    def _start_gun_game(self):
        if not self.gun_game:
            return
        self.msg(f"^6GUN GAME^7 — start with {self.gun_game.weapon_name}")
        names = ["slash", "keel", "visor", "anarki", "sarge"]
        self._spawn_objective_bots(names, self.skill)
        human = self.primary_player()
        if human:
            self._give_single_weapon(human, self.gun_game.weapon)

    def _gun_game_kill(self, killer, victim_id):
        if not self.gun_game or self.gun_game.complete:
            return
        self.controller.remove_enemy(victim_id)
        if self.gun_game.scored_kill():
            self.controller.finish()
            self.clear_all_bots()
            self.msg("^2GUN GAME COMPLETE!")
            return
        self._give_single_weapon(killer, self.gun_game.weapon)
        killer.tell(f"^2ADVANCE:^7 {self.gun_game.weapon_name}")
        token = self.controller.token()
        self._add_replacement_bot(random.choice(BOT_ROSTER), self.skill, token)

    def _add_replacement_bot(self, name, skill, token):
        @minqlx.delay(0.35)
        def _add():
            if not self.controller.token_valid(token, Phase.ACTIVE):
                return
            minqlx.console_command(f"addbot {name} {clamp(int(skill), 1, 5)} free")
        _add()

    def handle_death(self, victim, killer, data):
        if not victim:
            return
        if is_bot(victim):
            owned = victim.id in self.controller.enemy_ids
            self._kick_dead_bot(victim.id)
            if not owned:
                self._log(f"ignored death of unowned bot id={victim.id}")
                return
            killer_human = bool(killer and not is_bot(killer))
            if self.mode == "gun_game":
                if killer_human:
                    self._gun_game_kill(killer, victim.id)
                else:
                    self.controller.remove_enemy(victim.id)
                    self._add_replacement_bot(random.choice(BOT_ROSTER), self.skill, self.controller.token())
                return
            if self.controller.enemy_died(victim.id):
                if self.mode == "horde":
                    self._horde_objective_clear()
            return
        if self.controller.phase in (Phase.COMPLETE, Phase.FAILED):
            return
        self.controller.finish()
        self.clear_all_bots()
        if self.mode == "horde" and self.horde:
            self.horde.player_died()
            self.msg(f"^1HORDE RUN OVER.^7 Reached wave {self.horde.wave}.")
        elif self.mode == "gun_game" and self.gun_game:
            self.msg(f"^1GUN GAME RUN OVER.^7 Reached {self.gun_game.weapon_name}.")

    def _apply_human_loadout(self, player):
        if self.mode == "gun_game" and self.gun_game:
            self._give_single_weapon(player, self.gun_game.weapon)
            return
        try:
            player.health = 150
            player.armor = 50
            player.weapons(g=True, mg=True, sg=True, gl=True, rl=True, lg=True, rg=True, pg=True)
            player.ammo(mg=200, sg=50, gl=50, rl=50, lg=150, rg=30, pg=150)
            player.weapon(5)
        except Exception as exc:
            self._log(f"human loadout failed: {exc}")

    def _give_single_weapon(self, player, weapon):
        keys = {1:"g", 2:"mg", 3:"sg", 4:"gl", 5:"rl", 6:"lg", 7:"rg", 8:"pg"}
        key = keys.get(int(weapon))
        try:
            kwargs = {"g": True}
            if key:
                kwargs[key] = True
            player.weapons(reset=True, **kwargs)
            if key and key != "g":
                player.ammo(reset=True, **{key: 200})
            else:
                player.ammo(reset=True)
            player.weapon(int(weapon))
        except Exception as exc:
            self._log(f"weapon kit failed: {exc}")

    def cmd_run(self, player, msg, channel):
        if self.mode == "horde" and self.horde:
            player.tell(
                f"^6Horde:^7 wave {self.horde.wave}  phase {self.controller.phase.value}  "
                f"enemies {len(self.controller.enemy_ids)}"
            )
        elif self.mode == "gun_game" and self.gun_game:
            player.tell(
                f"^6Gun Game:^7 {self.gun_game.weapon_name}  phase {self.controller.phase.value}  "
                f"enemies {len(self.controller.enemy_ids)}"
            )

    def cmd_help(self, player, msg, channel):
        player.tell("^6Solo Engine v5 alpha:^7 !run shows the controller state.")
