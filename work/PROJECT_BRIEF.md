# Project Brief: Quake Live Launcher v5

- Owner: ChatGPT / project agent, with the repository owner as operator
- Status: ACTIVE
- Goal: Deliver an installable Linux-first Quake Live launcher whose native Arcade modes and advertised scripted Solo modes operate reliably for one human against bots.
- User/operator: A Linux Mint Quake Live player who primarily wants offline/single-player play.

## Current reality

- Checked facts:
  - The usable v4.11 package contains the launcher GUI, installer, Steam discovery, installed/Workshop map scanner, curated map recommendations, native Arcade factories, 15 Solo cards, movement controls, logging, and Solo setup tooling.
  - Repository `main` currently contains documentation only; it is not an installable product.
  - Repository `v5-alpha` contains the rebuilt Solo controller/plugin slice, QLDS start configuration, and pure tests, but not the full launcher shell.
  - Dry-run review found deterministic v5 blockers: package imports, false-positive health checks, FFA bot infighting, and objective activation deadlock when an enemy dies before all scheduled enemies have spawned.
  - Existing CI passes because it tests pure state in an ideal event order and does not load the plugin through a minqlx-like package layout.
- Evidence/source paths:
  - `docs/V4_11_AUDIT.md`
  - `docs/V5_ARCHITECTURE.md`
  - `docs/V5_DRY_RUN.md`
  - v4.11 package source retained during reconstruction
  - `plugins/`, `qlds/`, `tests/` on `v5-alpha`
- Important assumptions:
  - The current shinqlx/minqlx APIs documented upstream remain compatible with the target Quake Live Dedicated Server build.
  - TDM can be used as the hidden combat sandbox for one red human versus blue scripted enemies while the plugin owns objectives and completion.

## Definition of DONE

- Finished result:
  - A fresh checkout of the release branch is directly installable with `bash install.sh` on the target Linux family.
  - Quick Play, native Arcade, Solo, and Custom Match are present.
  - Every Solo mode visible as playable has implemented progression, termination, and failure behavior.
  - Solo movement includes selectable air control plus lateral ground dodge-hop / air thrust without replacing normal jump.
  - Installed and Workshop maps remain discoverable and Solo map pools are synchronized to QLDS.
  - Solo startup refuses to claim success until QLDS is alive, UDP is listening, and `solo_arcade` has written a mode-matching readiness handshake.
  - Failures produce a readable log/diagnostic report rather than silently dropping into ordinary Quake FFA/TDM.
- Final proof:
  - Python compile + shell syntax + JSON/resource integrity.
  - Pure state tests.
  - Fake-minqlx integration simulations covering plugin package import, human load/spawn, team assignment, staggered bot spawning, early kills, bot-vs-bot rejection, human death, stale callbacks, map transitions, Arena upgrades, Gun Game replacement, Wipeout timers, and every advertised mode reaching a valid active/complete/fail path.
  - Package/install smoke test from a disposable HOME.
  - Runtime self-test script that, on a machine with QLDS/shinqlx installed, starts QLDS and requires the plugin readiness handshake before passing.
- Final proof performed/inspected by: automated CI plus the project agent; the runtime self-test executes locally on the operator machine without requiring gameplay.

## Scope and boundaries

- In scope:
  - Full v4.11 launcher shell and resources.
  - Corrected v5 Solo engine.
  - All 15 currently advertised Solo modes, or removal/disablement of any mode that cannot meet the same pass/fail contract.
  - Movement features, map recommendations, Workshop sync, logs/diagnostics, installer/uninstaller.
- Not doing:
  - Multiplayer matchmaking/server administration.
  - Replacing Quake Live physics/AI with a new game engine.
  - Requiring root for normal launcher use; Solo setup may request standard package installation when dependencies are missing.
- Effort limit:
  - Reconsider architecture if a core shinqlx/QLDS assumption is disproven by upstream source/API evidence or the automated runtime self-test.

## Constraints and quality bar

- No mode may be labeled playable merely because its Python file compiles.
- One-human play must not depend on bootstrap bots used solely to stop Quake forfeiting.
- Delayed callbacks must be generation/token guarded when they can mutate later objectives.
- The engine and plugin must have one clear owner for each lifecycle decision.
- User configuration/keybind changes must be temporary and recoverable.

## Unknowns and risks

- Exact live behavior of current QLDS/shinqlx can only be fully exercised where the Steam dedicated server is installed.
- Research/prototype needed first: automated runtime readiness/self-test plus fake-minqlx integration harness.
- Evidence that would invalidate the current plan: `allow_single_player(True)` or the TDM red-vs-blue sandbox failing under the current QLDS build despite correct startup configuration.

## Decision path

- Owner may decide: internal module layout, test architecture, logging, state-machine decomposition, and implementation details that preserve the visible product contract.
- Escalate to operator: dropping an advertised mode, removing a user-facing feature, introducing paid/external dependencies, or changing the primary platform.

## Planning

- Roadmap: `work/ROADMAP.md`
- Roadmap state: WORKING
- Mission meeting required: YES
- First wave: QLL-001 through QLL-004
- Reconsider if: runtime contract evidence fails, an advertised mode cannot be made deterministic, or integration complexity requires a materially different product scope.
