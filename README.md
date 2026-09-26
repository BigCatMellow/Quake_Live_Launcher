# Quake Live Launcher — Linux v5.0-alpha

A Linux-first Quake Live launcher focused on **single-player/offline play against bots**.
It combines the native Quake Live launcher/factory features from v4.x with a rebuilt
scripted Solo Engine designed around a single explicit lifecycle.

## What the launcher includes

- Native Steam and Flatpak Steam detection.
- Steam library and Workshop map scanning.
- `.pk3`, loose BSP and `.arena` metadata scanning.
- Curated mode-aware map recommendations and random selection.
- Native Arcade modes built from Quake Live factories/cvars.
- Scripted Solo modes powered by a local Quake Live Dedicated Server + shinqlx.
- Solo movement options: enhanced air control plus left/right ground dodge-hop and air dash.
- Detailed setup/start/server diagnostics.
- Optional automatic post-game GitHub debug capture for the repository owner.

## Install/update the launcher

```bash
bash install.sh
```

The normal launcher install does not require sudo. It installs under:

```text
~/.local/share/quake-live-launcher
~/.local/bin/quake-live-launcher
~/.local/share/applications/quake-live-launcher.desktop
```

## Solo Engine setup

Open the **SOLO** tab and choose:

**SET UP / REPAIR SOLO ENGINE**

The first setup installs/updates the local Quake Live Dedicated Server and builds
shinqlx in a private Python environment. On Linux Mint/Ubuntu it may ask for sudo
only to install missing compiler/runtime packages.

v5 no longer marks the Solo Engine ready merely because files installed. Setup runs
`solo_engine/self_test.sh`, which launches the real local QLDS + shinqlx +
`solo_arcade` plugin on test port 27961 and requires the plugin readiness handshake.
Only after that succeeds are these markers created:

```text
~/.local/share/quake-live-launcher/solo_runtime/SELF_TEST_OK
~/.local/share/quake-live-launcher/solo_runtime/READY
```

## Scripted Solo architecture

The underlying scripted match is **TDM used as an invisible combat sandbox**:

```text
human player -> RED
scripted bots -> BLUE
friendly fire -> off
frag/time/score limits -> disabled
bot_minplayers -> 0
```

Quake Live supplies physics, bot AI, navigation and weapon combat. The plugin owns
waves, bot ownership, progression, lives, bosses, objectives and completion.

shinqlx `allow_single_player(True)` is reapplied during lifecycle transitions so a
one-human local match can continue without the old v4 bootstrap-bot/forfeit races.

### Solo lifecycle

```text
WAITING_FOR_PLAYER
        ↓
PREPARING
        ↓
ACTIVE
        ↓
BETWEEN_ROUNDS
        ↓
PREPARING ...
        ↓
COMPLETE

Any state can become FAILED when a runtime contract is violated.
```

The controller separately tracks:

- intended spawn events fulfilled;
- enemies currently alive;
- exact owned bot client IDs;
- callback generation tokens;
- pending map transitions.

That prevents an early bot kill during staggered spawning from deadlocking a wave.

## Scripted Solo modes

| Mode | Behavior |
| --- | --- |
| Arena Run | Roguelite rounds, 3 upgrade choices, synergies, bosses, themed maps and finite/endless runs. |
| Horde | Increasing waves until the player dies, with elite waves every fifth wave. |
| Gun Game | Every kill advances the weapon ladder; finish with a Gauntlet kill. |
| Boss Rush | Defeat 10 increasingly modified bosses. |
| Wipeout Solo | Clear 5 squads; respawn delays grow and a round is won only when the squad is dead simultaneously. |
| The Gauntlet | 10 seeded stages mixing weapon trials, survival, duel-like encounters and bosses. |
| Last Stand | Continuous bots; one life; score is kills and survival time. |
| One Life | Reach 12 kills without dying. |
| Bounty Hunt | Eliminate 8 marked targets while the rest interfere. |
| Rocket Tag | Rocket-only target chase; eliminate 10 marked targets. |
| Movement Hunter | Survive 90 seconds against armed bots. |
| Predator | Start fragile, heal on kills, reach a 25-kill streak. |
| Accuracy Trial | Clear 20 Lightning Gun kills, then review final weapon accuracy. |
| Speedrun Combat | Clear 15 kills as fast as possible. |
| Random Loadout | Reach 20 kills; weapon set rerolls every 4 kills and after death. |

