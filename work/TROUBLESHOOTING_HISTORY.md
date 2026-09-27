# Troubleshooting History — Quake Live Launcher v5

> Purpose: preserve the failures, attempted fixes, evidence, dead ends, and unresolved questions so a future debugging pass does not repeat work that has already been tried.
>
> This is a living engineering log. v5 has now been merged into `main`; `v5-alpha` remains the rolling alpha/development branch used for verified prerelease builds.

## Current highest-priority failure

**Status as of 2026-09-26: still reproducing in real Linux Mint play on `5.0-alpha-hotload2`. `5.0-alpha-warmup1` addresses the leading root cause (see §4.3) and needs live confirmation.**

The scripted Solo round can **forfeit immediately** after the player enters the match.

This remains unresolved even though:

- QLDS starts;
- the UDP socket can be healthy;
- shinqlx/plugin readiness can be healthy;
- the scripted plugin can initialize;
- automated fake-minqlx tests pass;
- the training/single-player contract is asserted repeatedly in code.

Therefore **do not treat a green CI run, a live QLDS process, UDP readiness, or `plugin_ready.json` as proof that the real Quake client will not enter its normal one-player multiplayer forfeit path.**

The next evidence source is the automatic post-game diagnostic uploader added in `5.0-alpha-hotload2`.

---

# 1. v4.11 lessons that caused the v5 rebuild

The v4.11 audit found multiple architectural problems. v5 was intentionally built to avoid patching around them.

## What was wrong

- Single-player behavior was not based on the proper shinqlx `allow_single_player(True)` contract.
- ZMQ-dependent hooks could be used without guaranteeing `zmq_stats_enable 1`.
- Existing Quake cvars were sometimes handled with `set_cvar_once` when runtime enforcement required `set_cvar`.
- `bot_minplayers` could race against scripted bot ownership.
- Wave ownership could become "all bots" rather than exact plugin-owned bot IDs.
- Delayed callbacks could fire after the objective that created them had ended.
- Map transitions used fixed sleep/timer guesses instead of confirmed engine lifecycle events.
- MOD/damage handling relied on string/numeric heuristics instead of exported minqlx constants.
- Some upgrades were advertised without meaningful runtime behavior.
- Some modes did not have complete terminal-state behavior.
- Movement/ground detection contained heuristic behavior that was too weak for reliable scripted movement.

## Decision

Do not reintroduce these shortcuts to solve new runtime problems unless new evidence proves they are necessary.

Reference: `docs/V4_11_AUDIT.md`.

---

# 2. First v5 dry-run failures

The initial v5 adapter looked sound at the pure-state level but failed a stricter execution walk-through.

Reference: `docs/V5_DRY_RUN.md`.

## 2.1 minqlx package imports were wrong

### Failure

Plugin sibling imports assumed the plugin directory itself was directly on `sys.path`.

### Why it mattered

That could compile normally and still fail when minqlx loaded the plugin using its real package layout.

### Fix

The Solo runtime became an actual package and plugin sibling imports were corrected to package-relative imports.

Relevant history includes:

- `6673f4b` — make Solo runtime a package
- `56c04ad` — make Solo modes a package

### Rule

A normal Python import test is not enough. Keep the fake-minqlx package-layout import test.

---

## 2.2 FFA was the wrong combat sandbox

### Failure

Scripted wave enemies were originally spawned into FFA/free.

### Effect

Enemies could treat each other as opponents, corrupting objective ownership and wave behavior.

### Fix

Scripted Solo uses an invisible TDM sandbox:

- human = RED;
- scripted enemies = BLUE;
- friendly fire off;
- normal score/time/round limits disabled;
- `bot_minplayers 0`;
- plugin owns objective completion.

### Rule

Do not switch wave modes back to FFA as a convenience fix.

---

## 2.3 Living-bot count was incorrectly used as spawn completion

### Failure

The controller originally decided that PREPARING was complete when the number of *currently alive* enemies reached the intended spawn count.

If an early bot died before the later staggered spawns occurred, the living count might never reach the target.

### Effect

An objective could remain stuck in PREPARING forever.

### Fix

Track these separately:

