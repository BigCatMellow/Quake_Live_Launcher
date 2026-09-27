#!/usr/bin/env python3
"""Director capability probe: answers, on the real QLDS, what the Director may rely on.

Loaded *instead of* solo_directed by ``run_director_probe.sh``; never part of
normal Solo play. It answers four questions that the shinqlx/Quake 3 source
could not settle for Quake Live's closed game and bot code:

1. Custom bot files - does QL load ``scripts/*.bot`` definitions and custom
   ``botfiles/bots/*_c.c`` character files? (botlist output, bot userinfo and
   the bot loader's own "loaded skill N from <file>" console lines.)
2. Fractional skill - does ``addbot <name> 2.5`` keep 2.5 (Quake 3 interpolates
   character traits between skill levels) or round it?
3. Item IDs - ``spawn_item`` only takes a numeric ID and nothing maps names to
   IDs; each ID is spawned on a bot and classified from what the bot gained.
4. Item lures - do bots go for a dropped Mega Health? Measured by closest
   approach against a no-item control window, plus a health jump on pickup.

Results go to ``solo_runtime/director_probe.json``. ``replace_items`` is never
called: shinqlx 0.7.0 writes its item table into configstring ``item_id``
instead of CS_ITEMS, which can clobber unrelated server state.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path

import minqlx

try:
    from .spawn_director import SpawnPointBook, as_vec, distance
except ImportError:
    from spawn_director import SpawnPointBook, as_vec, distance

RUNTIME_DIR = Path.home() / ".local/share/quake-live-launcher/solo_runtime"
SESSION_FILE = Path.home() / ".config/quake-live-launcher/solo_session.json"
PLUGIN_READY_FILE = RUNTIME_DIR / "plugin_ready.json"
RESULT_FILE = RUNTIME_DIR / "director_probe.json"
PROBE_VERSION = 1

PROBE_BOT_A = "qllprobea"       # custom .bot entry pointing at a stock character
PROBE_BOT_B = "qllprobeb"       # custom .bot entry pointing at our own character
PROBE_CHAR_FILE = "bots/qll_probe_c.c"
FRACTIONAL_BOT, FRACTIONAL_SKILL = "anarki", "2.5"
PICKER_BOT, PICKER_SKILL = "sarge", "3"
STOCK_BOTS = ("sarge", "keel", "anarki", "xaero", "visor", "slash")
MAX_ITEM_ID = 96
ITEM_SETTLE = 0.45
CONTROL_WINDOW = 15.0
LURE_WINDOW = 30.0
SAMPLE_EVERY = 0.5
LURE_HEALTH = 60
PICKUP_HEALTH_JUMP = 80
LURE_MIN_CLEARANCE = 400.0

WEAPON_KEYS = ("g", "mg", "sg", "gl", "rl", "lg", "rg", "pg", "bfg", "gh", "ng", "pl", "cg", "hmg")
WEAPON_CLASS = {
    "g": "weapon_gauntlet", "mg": "weapon_machinegun", "sg": "weapon_shotgun", "gl": "weapon_grenadelauncher",
    "rl": "weapon_rocketlauncher", "lg": "weapon_lightning", "rg": "weapon_railgun", "pg": "weapon_plasmagun",
    "bfg": "weapon_bfg", "gh": "weapon_grapplinghook", "ng": "weapon_nailgun", "pl": "weapon_prox_launcher",
    "cg": "weapon_chaingun", "hmg": "weapon_hmg",
}
AMMO_CLASS = {
    "mg": "ammo_bullets", "sg": "ammo_shells", "gl": "ammo_grenades", "rl": "ammo_rockets", "lg": "ammo_lightning",
    "rg": "ammo_slugs", "pg": "ammo_cells", "bfg": "ammo_bfg", "ng": "ammo_nails", "pl": "ammo_mines",
    "cg": "ammo_belt", "hmg": "ammo_hmg",
}
POWERUP_KEYS = ("quad", "battlesuit", "haste", "invisibility", "regeneration", "invulnerability")
LOADER_LINE = re.compile(r"(loaded|couldn't|could not|character|skill|bot)", re.I)


# ---------------------------------------------------------------- pure logic
def parse_botlist(lines):
    """Parse Quake 3 style ``botlist`` rows: name, model, aifile, funname."""
    bots = {}
    for raw in lines:
        line = re.sub(r"\^[0-9]", "", str(raw)).strip()
        if not line or line.lower().startswith("name"):
            continue
        parts = line.split()
        if len(parts) >= 3 and ("/" in parts[2] or parts[2].endswith(".c")):
            bots[parts[0].lower()] = {"model": parts[1], "aifile": parts[2]}
    return bots


def snapshot(player):
    """Capture the parts of a player's state that item pickups change."""
    try:
        state = player.state
    except Exception:
        state = None
    if state is None:
        return None

    def fields(obj, keys):
        return {key: getattr(obj, key, 0) for key in keys} if obj is not None else {}

    return {
        "alive": bool(getattr(state, "is_alive", True)),
        "health": int(getattr(state, "health", 0) or 0),
        "armor": int(getattr(state, "armor", 0) or 0),
        "weapons": {k: bool(v) for k, v in fields(getattr(state, "weapons", None), WEAPON_KEYS).items()},
        "ammo": {k: int(v or 0) for k, v in fields(getattr(state, "ammo", None), WEAPON_KEYS).items()},
        "powerups": {k: bool(v) for k, v in fields(getattr(state, "powerups", None), POWERUP_KEYS).items()},
        "holdable": getattr(state, "holdable", None),
    }


