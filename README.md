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

## Current alpha status

The real Linux Mint runtime still has one high-priority unresolved issue: a scripted Solo round can **forfeit immediately** even though the server/plugin health checks and automated single-player-training regressions pass. This was reproduced again on **2026-09-26**.

Build `5.0-alpha-warmup1` targets the leading root cause: the old contract forced a live match (`g_doWarmup 0`), and every mode starts with an empty BLUE team, which Quake Live forfeits. The scripted sandbox now stays in warmup permanently (see `work/TROUBLESHOOTING_HISTORY.md` §4.3). Automatic post-game diagnostic capture remains in place. Do not interpret green CI as proof that the real-engine forfeit issue is solved until it is confirmed in real play.

Engineering history and approaches already tried are recorded in:

- `work/TROUBLESHOOTING_HISTORY.md`
- `work/HOTLOAD_CHECKPOINT.md`
- `work/RISK_REGISTER.md`
- `docs/V5_DRY_RUN.md`

## Install the launcher

Download **`quake-live-launcher-installer.sh`** from the
[latest release](https://github.com/BigCatMellow/Quake_Live_Launcher/releases/tag/v5-alpha-latest)
and run it:

```bash
bash quake-live-launcher-installer.sh
```

It downloads the latest verified build, checks its SHA-256, and installs it into your
home folder. No sudo is needed; it refuses to run as root. It installs under:

```text
~/.local/share/quake-live-launcher
~/.local/bin/quake-live-launcher
~/.local/share/applications/quake-live-launcher.desktop
```

### Automatic updates

Every time the launcher starts, it checks the `v5-alpha-latest` release (a short
timeout, so it never blocks when you're offline). If a newer build has been published,
it downloads it, verifies the checksum, installs it, and then starts the new version. A
failed download or install always falls back to starting the version you already have.
The release is only published by CI after the full test suite and install checks pass.

- Check without updating: `python3 ~/.local/share/quake-live-launcher/qll_update.py --check`
- Turn updates off: put `{"auto_update": false}` in `~/.config/quake-live-launcher/update.json`,
  or start with `QLL_NO_UPDATE=1`.
- Log: `~/.local/share/quake-live-launcher/logs/updater.log`

### Installing from a source checkout

```bash
bash install.sh
```

This is recorded as a *local* install, and the updater never overwrites it; your
working copy stays in charge. Running the installer switches back to release updates.

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
match layer -> permanent warmup (ready-up blocked, countdowns aborted)
```

The sandbox never leaves warmup. Quake Live forfeits a live TDM match when a team is
empty, and BLUE is empty at the start of every mode and after every clear; warmup
has full combat but no match that can be forfeited.

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
| Arena Run | Roguelite rounds, 3 upgrade choices (F5/F6/F7 or `!pick`), synergies, bosses, themed maps, scaling bot HP/armor, finite/endless runs. |
| Horde | Increasing waves until the player dies, elite waves every fifth wave; ammo and health are topped up between waves. |
| Gun Game | 2 kills per weapon through 7 weapons, then one Gauntlet kill. A bot Gauntlet kill demotes you a tier. |
| Boss Rush | 10 bosses with rising HP, armor and damage; resupply between bosses. |
| Wipeout Solo | Clear 5 squads with 3 lives; bot respawn delays grow and a round is won only when the squad is dead simultaneously. |
| The Gauntlet | 10 seeded stages; weapon-trial stages hand you that weapon, with resupply each stage. |
| Last Stand | One life. Threat level rises every 5 kills and every minute: more bots, higher skill, tougher and harder-hitting enemies. |
| One Life | Reach 12 kills without dying. |
| Bounty Hunt | Eliminate 8 marked targets (Haste smoke trail) while the rest interfere. |
| Rocket Tag | Rocket-only; eliminate 10 marked targets (Haste smoke trail). |
| Movement Hunter | Survive 90 seconds against armed bots, with on-screen countdown callouts. |
| Predator | Start fragile; kills heal you, but 8 seconds without a kill starts starvation. Reach 25 kills. |
| Accuracy Trial | 20 Lightning Gun kills; results show LG hits, damage per kill and average/best time-to-kill. |
| Speedrun Combat | 15 kills; the clock starts when the enemies are live, with splits at 5 and 10 against your best. |
| Random Loadout | Reach 20 kills; weapon set rerolls every 4 kills and after death. |

### After every run

- A result summary with your progress, kills and time, plus personal-best comparison.
- Records are kept per mode and difficulty (and Arena Run length) in
  `~/.config/quake-live-launcher/solo_records.json`; `!best` shows them in game.
- `!again` (or `!restart`) replays the same mode in place with a new seed, without
  closing Quake or returning to the launcher.

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
`+moveright` (detected from your config, so ESDF/arrow layouts work), binds
F5/F6/F7 to Arena Run upgrade picks, then restores the original binds after
Quake closes.

## Director

The Director manages encounters without aiming or firing for bots (see
`docs/DIRECTOR_DESIGN.md`). Since 5.0-alpha-director1 it also:

- learns each map's spawn points from real spawns and moves enemy spawns that land
  on top of you to fair positions in its engagement band;
- sends part of larger squads as delayed flankers that prefer your sides and rear.

### Director capability probe

Some Director features depend on Quake Live behavior that can only be checked on a
real server (custom bot personalities, fractional bot skill, item IDs, whether bots
chase dropped items). Run once:

```bash
bash ~/.local/share/quake-live-launcher/solo_engine/run_director_probe.sh
```

It takes about two minutes, restores your Solo session afterwards, and writes
`~/.local/share/quake-live-launcher/solo_runtime/director_probe.json`. Add `--watch`
to spectate it in Quake Live. What was verified and why is in
`docs/DIRECTOR_CAPABILITIES.md`.

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