- `expected_spawns`;
- `fulfilled_spawns`;
- `enemy_ids` currently alive.

Activation uses fulfilled spawn events. Clearing uses living owned IDs.

### Rule

Never collapse spawn accounting and live-enemy ownership into one number.

---

## 2.4 QLDS/UDP health was a false-positive health check

### Failure

The original shell check only proved that `qzeroded` was alive and UDP 27960 was listening.

### Effect

A plugin import/init failure could still look like a healthy Solo server.

### Fix

The plugin writes a readiness handshake, and startup requires matching plugin readiness before launching the client.

Relevant history:

- `62192cf` — require real plugin readiness before client launch
- `d048897` — add installed-runtime QLDS self-test

### Rule

Process alive + socket open != Solo Engine ready.

---

## 2.5 Starting from player_loaded was too early

### Failure

Mode startup could begin at `player_loaded`, before the human was guaranteed to be alive on the intended combat team.

### Effect

Bots/objectives could begin during spectator/team transition and startup deaths could be misinterpreted.

### Fix

Remember the human at load, force RED, then gate actual mode start on a real human spawn.

### Rule

A client having loaded is not the same thing as a player being combat-ready.

---

## 2.6 Fixed-delay map transitions were not reliable

### Failure

Older logic assumed a map would be ready after a hard-coded delay.

### Fix

Map changes now carry pending transition state and resume only after the expected map/player lifecycle is observed.

### Remaining risk

Real Workshop/custom maps can still have variable load time or fail independently. A 15-second launcher hot-load acknowledgement timeout is currently unproven for every map and should not be changed without live evidence.

---

# 3. Real gameplay failures after the simulated foundation passed

Automated tests proved many lifecycle invariants, but real Linux Mint play found product failures that simulation did not.

## 3.1 Passive / non-contributing bots

### Observation

QLDS and the plugin could work, but some Horde encounters felt passive or bots failed to contribute enough.

### What changed

The project added:

- explicit combat-ready loadouts;
- safer aggressive engine cvars;
- encounter roles;
- contribution/engagement telemetry;
- conservative idle-bot recovery/replacement;
- the Encounter Director.

### What was deliberately *not* done

- no scripted direct aiming;
- no scripted firing;
- no custom pathfinding;
- no hidden damage multipliers;
- no hostile instant teleport into danger.

### Current state

Improved in tests, but subjective quality still requires real play.

---

## 3.2 Modes appearing to terminate immediately

### Observation

Several scripted modes appeared to end immediately during early real play.

### Changes made

The runtime added/strengthened:

- startup-death guards;
- explicit terminal-state logic;
- exact bot ownership;
- generation-safe delayed callbacks;
- team assertions;
- finite-mode completion tests.

### Rule

A death/event during PREPARING, team transition, or map transition must not be treated as an ACTIVE objective death unless the mode contract explicitly says so.

---

# 4. Instant one-player forfeit history

This is the current blocker.

## 4.1 First approach: call allow_single_player(True)

The initial assumption was that calling shinqlx `allow_single_player(True)` during plugin initialization would be enough.

### Result

**Not sufficient in real play.** The game could still immediately enter the ordinary multiplayer one-player forfeit path.

### Why the model changed

`allow_single_player(True)` affects the **current level**. A constructor/init call can happen before the eventual CurrentLevel exists. A successful call at that point is therefore not proof that the loaded map received the training/single-player state.

---

## 4.2 Second approach: treat training state as an invariant

The runtime was changed to enforce the contract repeatedly:

- request `g_training 1` before the initial `+map`;
- put `g_training 1` in server configuration;
- call `allow_single_player(True)` on new-game;
- call it on map;
- call it on player-loaded;
- call it on player-spawn;
- reassert from the first eligible live frame;
- continue low-frequency reassertion while the server remains alive;
- reset the frame assertion gate after hot-loaded map transitions.

Regression:

- `3826a40` — deliberately remove fake training permission during ACTIVE Horde and prove the next live frame restores it without ending the objective.

### Result

Automated proof passes.

### Real result

**Still not closed.** On 2026-09-26 the operator again reported that the round forfeits instantly.

This means at least one of the following remains possible:

