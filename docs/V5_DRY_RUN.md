# v5-alpha dry-run execution audit

This document walks the current code as if Quake Live were executing it. It is deliberately stricter than the unit tests: a green CI run is not treated as proof that QLDS and the plugin will integrate correctly.

## Verdict before fixes

**Current v5-alpha is not ready to call runnable.**

The pure state code is promising, but the real adapter has deterministic integration problems that can be found without launching Quake:

1. plugin sibling imports do not match minqlx's package-loading layout;
2. Horde/Gun Game/Arena Run currently spawn enemies into FFA, so enemies may fight one another;
3. objective activation counts currently-alive bot IDs instead of fulfilled spawn events, so an early kill while a staggered wave is still spawning can leave the controller in PREPARING forever;
4. the shell health check proves only that qzeroded is alive/listening, not that `solo_arcade` loaded;
5. mode startup begins from `player_loaded`, which can be earlier than a guaranteed playable human spawn;
6. bot disconnect/retirement and delayed respawn edge cases are not modeled by the current unit tests;
7. Arena Run map changes have no timeout/failure path if the requested map cannot load;
8. Workshop/custom-map syncing from v4 is not yet present in this branch;
9. the `Bottomless` Arena upgrade currently has no meaningful advantage because the normal loadout code already refills ammo on spawn/clear;
10. the damage hook should reject non-Player entity IDs explicitly before accessing Player fields.

## Startup trace

### 1. `qlds/start_solo.sh`

Expected sequence:

```text
read solo_session.json
-> locate qzeroded.x64
-> locate venv Python
-> locate shinqlx shared object
-> copy plugin files into qlds/minqlx-plugins
-> set LD_PRELOAD to shinqlx
-> set LD_LIBRARY_PATH to qlds/linux64
-> start qzeroded on 127.0.0.1:27960
-> set zmq_stats_enable=1 before plugin initialization
-> load solo_arcade
-> load selected map
```

The QLDS/shinqlx launch shape is reasonable and follows shinqlx's documented preload model.

**Problem:** the final health check checks process lifetime and UDP 27960 only. A plugin import/initialization failure can therefore still be reported as a healthy server.

Required change: plugin writes a runtime-ready marker only after its constructor completes; startup waits for both UDP and that marker.

## Plugin import trace

minqlx loads a configured plugin as a module inside the configured plugin-directory package and adds the *parent* of `qlx_pluginsPath` to `sys.path`.

Current adapter imports:

```python
from modes.gun_game import GunGameState
from modes.horde import HordeState
from solo_controller import Phase, SoloController
from solo_core import ...
```

Those paths assume the plugin directory itself is on `sys.path`.

Required form:

```python
from .modes.gun_game import GunGameState
from .modes.horde import HordeState
from .solo_controller import Phase, SoloController
from .solo_core import ...
```

This is a deterministic startup blocker.

## Plugin initialization trace after import fix

```text
read session
-> validate supported mode
-> verify allow_single_player exists
-> verify zmq_stats_enable == 1
-> allow_single_player(True)
-> set existing QL cvars with set_cvar()
-> register player/death/damage/map hooks
-> controller -> WAITING_FOR_PLAYER
```

This sequence looks correct. ZMQ is enabled on the qzeroded command line before plugin construction, so ZMQ-dependent hooks can be registered; minqlx starts its stats listener after preset plugins are loaded.

Using `set_cvar()` rather than `set_cvar_once()` for existing Quake cvars is also correct.

## Human connection trace

Current:

```text
player_loaded(human)
-> controller.player_loaded()
-> PREPARING
-> _start_selected_mode()
-> bots may begin spawning immediately
```

`player_loaded` means the client finished loading, but it is not as strong a contract as 'the human is now alive in the combat team'.

Safer sequence:

```text
player_loaded(human)
-> remember player ID
-> force human to RED
-> wait
player_spawn(human on RED)
-> start selected mode exactly once
```

This prevents a wave from starting while the human is still a spectator/transitioning.

## Combat sandbox

Current command line and Arena transitions use `ffa` and bots are added to `free`.

That is wrong for cooperative-enemy waves because FFA bots are mutually hostile.

Desired sandbox:

```text
TDM
human -> RED
all scripted bots -> BLUE
g_friendlyFire 0
bot_minplayers 0
g_teamForceBalance 0
fraglimit/timelimit/scorelimit/roundlimit 0
```

The plugin, not the base TDM score, remains responsible for completion. `allow_single_player(True)` remains enabled.

## Horde trace

Pure Horde planning is sound:

```text
wave 1 -> 2 enemies, skill 2
clear -> wave 2
...
wave 5 -> elite Keel added
```

Runtime intended flow:

```text
human spawn
-> plan wave
-> begin objective(expected=N)
-> stagger N addbot commands
-> bot spawn events register owned bot IDs
-> objective ACTIVE
-> deaths remove exact owned IDs
-> zero living owned IDs -> BETWEEN_ROUNDS
-> one-second guarded callback
-> next wave
```