def classify_pickup(before, after):
    """Describe what a pickup changed and guess the item's classname."""
    if not before or not after:
        return {"picked": False, "guess": None, "reason": "no state"}
    delta = {
        "health": after["health"] - before["health"],
        "armor": after["armor"] - before["armor"],
        "weapons": sorted(k for k, v in after["weapons"].items() if v and not before["weapons"].get(k)),
        "ammo": {k: v - before["ammo"].get(k, 0) for k, v in after["ammo"].items() if v - before["ammo"].get(k, 0) > 0},
        "powerups": sorted(k for k, v in after["powerups"].items() if v and not before["powerups"].get(k)),
        "holdable": after["holdable"] if after["holdable"] not in (None, 0, before["holdable"]) else None,
    }
    guess = None
    if delta["weapons"]:
        guess = WEAPON_CLASS.get(delta["weapons"][0], "weapon_" + delta["weapons"][0])
    elif delta["powerups"]:
        guess = "item_" + delta["powerups"][0]
    elif delta["holdable"] is not None:
        guess = f"holdable_{delta['holdable']}"
    elif delta["ammo"]:
        key = max(delta["ammo"], key=lambda k: delta["ammo"][k])
        guess = AMMO_CLASS.get(key, "ammo_" + key)
    elif delta["health"] > 0:
        h = delta["health"]
        guess = ("item_health_mega" if h >= 75 else "item_health_large" if h >= 40
                 else "item_health" if h >= 20 else "item_health_small")
    elif delta["armor"] > 0:
        a = delta["armor"]
        guess = ("item_armor_body" if a >= 75 else "item_armor_combat" if a >= 40
                 else "item_armor_jacket" if a >= 20 else "item_armor_shard")
    picked = guess is not None
    return {"picked": picked, "guess": guess, "delta": delta}


def lure_verdict(control_min, lure_min, picked_by):
    if picked_by:
        return "picked_up"
    if control_min is None or lure_min is None:
        return "not_measured"
    if lure_min < control_min * 0.6 and lure_min < 600:
        return "approached"
    return "ignored"


