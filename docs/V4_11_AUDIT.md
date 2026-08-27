# v4.11 Solo Engine architecture audit

This document records the architectural findings from the v4.11 prototype. It is intentionally preserved so future changes do not reintroduce already-understood failures.

## Executive conclusion

The launcher/UI side is worth preserving. The scripted Solo lifecycle is not.

v4.11 allowed two independent state machines to control the same match:

1. Quake Live FFA controlled warmup, match state, bot behavior, respawn and normal game completion.
2. `solo_arcade.py` independently controlled wave starts, bot replacement, objectives, map changes, lives and run completion.

When those systems disagree, symptoms include waiting-for-player states, immediate forfeits, phantom bots, waves completing early, delayed callbacks firing into a later round, and mode completion without the underlying game actually becoming terminal.

## Confirmed architectural problems

### 1. Native single-player support was not used

shinqlx exposes `allow_single_player(True)`. Its implementation explicitly marks the current level as a training/single-player map so a one-player game can continue without forfeiting.

v4.11 instead accumulated workarounds around `g_allowForfeit`, `g_forfeit`, `g_teamForcePresent` and `bot_minplayers`.

**v5 rule:** call `minqlx.allow_single_player(True)` and keep `bot_minplayers 0`. The Solo plugin, not Quake Live's automatic bot manager, owns all scripted-mode bots.

### 2. ZMQ-dependent hooks were not guaranteed to be available

`death`, `game_start`, `game_end`, `round_end` and related minqlx/shinqlx dispatchers require `zmq_stats_enable` to be enabled when hooks are registered.

v4.11 registered `death` and `game_start` but did not guarantee `zmq_stats_enable 1` before plugin loading.

**v5 rule:** QLDS startup enables ZMQ stats before shinqlx loads plugins. Plugin startup also validates the setting and fails loudly if the required event layer is unavailable.

### 3. `set_cvar_once()` was used for existing Quake cvars

`set_cvar_once()` sets a cvar only when it does not already exist. Cvars such as `fraglimit`, `timelimit`, `bot_enable` and `sv_hostname` already exist, so calls such as `set_cvar_once('fraglimit','0')` are not reliable configuration.

**v5 rule:** use `set_cvar()` or explicit console commands for existing engine cvars.

### 4. `bot_minplayers` bootstrap created a race

v4.11 started with `bot_minplayers 2`, then the plugin spawned the real wave, then attempted to retire bootstrap bots, then reset `bot_minplayers 0`.

The engine could add an automatic bot after the plugin recorded the set of "old" bots. That bot then looked like a mode-owned bot and could contaminate wave-clear logic.

**v5 rule:** no automatic bots. Single-player allowance keeps the match alive while the plugin owns every bot slot.

### 5. Wave ownership was inferred from all bots on the server

Several mode transitions used conditions like `not self.bot_players()` instead of a controller-owned set of enemy IDs for the current objective.

**v5 rule:** each objective owns explicit enemy IDs. A bot death only advances a wave if the victim belongs to the current objective generation.

### 6. Delayed callbacks had no universal generation guard

A delayed respawn, replenish or next-wave call can execute after the player dies, changes map, restarts the mode, or begins another wave.

Wipeout had a local generation token, but the pattern was not universal.

**v5 rule:** one controller generation counter. Every scheduled callback captures it and becomes a no-op if the controller has moved on.

### 7. Map transitions relied on fixed sleep durations

Arena Run and Gauntlet resumed approximately 1.4 seconds after `map`, assuming the client was loaded.

shinqlx already exposes `map` and `player_loaded`; `player_loaded` also fires after map changes.

**v5 rule:** map transition sets a pending stage, changes the map, then resumes only after the intended human is active on the intended map.

### 8. Means-of-death handling used string heuristics

v4.11 converted `means_of_death` to a string and looked for substrings such as `"6"` and `"7"` to identify rockets. Values such as 16/17/26/27 can therefore be misclassified.

shinqlx exports `MOD_ROCKET`, `MOD_ROCKET_SPLASH`, `MOD_LIGHTNING`, `MOD_RAILGUN`, `MOD_RAILGUN_HEADSHOT`, `MOD_PLASMA`, `MOD_PLASMA_SPLASH`, etc.

**v5 rule:** use exported constants and explicit sets.

### 9. Declared Arena Run effects were not all implemented

Examples in v4.11:

- `regen` was declared but had no complete regeneration consumer.
- `rocket_chain` was declared but no chain explosion was implemented.
- `rocket_jump` was declared but no matching behavior consumed it.
- `bottomless` was declared but no clear/spawn ammo refill behavior fully consumed it.

**v5 rule:** an upgrade cannot appear in the roll pool until its effect has a tested runtime consumer.

### 10. Several modes had incomplete terminal states

Gun Game, Boss Rush, Gauntlet, Movement Hunter and other modes could announce completion while delayed replenishment/respawn code remained active.

**v5 rule:** completion is a controller phase (`COMPLETE`), not merely a message. All callbacks check phase/generation before mutating gameplay.

### 11. Ground detection for movement abilities was heuristic

The v4.11 dash code inferred grounding mostly from vertical velocity. The apex of a jump can resemble a ground state, and slopes/platforms can produce false refreshes.

**v5 rule:** keep side-thruster movement experimental until grounding is made robust. Normal jump remains untouched.

## Components to preserve

- launcher UI concept and Linux-first packaging;
- Steam/native/Flatpak detection;
- installed map scanner and `.arena` metadata parsing;
- Workshop scanning;
- curated map-rating system;
- native Arcade factories;
- `solo_core.py` deterministic Arena Run state, upgrade rolls and persistence model;
- local QLDS + private venv + shinqlx installation strategy;
- diagnostics/logging work from v4.4 onward.

## v5 state model

The Solo controller should have exactly one lifecycle:

```text
BOOTING
  -> WAITING_FOR_PLAYER
  -> PREPARING
  -> ACTIVE
  -> BETWEEN_ROUNDS
  -> PREPARING ...
  -> COMPLETE

Any state may transition to FAILED.
```

Map changes use a pending transition record and `player_loaded`, not a timer.

## Integration order

1. Horde: player joins -> wave 1 -> enemies die -> wave 2 -> player death -> terminal state.
2. Gun Game: kill -> weapon progression -> final weapon -> terminal state.
3. Arena Run: maps, waves, upgrades, boss rounds, player lives, persistence and movement.
4. Remaining scripted modes after the common lifecycle has proven stable.

## Testing requirement

Source-string tests are not sufficient for gameplay behavior.

v5 should include a fake minqlx adapter that can simulate:

- player loading/spawning;
- bot spawning;
- bot/human deaths;
- damage events;
- map changes;
- delayed callbacks;
- stale-generation callbacks;
- run completion.

The pure rules layer remains independent of minqlx and receives normal unit tests.