1. the real engine's forfeit decision occurs before our reassertion can matter;
2. `g_training` / `allow_single_player` is not the entire real-engine contract;
3. another cvar/factory/gamestate transition later overwrites it;
4. the client/server are entering a different state than our fake-minqlx model represents;
5. an old installed runtime/plugin may be involved;
6. the observed "forfeit" is produced by another Quake lifecycle condition that looks similar.

Do not select one of these as the root cause without runtime evidence.

## 4.3 Third approach (5.0-alpha-warmup1): never leave warmup

### New analysis

Re-reading the contract against the mode bootstrap exposed a structural problem that no amount of training-state reassertion could fix:

- `g_doWarmup 0` + `sv_warmupReadyPercentage 0` deliberately forced the sandbox straight into a **live match**.
- Every mode starts from `handle_player_spawn` -> `_spawn_objective_bots` -> `clear_all_bots()`, and bots are only added by `minqlx.delay` callbacks on later frames. So at the exact moment the human spawns, **BLUE is empty**.
- Defeated enemies are kicked, so BLUE empties again at every wave/round clear.
- A live TDM match with an empty team is Quake Live's normal team-forfeit path.
- minqlx/shinqlx `allow_single_player()` only sets `level->mapIsTrainingMap`; its own docstring scopes it to letting a *single player* continue ("useful for race"). Nothing indicates it suppresses the empty-team rule.

This also explains the v4 history: the "bootstrap bot" existed precisely to keep the other side populated, and its races were races against this rule.

### Change

The scripted sandbox now stays in **warmup for its whole lifetime**. Warmup has full combat, bots, spawns and minqlx death/damage events (QL stats events carry a `WARMUP` flag and are still emitted), but no match exists that can be forfeited. The plugin already owns objectives, lives, scoring and completion, so nothing depended on the match layer.

- `g_doWarmup 1`, `sv_warmupReadyPercentage 1`, `g_warmupReadyDelay 0` in the plugin, `server.cfg` template and `start_solo.sh`.
- `start_solo.sh` sets them **after** `+exec server.cfg`, because already-installed `server.cfg` files still say `g_doWarmup "0"`.
- `readyup` / `ready` / `notready` client commands are blocked.
- `game_countdown` / `game_start` hooks, plus a 1 s frame backstop on `game.state`, run `abort` if the engine ever leaves warmup anyway. Each is logged as `warmup guard:` in the minqlx log.
- `game_end` is logged with `ABORTED` / `EXIT_MSG` so a remaining forfeit shows up in post-game diagnostics.
- Hot-load protocol bumped 1 -> 2 so a still-running old server is restarted instead of reused.
- `FakeServer` now models the live-match empty-team forfeit rule; `tests/test_warmup_sandbox.py` asserts no mode forfeits at spawn or when BLUE empties.

### Status

Still requires real-play confirmation (R-002 stays open until then). If a forfeit still occurs, the minqlx log should now show either a `warmup guard:` line (something started a match) or a `game_end` line with the exit message, which narrows it to a different cause.

### Known trade-off

Quake Live's own scoreboard/accuracy stats may not accumulate during warmup. The plugin's kill/score tracking is unaffected.

---

## 4.4 Other ways a run could end instantly (fixed in 5.0-alpha-modes1)

Found while reviewing the modes; either could look like an early forfeit:

- **Bot self-kills failed the whole run.** A bot's own rocket/grenade splash is reported with the bot as its own killer, which tripped the bot-vs-bot contract check (`SOLO ENGINE CONTRACT FAILURE`). `bot_rocketjump 1` and Rocket Tag made this common. Self-kills (`SUICIDE` flag or killer == victim) and telefrags are now neutral enemy deaths; other bot-vs-bot kills still fail the contract.
- **Duplicate bot names mis-resolved deaths.** shinqlx/minqlx resolve bot deaths by name (bots have no Steam ID in the stats stream) and take the first match. Continuous-mode replacements and Horde wave 14+ reused live names, so a kill could remove a living namesake while the real victim stayed counted. Every bot in play now gets a unique name (reservations cover scheduled adds).

## 4.5 Solo controls cfg was never effective (fixed in 5.0-alpha-modes1)