## Arena Run upgrades

The v5 upgrade pool only exposes effects with runtime consumers, including:

- all-damage, health and armor stacking;
- Haste;
- jump boost;
- Phase Thrusters (extra dash charge + thrust);
- out-of-combat regeneration;
- lifesteal and kill healing;
- Rocket/LG/Rail/Plasma damage builds;
- LG Overcharge and Vampiric Current;
- Perfect Shot Rail combo;
- ammo scavenging on kills;
- Quad Burst;
- Glass Cannon / Berserker tradeoffs;
- STORMBRINGER, DEADEYE, JUGGERNAUT and VELOCITY synergies.

## Movement

Normal Quake jump is left intact.

With Side Thrusters enabled:

- tap left/right on the ground for a quick horizontal dodge plus a short hop;
- tap left/right in the air for a lateral correction;
- charges refresh after a confirmed landing;
- Arena Run movement upgrades can add charges and thrust.

The launcher temporarily wraps the keys currently bound to `+moveleft` and
`+moveright`, then restores the original binds after Quake closes.

## Map recommendations

The launcher rates curated maps by mode and prefers the best installed match for
Recommended Random. Arena Run and Gauntlet use themed pools for normal, boss,
elite, Rail, Rocket, LG, Plasma, Duel and survival stages. Workshop maps continue
to be discovered from `.arena` metadata and synced into the local QLDS runtime.

## Diagnostics

The SOLO tab provides:

- **VIEW LATEST LOG**
- **RUN DIAGNOSTICS**
- **OPEN LOG FOLDER**

Logs live under:

```text
~/.local/share/quake-live-launcher/logs/
```

### Automatic post-game GitHub debug capture

When a Solo-launched Quake client closes, the launcher starts a detached watcher that
captures the current session, plugin/hot-load state, server log and minqlx log. It
always saves the report locally. If GitHub CLI (`gh`) is installed and authenticated
as the repository owner (`BigCatMellow`), it also creates a privacy-scrubbed issue in
`BigCatMellow/Quake_Live_Launcher`. Home-directory paths, hostname and obvious GitHub
token patterns are redacted before upload.

One-time GitHub CLI authentication:

```bash
gh auth login
```

Upload success/failure is recorded in:

```text
~/.local/share/quake-live-launcher/solo_runtime/last_github_debug.json
```

A normal Solo launch is considered healthy only when all of these are true:

```text
qzeroded process alive
+ requested UDP socket visible
+ solo_arcade plugin_ready.json exists
+ ready == true
+ handshake mode == requested mode
```

This prevents a running ordinary QLDS process from being mistaken for a working
scripted mode when the Python plugin failed during import or initialization.

## Development verification

The repository test suite includes:

- pure Arena Run/state-machine tests;
- launcher/package/resource tests;
- a fake-minqlx integration harness that imports the plugin using the runtime-style
  `minqlx-plugins` package layout;
- ugly event-order tests such as killing an enemy before later wave spawns finish;
- red-human/blue-bot team assertions;
- bot-vs-bot contract-failure detection;
- map-transition resume tests;
- full finite-mode completion tests;
- ground dodge-hop simulation.

GitHub CI cannot execute Steam's `qzeroded.x64`, which is why the installed product
also carries the real-runtime `self_test.sh` gate.

## Uninstall

```bash
bash uninstall.sh
```

The launcher uninstall preserves the large Solo runtime/downloads unless explicitly
removed, so reinstalling the launcher does not necessarily require downloading QLDS
and rebuilding shinqlx again.
