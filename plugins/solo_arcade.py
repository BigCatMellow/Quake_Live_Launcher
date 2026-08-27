#!/usr/bin/env python3
"""v5 Solo Engine adapter for shinqlx/minqlx.

Quake Live supplies the combat sandbox. This plugin owns scripted Solo state.
The base match has no frag/time limit, no ready-up and no automatic bots.
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
from solo_core import RARITY_COLOR, UPGRADE_BY_ID, advance_round, load_state, new_state, pick_upgrade, roll_upgrade_choices, round_plan, save_state, upgrade_effects

SESSION_FILE = Path.home() / ".config/quake-live-launcher/solo_session.json"
STATE_FILE = Path.home() / ".config/quake-live-launcher/arena_run_state_v5.json"
BOT_ROSTER = ("slash", "keel", "visor", "anarki", "sarge", "ranger", "doom", "bones")
SUPPORTED_MODES = {"horde", "gun_game", "arena_run"}


def is_bot(player) -> bool:
    try: return str(int(player.steam_id)).startswith("9")
    except Exception: return False


def clamp(value, low, high): return max(low, min(high, value))


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
        self.controller = SoloController(self.mode)
        self.horde = HordeState(self.seed) if self.mode == "horde" else None
        self.gun_game = GunGameState() if self.mode == "gun_game" else None
        self.run = None
        self.current_plan = None
        self.player_id = None
        self.kills = 0
        self.last_damage_time = {}
        self.lg_streak = {}
        self.rail_hits = {}
        self._require_runtime_contract()
        self._configure_engine()
        self.add_hook("player_loaded", self.handle_player_loaded)
        self.add_hook("player_spawn", self.handle_player_spawn)
        self.add_hook("player_disconnect", self.handle_player_disconnect)
        self.add_hook("death", self.handle_death)
        self.add_hook("damage", self.handle_damage)
        self.add_hook("map", self.handle_map)
        self.add_hook("new_game", self.handle_new_game)
        self.add_command(("run", "solo"), self.cmd_run)
        self.add_command("solohelp", self.cmd_help)
        self.add_command(("pick", "choose"), self.cmd_pick)
        self.add_command(("upgrades", "build"), self.cmd_upgrades)
        self.controller.wait_for_player()
        self._log(f"loaded mode={self.mode} seed={self.seed} skill={self.skill}")

    def _load_session(self):
        try:
            data = json.loads(SESSION_FILE.read_text(encoding="utf-8")); return data if isinstance(data, dict) else {}
        except Exception: return {}

    def _require_runtime_contract(self):
        if self.mode not in SUPPORTED_MODES: raise RuntimeError(f"v5-alpha scripted mode '{self.mode}' is not ported yet; use Horde, Gun Game, or Arena Run")
        if not hasattr(minqlx, "allow_single_player"): raise RuntimeError("shinqlx/minqlx build does not expose allow_single_player()")
        try: zmq_enabled = int(self.get_cvar("zmq_stats_enable") or 0)
        except Exception: zmq_enabled = 0
        if zmq_enabled != 1: raise RuntimeError("zmq_stats_enable must be 1 before solo_arcade loads; death/game lifecycle hooks depend on the stats listener")

    def _configure_engine(self):
        minqlx.allow_single_player(True)
        for name, value in {"sv_hostname":"Quake Live // Solo Engine v5","bot_enable":"1","bot_thinktime":"0","bot_challenge":"1","bot_minplayers":"0","fraglimit":"0","timelimit":"0","g_doWarmup":"0","g_warmup":"0","sv_warmupReadyPercentage":"0","g_warmupDelay":"0"}.items(): self.set_cvar(name, value)

    def _log(self, message): minqlx.console_print(f"[solo_arcade:v5] {message}")

    def human_players(self):
        players=[]; teams=self.teams()
        for team in ("free","red","blue"): players.extend(teams.get(team,[]))
        return [p for p in players if not is_bot(p)]

    def bot_players(self):
        players=[]; teams=self.teams()
        for team in ("free","red","blue"): players.extend(teams.get(team,[]))
        return [p for p in players if is_bot(p)]

    def primary_player(self):
        if self.player_id is not None:
            for player in self.human_players():
                if player.id == self.player_id: return player
        humans=self.human_players(); return humans[0] if humans else None

    def clear_all_bots(self):
        for bot in list(self.bot_players()):
            try: bot.kick("solo objective reset")
            except Exception: minqlx.console_command(f"kick {bot.id}")
        self.controller.enemy_ids.clear()

    def _spawn_objective_bots(self, names, skill):
        names=list(names); token=self.controller.begin_objective(len(names)); self.clear_all_bots()
        for index,name in enumerate(names): self._add_bot_later(name, skill, index*0.15, token)

    def _add_bot_later(self, name, skill, delay, token):
        @minqlx.delay(delay)
        def _add():
            if self.controller.token_valid(token, Phase.PREPARING): minqlx.console_command(f"addbot {name} {clamp(int(skill),1,5)} free")
        _add()

    def _add_replacement_bot(self, name, skill, token):
        @minqlx.delay(0.35)
        def _add():
            if self.controller.token_valid(token, Phase.ACTIVE): minqlx.console_command(f"addbot {name} {clamp(int(skill),1,5)} free")
        _add()

    @minqlx.delay(0.05)
    def _kick_dead_bot(self, client_id):
        for bot in self.bot_players():
            if bot.id == client_id:
                try: bot.kick("solo enemy defeated")
                except Exception: minqlx.console_command(f"kick {client_id}")
                return

    def handle_new_game(self): minqlx.allow_single_player(True); self._configure_engine()
    def handle_map(self, map_name, factory): minqlx.allow_single_player(True); self._log(f"map={map_name} factory={factory} phase={self.controller.phase.value}")

    def handle_player_loaded(self, player):
        if not player or is_bot(player): return
        self.player_id=player.id; minqlx.allow_single_player(True)
        if self.controller.pending_map:
            payload=self.controller.resume_map_if_ready(self.current_map_name(), player.id)
            if payload and payload.get("kind") == "arena": self._launch_arena_plan(payload["plan"]); return
        if self.controller.player_loaded(player.id): self._start_selected_mode()

    def handle_player_spawn(self, player):
        if not player: return
        if is_bot(player):
            activated=self.controller.enemy_spawned(player.id)
            if self.mode == "arena_run": self._apply_arena_bot(player)
            self._log(f"enemy spawned id={player.id} owned={player.id in self.controller.enemy_ids} count={len(self.controller.enemy_ids)}/{self.controller.expected_enemies} phase={self.controller.phase.value}")
            if activated: self.msg("^2OBJECTIVE LIVE")
            return
        self.player_id=player.id; self._apply_human_loadout(player)

    def handle_player_disconnect(self, player, reason):
        if player and not is_bot(player) and player.id == self.player_id: self.controller.finish(); self.clear_all_bots()

    def _start_selected_mode(self):
        self.clear_all_bots()
        if self.mode == "horde": self._start_horde_wave()
        elif self.mode == "gun_game": self._start_gun_game()
        elif self.mode == "arena_run": self._start_arena_run()

    def _start_horde_wave(self):
        if not self.horde or self.horde.complete: return
        plan=self.horde.plan(); elite=" ^1ELITE" if plan["elite"] else ""; self.msg(f"^6HORDE WAVE {plan['wave']}^7 — {plan['count']} enemies{elite}"); self._spawn_objective_bots(plan["bots"],plan["skill"])

    def _horde_objective_clear(self):
        if not self.horde or self.horde.complete: return
        self.horde.clear_wave(); token=self.controller.token()
        @minqlx.delay(1.0)
        def _next():
            if self.controller.token_valid(token, Phase.BETWEEN_ROUNDS): self._start_horde_wave()
        _next()

    def _start_gun_game(self):
        if not self.gun_game: return
        self.msg(f"^6GUN GAME^7 — start with {self.gun_game.weapon_name}"); self._spawn_objective_bots(["slash","keel","visor","anarki","sarge"],self.skill); human=self.primary_player()
        if human: self._give_single_weapon(human,self.gun_game.weapon)

    def _gun_game_kill(self, killer, victim_id):
        if not self.gun_game or self.gun_game.complete: return
        self.controller.remove_enemy(victim_id)
        if self.gun_game.scored_kill(): self.controller.finish(); self.clear_all_bots(); self.msg("^2GUN GAME COMPLETE!"); return
        self._give_single_weapon(killer,self.gun_game.weapon); killer.tell(f"^2ADVANCE:^7 {self.gun_game.weapon_name}"); self._add_replacement_bot(random.choice(BOT_ROSTER),self.skill,self.controller.token())

    def current_map_name(self):
        try: return str(self.game.map).lower()
        except Exception: return str(self.session.get("map","")).lower()

    def _choose_session_map(self, pool_key, salt=0):
        pool=list(self.map_pools.get(pool_key) or self.maps)
        if not pool: return None
        return str(random.Random(self.seed+int(salt)*7919+sum(ord(c) for c in str(pool_key))).choice(pool)).lower()

    def _start_arena_run(self):
        resume=bool(self.session.get("continue_run")); self.run=load_state(STATE_FILE) if resume else None
        if not self.run or self.run.complete: self.run=new_state(self.seed,self.difficulty,self.length); save_state(STATE_FILE,self.run)
        self.msg(f"^2ARENA RUN^7 seed ^3{self.run.seed}^7 round ^3{self.run.round}^7 lives ^3{self.run.lives}"); self._start_arena_round()

    def _start_arena_round(self):
        if not self.run or self.run.complete: return
        if self.run.waiting_for_pick: self._show_upgrade_choices(); return
        plan=round_plan(self.run); self.current_plan=plan; theme=plan.get("theme","normal"); target_map=self._choose_session_map(theme,self.run.round); current=self.current_map_name()
        if target_map and current and target_map != current:
            self.controller.request_map(target_map,{"kind":"arena","plan":plan}); self.clear_all_bots(); self.msg(f"^5NEXT ARENA:^7 {target_map} ^3({theme.upper()})"); minqlx.console_command(f"map {target_map} ffa"); return
        self._launch_arena_plan(plan)

    def _launch_arena_plan(self, plan):
        if not self.run or self.run.complete: return
        self.current_plan=plan; theme=plan.get("theme","normal")
        if plan.get("boss"): self.msg(f"^1BOSS ROUND {self.run.round}^7 — ^3{plan['bots'][0].upper()}")
        elif theme in ("rail","rocket","lg"): self.msg(f"^6ROUND {self.run.round}^7 — ^3{theme.upper()} TRIAL^7")
        else: self.msg(f"^6ROUND {self.run.round}^7 — {plan['count']} enemies")
        self._spawn_objective_bots(plan["bots"],plan["skill"]); human=self.primary_player()
        if human: self._apply_human_loadout(human)

    def _arena_objective_clear(self):
        if not self.run or self.run.complete: return
        if advance_round(self.run): save_state(STATE_FILE,self.run); self.controller.finish(); self.msg(f"^2ARENA RUN COMPLETE!^7 Rounds cleared: ^3{self.run.round}"); return
        roll_upgrade_choices(self.run); save_state(STATE_FILE,self.run); human=self.primary_player()
        if human: self._apply_human_loadout(human)
        self.msg("^2ROUND CLEARED.^7 Choose one upgrade:"); self._show_upgrade_choices()

    def _show_upgrade_choices(self, player=None):
        if not self.run or not self.run.choices: return
        target=player.tell if player else self.msg
        for index,uid in enumerate(self.run.choices,1):
            upgrade=UPGRADE_BY_ID[uid]; color=RARITY_COLOR.get(upgrade["rarity"],"^7"); target(f"^3!pick {index} ^7— {color}{upgrade['name']} ^7[{upgrade['rarity'].upper()}] — {upgrade['text']}")

    def cmd_pick(self, player, msg, channel):
        if self.mode != "arena_run" or not self.run: player.tell("^7Upgrade picks are only used in Arena Run."); return
        if len(msg)<2: self._show_upgrade_choices(player); return
        try: result=pick_upgrade(self.run,int(msg[1]))
        except Exception as exc: player.tell(f"^1{exc}"); return
        upgrade=result["upgrade"]; self.msg(f"^2PICKED:^7 {upgrade['name']} — {upgrade['text']}")
        for synergy in result["synergies"]: self.msg(f"^6SYNERGY UNLOCKED:^7 {synergy['name']} — {synergy['text']}")
        save_state(STATE_FILE,self.run); self._apply_human_loadout(player); self._start_arena_round()

    def cmd_upgrades(self, player, msg, channel):
        if self.mode != "arena_run" or not self.run: player.tell("^7This mode has no roguelite upgrade build."); return
        if not self.run.upgrades: player.tell("^7No upgrades yet."); return
        items=[f"{UPGRADE_BY_ID[uid]['name']} x{stacks}" for uid,stacks in self.run.upgrades.items() if uid in UPGRADE_BY_ID]; player.tell("^6BUILD:^7 "+", ".join(items))
        if self.run.synergies: player.tell("^6SYNERGIES:^7 "+", ".join(self.run.synergies))

    def handle_death(self, victim, killer, data):
        if not victim: return
        if is_bot(victim):
            owned=victim.id in self.controller.enemy_ids; self._kick_dead_bot(victim.id)
            if not owned: self._log(f"ignored death of unowned bot id={victim.id}"); return
            killer_human=bool(killer and not is_bot(killer))
            if self.mode == "gun_game":
                if killer_human: self._gun_game_kill(killer,victim.id)
                else: self.controller.remove_enemy(victim.id); self._add_replacement_bot(random.choice(BOT_ROSTER),self.skill,self.controller.token())
                return
            if killer_human:
                self.kills+=1
                if self.mode == "arena_run": self._arena_human_kill(killer)
            if self.controller.enemy_died(victim.id):
                if self.mode == "horde": self._horde_objective_clear()
                elif self.mode == "arena_run": self._arena_objective_clear()
            return
        if self.controller.phase in (Phase.COMPLETE,Phase.FAILED): return
        if self.mode == "arena_run" and self.run:
            if self.controller.phase == Phase.ACTIVE:
                self.run.lives-=1; save_state(STATE_FILE,self.run); self.msg(f"^1LIFE LOST.^7 {max(0,self.run.lives)} lives remaining.")
                if self.run.lives<=0: self.run.complete=True; save_state(STATE_FILE,self.run); self.controller.finish(); self.clear_all_bots(); self.msg(f"^1ARENA RUN OVER.^7 Reached round {self.run.round}.")
            return
        self.controller.finish(); self.clear_all_bots()
        if self.mode == "horde" and self.horde: self.horde.player_died(); self.msg(f"^1HORDE RUN OVER.^7 Reached wave {self.horde.wave}.")
        elif self.mode == "gun_game" and self.gun_game: self.msg(f"^1GUN GAME RUN OVER.^7 Reached {self.gun_game.weapon_name}.")

    def _arena_max_health(self):
        effects=upgrade_effects(self.run) if self.run else {}; hp=int(125+effects.get("max_health",0))
        if effects.get("health_cap"): hp=min(hp,int(effects["health_cap"]))
        return max(1,hp)

    def _arena_human_kill(self, killer):
        if not self.run: return
        effects=upgrade_effects(self.run); heal=int(effects.get("kill_heal",0))
        if heal:
            try: killer.health=min(self._arena_max_health(),int(killer.health)+heal)
            except Exception: pass
        if effects.get("quad_burst") and self.kills%5==0:
            try: killer.powerups(quad=5); killer.tell("^1QUAD BURST!")
            except Exception: pass

    def handle_damage(self, target, attacker, damage, dflags, means_of_death):
        if self.mode != "arena_run" or not self.run or not target or not attacker: return
        if getattr(attacker,"id",None)==getattr(target,"id",None): return
        if is_bot(attacker) and not is_bot(target):
            multiplier=max(0.0,float((self.current_plan or {}).get("damage_mult",1.0))-1.0); bonus=max(0,int(round(damage*multiplier)))
            if bonus and getattr(target,"is_alive",False):
                try: target.health=max(1,int(target.health)-bonus)
                except Exception: pass
            return
        if is_bot(attacker): return
        effects=upgrade_effects(self.run); multiplier=float(effects.get("damage_mult",0)); mod=int(means_of_death)
        rocket_mods={minqlx.MOD_ROCKET,minqlx.MOD_ROCKET_SPLASH}; lightning_mods={minqlx.MOD_LIGHTNING,minqlx.MOD_LIGHTNING_DISCHARGE}; rail_mods={minqlx.MOD_RAILGUN,minqlx.MOD_RAILGUN_HEADSHOT}; plasma_mods={minqlx.MOD_PLASMA,minqlx.MOD_PLASMA_SPLASH}
        if mod in rocket_mods: multiplier+=effects.get("rocket_mult",0)
        if mod in lightning_mods:
            multiplier+=effects.get("lg_mult",0); now=time.time(); last=self.last_damage_time.get(attacker.id,0); self.lg_streak[attacker.id]=self.lg_streak.get(attacker.id,0)+1 if now-last<0.25 else 1; self.last_damage_time[attacker.id]=now
            if effects.get("lg_overcharge"): multiplier+=min(0.40,self.lg_streak[attacker.id]*0.015)
            vamp=effects.get("lg_vampire",0)
            if vamp:
                try: attacker.health=min(self._arena_max_health(),int(attacker.health)+max(1,int(damage*vamp)))
                except Exception: pass
        if mod in rail_mods:
            multiplier+=effects.get("rail_mult",0); self.rail_hits[attacker.id]=self.rail_hits.get(attacker.id,0)+1
            if effects.get("rail_combo") and self.rail_hits[attacker.id]%3==0: multiplier+=0.80 if effects.get("rail_combo_bonus") else 0.50; attacker.tell("^5PERFECT SHOT!")
        if mod in plasma_mods: multiplier+=effects.get("plasma_mult",0)
        vamp=effects.get("vampire",0)
        if vamp:
            try: attacker.health=min(self._arena_max_health(),int(attacker.health)+max(1,int(damage*vamp)))
            except Exception: pass
        bonus=max(0,int(round(damage*multiplier)))
        if bonus and getattr(target,"is_alive",False):
            try: target.health=max(1,int(target.health)-bonus)
            except Exception: pass

    def _apply_human_loadout(self, player):
        if self.mode == "gun_game" and self.gun_game: self._give_single_weapon(player,self.gun_game.weapon); return
        if self.mode == "arena_run" and self.run:
            try:
                effects=upgrade_effects(self.run); player.health=self._arena_max_health(); player.armor=max(0,int(50+effects.get("max_armor",0))); player.weapons(g=True,mg=True,sg=True,gl=True,rl=True,lg=True,rg=True,pg=True); player.ammo(mg=200,sg=60,gl=60,rl=60,lg=200,rg=50,pg=200); theme=(self.current_plan or {}).get("theme"); trial={"rocket":5,"lg":6,"rail":7}.get(theme)
                if trial: self._give_single_weapon(player,trial)
                else: player.weapon(5)
                if effects.get("haste"): player.powerups(haste=3600)
            except Exception as exc: self._log(f"arena loadout failed: {exc}")
            return
        try: player.health=150; player.armor=50; player.weapons(g=True,mg=True,sg=True,gl=True,rl=True,lg=True,rg=True,pg=True); player.ammo(mg=200,sg=50,gl=50,rl=50,lg=150,rg=30,pg=150); player.weapon(5)
        except Exception as exc: self._log(f"human loadout failed: {exc}")

    def _apply_arena_bot(self, player):
        plan=self.current_plan or {}
        if not plan: return
        try:
            player.health=int(plan.get("health",100)); player.armor=int(plan.get("armor",0)); trial={"rocket":5,"lg":6,"rail":7}.get(plan.get("theme"))
            if trial: self._give_single_weapon(player,trial)
            elif plan.get("boss"): self._give_single_weapon(player,5)
        except Exception as exc: self._log(f"arena bot loadout failed: {exc}")

    def _give_single_weapon(self, player, weapon):
        keys={1:"g",2:"mg",3:"sg",4:"gl",5:"rl",6:"lg",7:"rg",8:"pg"}; key=keys.get(int(weapon))
        try:
            kwargs={"g":True}
            if key: kwargs[key]=True
            player.weapons(reset=True,**kwargs)
            if key and key != "g": player.ammo(reset=True,**{key:200})
            else: player.ammo(reset=True)
            player.weapon(int(weapon))
        except Exception as exc: self._log(f"weapon kit failed: {exc}")

    def cmd_run(self, player, msg, channel):
        if self.mode == "arena_run" and self.run: player.tell(f"^6Arena Run:^7 round {self.run.round} lives {self.run.lives} phase {self.controller.phase.value} enemies {len(self.controller.enemy_ids)}")
        elif self.mode == "horde" and self.horde: player.tell(f"^6Horde:^7 wave {self.horde.wave} phase {self.controller.phase.value} enemies {len(self.controller.enemy_ids)}")
        elif self.mode == "gun_game" and self.gun_game: player.tell(f"^6Gun Game:^7 {self.gun_game.weapon_name} phase {self.controller.phase.value} enemies {len(self.controller.enemy_ids)}")

    def cmd_help(self, player, msg, channel):
        player.tell("^6Solo Engine v5 alpha:^7 !run shows controller state.")
        if self.mode == "arena_run": player.tell("^7Arena Run: use ^3!pick 1/2/3 ^7and ^3!upgrades^7.")