The retained launcher payload stored its controls helpers with one escaping layer too many: the cfg was joined with a literal backslash-n (one line beginning with `//`, so the whole file was a comment), and the bind regexes contained literal `\\s`, so strafe-key detection always fell back to A/D and restores appended junk lines to `qzconfig.cfg`. Side-thruster keys therefore never worked (only `!dash`). `launcher.py` now overrides `_parse_binds`, `_replace_bind_line` and `write_solo_controls_cfg`; the payload stays byte-stable.

---

## 4.6 Real play, 2026-09-26 evening (5.0-alpha-setup1 branch build)

Evidence: the player got past the start of the match (no instant forfeit reported), then:

1. **"Server disconnected - flooding the server".** Quake 3 servers accept one client command per second (`sv_floodProtect`); Quake Live disconnects clients that exceed it. The 4.5 controls fix made the strafe-key wrapper live for the first time, so every strafe tap sent `cmd qldash`. Fix (5.0-alpha-controls1): strafe keys are never wrapped; dash uses one dedicated unbound key (`cmd qldash auto`, direction from the current strafe); stale wrapper binds are repaired; the local-only server sets `sv_floodProtect 0`.
2. **A top-of-screen message flashing, "This match will determine …".** Matches Quake Live's training-match text. The old anti-forfeit "training contract" set `g_training 1` and `allow_single_player(True)` and re-applied both every second. Permanent warmup (4.3) is what prevents the forfeit, so both are removed: `g_training 0` is set after `server.cfg`, the training flag is cleared once per map, and nothing is re-applied per frame.