def verdicts(results):
    out = {}
    bl = results.get("botlist") or {}
    bots = results.get("bots") or {}
    loader_lines = [str(line).lower() for line in (results.get("loader_lines") or [])]
    a_present = bool((bots.get(PROBE_BOT_A) or {}).get("present"))
    b_present = bool((bots.get(PROBE_BOT_B) or {}).get("present"))
    if a_present or b_present or bl.get("has_probe_a") or bl.get("has_probe_b"):
        out["custom_bot_files"] = "yes"
    elif results.get("botlist_rows", 0) > 0:
        out["custom_bot_files"] = "no"
    else:
        out["custom_bot_files"] = "unknown"
    char_file = PROBE_CHAR_FILE.lower()
    b_char = str((bots.get(PROBE_BOT_B) or {}).get("characterfile") or "").lower()
    loaded = any("loaded" in line and char_file in line and "default" not in line for line in loader_lines)
    fell_back = any(char_file in line and ("couldn't" in line or "default" in line) for line in loader_lines)
    if loaded and not fell_back:
        out["custom_character_file"] = "yes"
    elif fell_back:
        out["custom_character_file"] = "fell_back_to_default"
    elif b_present and b_char == char_file:
        out["custom_character_file"] = "referenced_unconfirmed"
    else:
        out["custom_character_file"] = "unknown" if not b_present else "no"
    skill = (bots.get(FRACTIONAL_BOT) or {}).get("skill")
    try:
        value = float(skill)
        out["fractional_skill"] = "yes" if abs(value - float(FRACTIONAL_SKILL)) < 0.05 else f"no (stored {value:g})"
    except (TypeError, ValueError):
        out["fractional_skill"] = "unknown"
    ids = results.get("item_ids") or {}
    out["item_ids"] = f"mapped {len(ids)} items" if ids else "unknown"
    out["mega_health_id"] = ids.get("item_health_mega")
    out["bot_item_lure"] = (results.get("lure") or {}).get("verdict", "not_measured")
    return out