### Spawn race

Current controller switches PREPARING -> ACTIVE when:

```python
len(enemy_ids) >= expected_enemies
```

But `enemy_ids` means living bots, not total fulfilled spawns.

Example:

```text
expected = 6
bot A spawns -> alive=1
bot B spawns -> alive=2
player kills A -> alive=1
bot C -> alive=2
bot D -> alive=3
bot E -> alive=4
bot F -> alive=5
```

All six intended spawns happened, but alive count never reached six. The controller remains PREPARING and the wave cannot complete.

Required controller state:

```text
expected_spawns = 6
fulfilled_spawns = 6
living_enemy_ids = {B,C,D,E,F}
```

Activation is based on fulfilled spawns; clearing is based on living IDs.

## Gun Game trace

Pure weapon progression is sound:

```text
MG -> SG -> GL -> PG -> LG -> RL -> RG -> Gauntlet -> COMPLETE
```

Once the controller is ACTIVE, the replacement model is reasonable:

```text
human kills owned bot
-> remove victim ID
-> advance weapon
-> give new weapon
-> schedule replacement bot under same generation
```

The same PREPARING spawn race can break Gun Game if an early bot dies before all five initial bots have registered. Current replacement scheduling also requires ACTIVE, so that early kill can permanently reduce the initial population and prevent activation.

Team sandbox + corrected spawn accounting resolves the main failure.

## Arena Run trace

The pure run model is one of the strongest parts of v5:

```text
seeded round plan
-> optional themed map
-> objective
-> clear
-> advance round
-> deterministic 3-upgrade roll
-> pick
-> next round
-> boss every fifth round
```

Map transition logic is substantially better than v4:

```text
request_map(target, payload)
-> map command
-> Quake loads map
-> player_loaded fires again
-> verify actual current map == target
-> resume stored plan
```

This removes the old fixed 1.4-second guess.

### Remaining map risks

- no map-load timeout/failure transition;
- custom/Workshop map synchronization is not yet ported into v5;
- current map command still uses FFA and should use the team sandbox;
- after a map transition, resume should wait for the human's combat spawn, not immediately launch enemies from `player_loaded`.

## Arena damage/loadout trace

Using shinqlx's exported `MOD_ROCKET`, `MOD_LIGHTNING`, `MOD_RAILGUN`, etc. is correct and fixes v4's numeric substring bug.

The bonus-damage strategy is intentionally conservative:

```text
damage hook receives raw base damage
-> plugin subtracts only bonus damage
-> plugin refuses to reduce target below 1 HP
-> Quake applies its normal base hit afterward
```

That preserves Quake's normal lethal-hit attribution while still allowing base+bonus to kill.

The damage hook can receive Player, integer entity ID, or None for target/attacker. The adapter must verify actual Player-like values before using `.id`, `.health`, etc.

### Upgrade audit

Most current alpha upgrades have a runtime consumer. `Bottomless` does not provide a real benefit because `_apply_human_loadout()` already refills ammo for everybody on spawn and is called after a clear. Remove it until the base ammo policy changes or implement a distinct refill mechanic.

## Bot identity

The current `steam_id`-starts-with-9 convention is consistent with common QL minqlx plugin practice, but an explicit numeric bot threshold is clearer and easier to test.

## Tests required before calling the logic likely-good

The fake-minqlx harness should execute the real adapter, not only the pure state classes, and cover:

1. package-style import exactly as minqlx loads it;
2. ZMQ contract rejection/acceptance;
3. human load -> team assignment -> human spawn -> mode start;
4. every scripted enemy joins BLUE;
5. early enemy kill while the rest of the wave is still spawning;
6. normal Horde clear -> next wave;
7. human death invalidates pending callbacks;
8. Gun Game kill -> weapon advance -> replacement;
9. final Gun Game kill -> terminal state, no replacement;
10. unknown/manual bot cannot satisfy or clear an objective;
11. bot disconnect while ACTIVE cannot hang a wave;
12. Arena map request -> wrong-map load ignored -> correct-map human spawn resumes exactly once;
13. Arena death/lives behavior;
14. stale callback after map transition/finish does nothing;
15. plugin-ready marker is required by the shell health check.

## Confidence by subsystem before these fixes

| Subsystem | Dry-run assessment |
| --- | --- |
| QLDS + shinqlx preload | likely sound |
| ZMQ setup order | sound |
| pure SoloController idea | sound, spawn accounting bug |
| pure Horde planning | sound |
| pure Gun Game progression | sound |
| pure Arena Run state/persistence | sound |
| plugin import | broken |
| FFA combat sandbox | wrong for wave modes |
| bot objective ownership | good concept, timing incomplete |
| map-transition concept | good, missing timeout/spawn gate |
| damage MOD mapping | sound |
| plugin health reporting | insufficient |
| current CI | useful but insufficient |

The branch should remain a draft until the deterministic blockers above are fixed and the real adapter passes the fake-minqlx event-sequence tests.