3. **The automatic post-game report (GitHub issue #8) showed shinqlx's stats listener dying at every start**: `OSError: zmq error: InvalidArgument`. shinqlx subscribes with ZMQ PLAIN auth (user `stats`, password = `zmq_stats_password`), and libzmq's `ZMQ_PLAIN_PASSWORD` accepts only NULL or a non-empty value. Verified against libzmq 4.3.5: an empty password gives `EINVAL` (errno 22). The server never set a password, and death/kill events arrive **only** through that listener. **In real play the Solo plugin has therefore never received a single kill or death**: waves could not clear and deaths could not end runs. The fake-engine tests could not see this because they inject death events directly. Fix: `start_solo.sh` sets a fresh random `zmq_stats_password` per launch (before `+map`); startup fails with exit 8 if a `zmq error` appears; the plugin refuses to load with an empty password.
4. **The same report was posted at launch** ("quake-client-never-appeared"). `quake_running()` matched any command line containing "Quake Live" (including helpers with the game folder in their arguments), and the watchers trusted one sighting. The bind-restore watcher has the same race. Fix: our own helpers are excluded, and "started"/"closed" must hold for 4 s.

The absence of an instant forfeit in this run is the first live evidence for the 4.3 warmup fix. R-002 still needs an explicit confirmation run, now with working death events.

## 4.7 "Still flashing" after installing 5.0-alpha-controls1: the client joined the OLD server

Evidence (diagnostics, horde on trinity): `Stopping previous Solo server PID 11932`, then from the new server `UDP_OpenSocket: bind: Address already in use`, `Opening IP socket: 127.0.0.1:27961`, `zmq PUB socket error, bind failed: tcp://127.0.0.1:27960`; `ss -lunp` showed PID 11932 (the previous Arena Run server, old build) still on 27960 and the new PID on 27961.

Cause: the start script sent one SIGTERM, slept 1 s and moved on. PID 11932 ignored it. shinqlx's stats listener is a non-daemon thread with an endless loop, so a server with a working listener can outlive SIGTERM. The new server fell back to 27961. The health check only asked "is anything listening on 27960?", which the old server answered, so startup reported HEALTH OK. The client connected to 27960, the old server with the training flag, and the message kept flashing. None of the 4.6 fixes could have been visible in that session.

Fix (5.0-alpha-ports1):
- `stop_solo.sh` sends SIGTERM, waits 5 s, then SIGKILL. It also stops Solo servers the PID file no longer knows about: any process running our `qlds/qzeroded.x64` that owns the port or was started with `+set net_port PORT`, which covers the fallback case. It never kills a process that isn't our qzeroded, even if a stale PID file names it.
- `start_solo.sh` uses `stop_solo.sh`, then refuses to launch (exit 9) while anything still owns the port, and names the owner.
- Health now needs three things: the new PID must own the UDP port (`solo_ports.py`, read from `/proc`), `plugin_ready.json` must carry the new PID, and the engine log must have no bind error (exit 9). A server that fails health is killed rather than left running.
- The self-test cleanup uses `stop_solo.sh`. The launcher's start timeout goes from 35 s to 75 s.

---

# 5. Persistent Solo / hot-load attempts

The second live usability problem was having to close/reopen Quake for every scripted Solo mode.

## 5.1 Rejected approach: restart QLDS but keep Quake open

This was considered and rejected as the main architecture.

### Why

Stopping the local server disconnects the client. Reconnection would depend on reliably injecting client commands into an already-running Quake process, which the launcher does not own well enough.

### Chosen model

Keep one QLDS process and one client connection alive. Switch the scripted match inside the live server and use ordinary server map transitions.

---

## 5.2 First hot-load availability check was too weak

### Early assumption

If a QLDS PID exists and `plugin_ready` is true, reuse the server.

### Problem

An older plugin/server could satisfy those generic checks but not understand the new hot-load protocol.

### Fix

Add `hotload_ready.json` with:

- protocol version;
- live server PID;
- ready flag;
- current mode.

The launcher only reuses the server when the marker matches the **current PID** and expected protocol.

Relevant commits:

- `c5528e0` — require live hot-load capability handshake
- `b05f27f` — advertise hot-load protocol from live server
- `a12fb18` — test live capability marker
- `1f670d0` — test PID-matched capability before reuse

---

## 5.3 Reporting "loading" as successful hot-load was wrong

### Failure

The launcher initially accepted `loading` as a successful match switch.

### Why that was wrong

A map request being accepted is not proof that the human has actually spawned into the new match.

### Fix

Only `started` or `active` counts as launcher success.

Relevant commits:

- `b095aa0` — wait for live player spawn before confirming hot-load
- `e1ee2e3` — regression requiring spawn before success

---

## 5.4 Recreating SoloController on hot-load could allow stale callback collisions

### Failure model

A fresh controller could restart its generation counter. A delayed callback from the old mode could theoretically have a generation value that matched the new controller.

### Fix

Keep the controller object, explicitly finish/bump the old generation, clear the old encounter, then reuse it with the new mode.

Relevant commits:

- `b5a3188` — preserve lifecycle generations across Solo hot-loads
- `8df9e97` — prove stale callbacks cannot cross a hot-load

### Rule

Generation IDs must remain monotonic across the lifetime of one persistent server.

---

# 6. Diagnostics evolution

## 6.1 Manual logs were not enough

Earlier debugging depended on manually finding and sharing files under:

`~/.local/share/quake-live-launcher/logs/`

That is fragile for failures that happen immediately or only on shutdown.

## 6.2 Automatic post-game capture

Added in `5.0-alpha-hotload2`.

Relevant commits:

- `26e0045` — upload Solo exit diagnostics to GitHub
- `9664bbd` — bump debug build version
- `ffdb32f` — test automatic GitHub debug upload
- `90f4134` — document automatic post-game capture

### Behavior

A detached watcher survives launcher/plugin failure, waits for Quake to close, then captures:

- launcher/system diagnostics;
- Solo session;
- `plugin_ready.json`;
- `hotload_ready.json`;
- match request;
- match status;
- minqlx log tail;
- QLDS/server log tail.

It always creates a local report.

If GitHub CLI is installed and authenticated as `BigCatMellow`, it posts a privacy-scrubbed GitHub issue.

### Security decisions

Do **not** embed a GitHub token in the launcher.

The public issue copy scrubs:

- home-directory path;
- hostname;
- username where practical;
- obvious GitHub token forms;
- Authorization token values.

Automatic upload is restricted to the repository owner's authenticated `gh` account.

Upload status is stored in:

`~/.local/share/quake-live-launcher/solo_runtime/last_github_debug.json`

---

# 7. CI/package issue that was not a product failure

On 2026-09-26 the first `hotload2` CI run failed after all 138 unit/integration tests passed.

### Cause

The disposable-install smoke test still asserted:

`APP_VERSION == 5.0-alpha-hotload1`

while the launcher correctly reported:

`5.0-alpha-hotload2`.

### Fix

Update the stale CI expectation.

Commit:

- `53e9318` — expect hotload2 launcher version

The subsequent workflow passed.

### Lesson

Distinguish stale verification metadata from runtime product failure. The failed workflow did **not** indicate a launcher logic regression.

---

# 8. Verified release packaging

A rolling prerelease is published only after the product workflow passes.

Current mechanism:

- build install ZIP;
- verify archive file set equals shipped source;
- verify byte equality after extraction;
- verify executable bits;
- publish/replace `v5-alpha-latest` release asset.

Commit introducing rolling release:

- `aff0281` — publish verified v5 alpha launcher release

This keeps a downloadable installer out of normal Git history while still giving the operator a stable download URL.

---

# 9. Things that green tests still do not prove

These require real Quake Live / Linux Mint play:

- the real engine does not forfeit a one-human scripted match;
- the real client remains connected through repeated server map changes;
- repeated Horde -> Gun Game -> Arena Run hot-loads do not leak bots/state;
- Workshop maps load within the current acknowledgement assumptions;
- movement bind restoration works after every exit/crash path;
- Director pressure feels fair rather than rubber-banded;
- native Quake bot behavior is sufficiently active on representative maps;
- persistent Director learning improves play instead of overfitting.

---

# 10. Current next diagnostic sequence

For the immediate-forfeit issue:

1. Install the latest `v5-alpha-latest` build.
2. Ensure `gh auth status` is authenticated as `BigCatMellow` if automatic issue upload is desired.
3. Close old Quake/QLDS processes once before the test so the new plugin is definitely loaded.
4. Start a scripted Solo Horde match.
5. Allow the instant forfeit to occur.
6. Close Quake.
7. Check:
   - the new auto-debug GitHub issue, or
   - `last_github_debug.json` and the local diagnostic report if upload was unavailable.
8. Compare timestamps/state around:
   - initial map;
   - new_game/map hooks;
   - player_loaded;
   - player_spawn;
   - training-state assertions;
   - first forfeit indication;
   - plugin readiness and server liveness.
9. Change the anti-forfeit logic only after identifying which event happens first in the real engine.

---

# 11. Do-not-repeat list

Unless new evidence specifically contradicts these conclusions:

- Do not trust UDP/process health by itself.
- Do not trust plugin import/readiness as proof of gameplay correctness.
- Do not rely on a constructor-only `allow_single_player(True)` call.
- Do not return scripted wave modes to FFA.
- Do not use `bot_minplayers` to own scripted population.
- Do not infer completed spawns from living enemy count.
- Do not use unguarded delayed callbacks.
- Do not use fixed sleeps as proof that a map transition completed.
- Do not accept hot-load `loading` as user-visible success.
- Do not reuse a server merely because a PID and generic plugin readiness exist; require the PID-matched hot-load protocol marker.
- Do not recreate lifecycle generations in a way that allows old callbacks to collide with a new match.
- Do not restart QLDS under an already-running Quake client as the normal hot-load design.
- Do not embed GitHub credentials in the launcher.
- Do not mark the immediate-forfeit issue resolved from simulation alone.
- Do not return the scripted sandbox to `g_doWarmup 0` / `sv_warmupReadyPercentage 0`, and do not set warmup cvars before `+exec server.cfg`; a live match forfeits whenever BLUE is empty, which every mode causes at start and at every clear.

---

# Related records

- `docs/V4_11_AUDIT.md` — reasons v4.11 was replaced.
- `docs/V5_DRY_RUN.md` — deterministic v5 integration problems found before live play.
- `work/HOTLOAD_CHECKPOINT.md` — current anti-forfeit/hot-load architecture and live validation gate.
- `work/RISK_REGISTER.md` — active engineering/product risks.
- `work/ROADMAP.md` — release definition and current next work.
- `docs/DIRECTOR_DESIGN.md` / `docs/DIRECTOR_LEARNING.md` — Director boundaries and learning contract.