# ------------------------------------------------------------------- plugin
class solo_probe(minqlx.Plugin):
    def __init__(self):
        try:
            self.session = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        except Exception:
            self.session = {}
        self.mode = str(self.session.get("mode", "horde"))
        self.console = []
        self.results = {"version": PROBE_VERSION, "started_at": time.time(), "steps": []}
        self.spawn_book = SpawnPointBook(RUNTIME_DIR / "spawn_points.json")
        self.item_id = 1
        self.item_rows = []
        self.lure = {}
        self._last_health = {}
        self._samples_left = 0
        self._mark = 0
        self.done = False
        self.add_hook("console_print", self.handle_console_print)
        self.add_hook("player_loaded", self.handle_player_loaded)
        self.add_hook("player_spawn", self.handle_player_spawn)
        self._write_ready()
        try:
            RESULT_FILE.unlink(missing_ok=True)
        except Exception:
            pass
        self._log("probe loaded; starting in 3s")
        self._later(3.0, self.step_start)

    # ---- plumbing
    def _log(self, text):
        try:
            minqlx.console_print(f"[solo_probe] {text}")
        except Exception:
            pass

    def _later(self, seconds, func, *args):
        @minqlx.delay(seconds)
        def _run():
            try:
                func(*args)
            except Exception as exc:
                self.results["steps"].append({"step": getattr(func, "__name__", "?"), "error": str(exc)})
                self._log(f"step {getattr(func, '__name__', '?')} failed: {exc}")
                self.finish()
        _run()

    def _write_ready(self):
        try:
            RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
            payload = {"ready": True, "mode": self.mode, "version": "probe", "pid": os.getpid(), "time": time.time(), "probe": True}
            temp = PLUGIN_READY_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            temp.replace(PLUGIN_READY_FILE)
        except Exception as exc:
            self._log(f"could not write readiness: {exc}")

    def handle_console_print(self, text):
        if text and not str(text).startswith("[solo_probe]"):
            for line in str(text).splitlines():
                self.console.append(line)
            if len(self.console) > 4000:
                self.console = self.console[-3000:]

    def handle_player_loaded(self, player):
        if not self._is_bot(player):
            try:
                player.put("spectator")
                player.tell("^6Director probe running^7 — watching only; results in solo_runtime/director_probe.json")
            except Exception:
                pass

    def handle_player_spawn(self, player):
        try:
            self.spawn_book.learn(str(self.game.map), player.position())
        except Exception:
            pass

    @staticmethod
    def _is_bot(player):
        try:
            return int(player.steam_id) > 90_000_000_000_000_000
        except Exception:
            return False

    def _bot(self, name):
        for player in self.players():
            if self._is_bot(player) and re.sub(r"\^[0-9]", "", str(player.name)).strip().lower() == name:
                return player
        return None

    def _bots(self):
        return [p for p in self.players() if self._is_bot(p)]

    # ---- 1. bot files + 2. fractional skill
    def step_start(self):
        self.results["steps"].append({"step": "start", "frames_running": True, "map": str(self.game.map)})
        self._mark = len(self.console)
        minqlx.console_command("botlist")
        self._later(1.0, self.step_botlist)

    def step_botlist(self):
        raw = self.console[self._mark:]
        rows = parse_botlist(raw)
        self.results["botlist_raw"] = raw[:80]
        self.results["botlist_rows"] = len(rows)
        self.results["botlist"] = {
            "has_probe_a": PROBE_BOT_A in rows,
            "has_probe_b": PROBE_BOT_B in rows,
            "stock_aifiles": {name: rows[name]["aifile"] for name in STOCK_BOTS if name in rows},
            "probe_rows": {name: rows[name] for name in (PROBE_BOT_A, PROBE_BOT_B) if name in rows},
        }
        self._mark = len(self.console)
        for command in (
            f"addbot {PROBE_BOT_A} 4 blue", f"addbot {PROBE_BOT_B} 4 blue",
            f"addbot {FRACTIONAL_BOT} {FRACTIONAL_SKILL} blue", f"addbot {PICKER_BOT} {PICKER_SKILL} blue",
        ):
            minqlx.console_command(command)
        self._later(4.0, self.step_bots)

    def step_bots(self):
        bots = {}
        for name in (PROBE_BOT_A, PROBE_BOT_B, FRACTIONAL_BOT, PICKER_BOT):
            player = self._bot(name)
            row = {"present": player is not None}
            if player is not None:
                cvars = getattr(player, "cvars", {}) or {}
                row["skill"] = cvars.get("skill")
                row["characterfile"] = cvars.get("characterfile")
            bots[name] = row
        self.results["bots"] = bots
        self.results["loader_lines"] = [line for line in self.console[self._mark:] if LOADER_LINE.search(line)][:120]
        self._later(0.5, self.step_item)

    # ---- 3. item IDs
    def _prepare_picker(self, picker):
        picker.health = 50
        picker.armor = 0
        picker.weapons(reset=True)
        picker.ammo(reset=True)
        picker.powerups(reset=True)
        try:
            picker.holdable = None
        except Exception:
            pass

    def step_item(self, attempts=0):
        picker = self._bot(PICKER_BOT) or next(iter(self._bots()), None)
        if picker is None or self.item_id > MAX_ITEM_ID:
            return self.step_items_done()
        before = snapshot(picker)
        if not before or not before["alive"]:
            if attempts > 10:
                return self.step_items_done()
            return self._later(1.0, self.step_item, attempts + 1)
        self._prepare_picker(picker)
        before = snapshot(picker)
        pos = as_vec(picker.position())
        try:
            minqlx.spawn_item(self.item_id, int(pos[0]), int(pos[1]), int(pos[2]))
        except ValueError:
            self.results["item_count"] = self.item_id - 1
            return self.step_items_done()
        self._later(ITEM_SETTLE, self.step_item_check, picker.id, before)

    def step_item_check(self, picker_id, before):
        picker = next((p for p in self._bots() if p.id == picker_id), None)
        after = snapshot(picker) if picker is not None else None
        row = {"id": self.item_id, **classify_pickup(before, after)}
        self.item_rows.append(row)
        try:
            minqlx.remove_dropped_items()
        except Exception:
            pass
        self.item_id += 1
        self._later(0.1, self.step_item)

    def step_items_done(self):
        ids = {}
        for row in self.item_rows:
            if row.get("guess") and row["guess"] not in ids:
                ids[row["guess"]] = row["id"]
        self.results["items"] = self.item_rows
        self.results["item_ids"] = ids
        self._later(0.5, self.step_lure_begin)

    # ---- 4. item lure
    def step_lure_begin(self):
        mega = (self.results.get("item_ids") or {}).get("item_health_mega")
        bots = self._bots()
        points = self.spawn_book.points(str(self.game.map))
        if not mega or not bots or not points:
            self.lure = {"verdict": "not_measured", "reason": "no mega id" if not mega else "no bots or spawn points"}
            return self.finish()
        positions = [pos for pos in (as_vec(b.position()) for b in bots) if pos]

        def clearance(point):
            return min(distance(point.pos, pos) for pos in positions) if positions else 0.0

        # The lure must start well away from every bot; otherwise a bot that
        # happens to stand on it "picks it up" without choosing to go there.
        target = max(points, key=clearance)
        if clearance(target) < LURE_MIN_CLEARANCE:
            self.lure = {"verdict": "not_measured", "reason": "no learned point far enough from the bots"}
            return self.finish()
        self.lure = {"point": list(target.pos), "item_id": mega, "control_min": None, "lure_min": None, "picked_by": None}
        for bot in bots:
            bot.health = LURE_HEALTH
        self._samples_left = int(CONTROL_WINDOW / SAMPLE_EVERY)
        self._later(SAMPLE_EVERY, self.step_sample, "control_min")

    def _closest(self):
        target = tuple(self.lure["point"])
        best = None
        for bot in self._bots():
            pos = as_vec(bot.position())
            if pos is not None:
                d = distance(pos, target)
                best = d if best is None else min(best, d)
        return best

    def step_sample(self, key):
        closest = self._closest()
        if closest is not None:
            self.lure[key] = closest if self.lure[key] is None else min(self.lure[key], closest)
        if key == "lure_min":
            for bot in self._bots():
                last = self._last_health.get(bot.id)
                health = int(getattr(bot, "health", 0) or 0)
                if last is not None and health - last >= PICKUP_HEALTH_JUMP and not self.lure["picked_by"]:
                    self.lure["picked_by"] = re.sub(r"\^[0-9]", "", str(bot.name))
                    self.lure["picked_after"] = round(LURE_WINDOW - self._samples_left * SAMPLE_EVERY, 1)
                self._last_health[bot.id] = health
        self._samples_left -= 1
        if self._samples_left > 0 and not self.lure.get("picked_by"):
            return self._later(SAMPLE_EVERY, self.step_sample, key)
        if key == "control_min":
            point = self.lure["point"]
            for bot in self._bots():
                bot.health = LURE_HEALTH
            self._last_health = {bot.id: LURE_HEALTH for bot in self._bots()}
            minqlx.spawn_item(int(self.lure["item_id"]), int(point[0]), int(point[1]), int(point[2]))
            self._samples_left = int(LURE_WINDOW / SAMPLE_EVERY)
            return self._later(SAMPLE_EVERY, self.step_sample, "lure_min")
        self.lure["verdict"] = lure_verdict(self.lure["control_min"], self.lure["lure_min"], self.lure["picked_by"])
        self.finish()

    # ---- results
    def finish(self):
        if self.done:
            return
        self.done = True
        try:
            minqlx.remove_dropped_items()
        except Exception:
            pass
        self.results["lure"] = self.lure
        self.results["verdicts"] = verdicts(self.results)
        self.results["finished_at"] = time.time()
        self.results["done"] = True
        try:
            self.spawn_book.save(force=True)
        except Exception:
            pass
        try:
            temp = RESULT_FILE.with_suffix(".tmp")
            temp.write_text(json.dumps(self.results, indent=2, default=str) + "\n", encoding="utf-8")
            temp.replace(RESULT_FILE)
        except Exception as exc:
            self._log(f"could not write results: {exc}")
        for key, value in self.results["verdicts"].items():
            self._log(f"RESULT {key}: {value}")
        try:
            self.msg("^6Director probe finished.^7 Results written; you can close Quake.")
        except Exception:
            pass
        for bot in self._bots():
            try:
                bot.kick("probe finished")
            except Exception:
                pass
