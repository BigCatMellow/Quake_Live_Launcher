# Roadmap: Quake Live Launcher v5

- State: WORKING

## Current reality

- Checked facts: the complete usable launcher exists in the retained v4.11 package; GitHub `main` is documentation-only; `v5-alpha` has the new Solo slice but four deterministic dry-run blockers and incomplete product packaging.
- Evidence/source paths: `docs/V4_11_AUDIT.md`, `docs/V5_ARCHITECTURE.md`, `docs/V5_DRY_RUN.md`, v4.11 retained source, `plugins/`, `qlds/`, `tests/`.
- Important assumptions: current shinqlx supports the documented single-player, player/team, hook, weapon/ammo/powerup, and console-command APIs; TDM is a viable hidden enemy-team sandbox.

## Definition of DONE

- Finished result: one installable repo/release containing the complete launcher and a Solo runtime in which every playable card has a verified lifecycle.
- Final proof: CI integration simulation + package smoke + a local QLDS runtime self-test that requires a plugin readiness handshake.
- Who can perform/inspect final proof: CI/project agent; runtime self-test is automatic on the target machine and does not require the user to play a match.

## Boundaries

- In scope: launcher shell, native Arcade, 15 Solo modes, map recommendations/sync, movement, setup, diagnostics, packaging.
- Not doing: public multiplayer server product, new AI/nav engine, destructive user-config edits.
- Effort limit: change architecture rather than continue patching if a core QLDS/shinqlx contract is disproven.
- Highest-risk unknown: live QLDS lifecycle behavior that cannot be reproduced in generic CI.

## Backward plan

1. Immediately before DONE: release package/install smoke passes, all advertised modes pass lifecycle simulations, and runtime self-test validates QLDS + shinqlx + plugin readiness.
2. Before that: all mode implementations use the corrected common controller, map transitions are recoverable, movement/config changes are reversible, and diagnostics expose exact failures.
3. Before that: full v4.11 product shell is restored to the repo and the v5 runtime contract blockers are removed with regression tests.
4. Current state: UI/product shell and v5 server work exist in separate lines; v5 tests are too idealized and the repo is not installable.

## Mission meeting

- Required: YES
- Questions to settle: What is the product boundary? What must be proven without asking the operator to manually play-test? Which engine owns teams, deaths, waves, and map changes? What happens to modes that cannot meet the proof bar?
- Assumptions accepted/rejected:
  - ACCEPT: preserve v4.11 launcher/UI/map work rather than rewrite it.
  - ACCEPT: plugin owns Solo objectives; Quake owns combat/navigation.
  - ACCEPT: automated local runtime self-test is part of the product because generic CI cannot launch the Steam QLDS stack.
  - REJECT: UDP-listening alone is server health.
  - REJECT: FFA is an acceptable multi-bot Horde/Arena sandbox.
  - REJECT: passing pure tests is enough to label a mode working.
- Unresolved questions + owner: exact live QLDS behavior -> automated runtime self-test owned by project agent/product; no manual gameplay dependency.
- Operator decisions needed: none; prior product intent already requires offline single-player modes, movement improvements, recommendations, logging, and the full launcher experience.
- Roadmap changes: merge the product shell and server rebuild before adding more features; make integration simulation and readiness handshake first-class release gates.
- First wave selected: QLL-001, QLL-002, QLL-003, QLL-004.

## First wave

- [ ] `QLL-001` — Restore the complete v4.11 launcher/product shell into `v5-alpha` without regressing map scanning, recommendations, native Arcade, setup UI, movement options, or diagnostics — Owner: project agent
- [ ] `QLL-002` — Correct the shared Solo runtime contract: package imports, plugin-ready handshake, one-human training state, red-vs-blue sandbox, objective spawn accounting, safe entity handling, and failure states — Owner: project agent
- [ ] `QLL-003` — Add a fake-minqlx integration harness that imports the plugin the way minqlx does and executes adverse event orderings — Owner: project agent
- [ ] `QLL-004` — Prove Horde, Gun Game, and Arena Run end-to-end in the harness, including map transitions, upgrades, player death, stale callbacks, and side-thruster movement commands — Owner: project agent

## Phase 0 — Foundation
- [ ] Restore product shell and make repo installable.
- [ ] Fix common runtime contract and health handshake.
- [ ] Add realistic integration simulator and fail CI on lifecycle regressions.

## Phase 1 — Delivery
- [ ] Core playable slice: Horde, Gun Game, Arena Run.
- [ ] Port and verify remaining scripted modes against the same controller contract:
  - [ ] Boss Rush
  - [ ] Wipeout Solo
  - [ ] The Gauntlet
  - [ ] Last Stand
  - [ ] One Life
  - [ ] Bounty Hunt
  - [ ] Rocket Tag
  - [ ] Movement Hunter
  - [ ] Predator
  - [ ] Accuracy Trial
  - [ ] Speedrun Combat
  - [ ] Random Loadout
- [ ] Keep only runtime-backed Arena upgrades; make every description match real behavior.
- [ ] Preserve movement profile + ground dodge-hop / air thrust and reversible temporary binds.
- [ ] Restore Workshop/local PK3 synchronization and map-specific curated pools.

## Phase 2 — Integration and final proof
- [ ] Add `solo_engine/self_test.sh` to validate installed QLDS, shinqlx, plugin import/init, readiness handshake, and log failure reasons automatically.
- [ ] Make launcher Solo readiness depend on setup + self-test status rather than a stale READY file alone.
- [ ] Run full tests, shell/JSON validation, disposable-HOME install/uninstall smoke, and package reconstruction.
- [ ] Update README from alpha architecture notes to actual install/use/recovery instructions.
- [ ] Merge only after final proof gates pass; otherwise keep draft and record exact blocker.

## Checkpoints

- Checkpoint: after common runtime + core-mode integration harness passes.
- Evidence reviewed: package import test, early-kill spawn test, team ownership test, readiness handshake test, Horde/Gun Game/Arena scenario traces.
- Decision: CONTINUE if all pass; CHANGE if any upstream API assumption fails.
- Reason: remaining modes should not be ported onto an unproven lifecycle.
- Next action: port the other 12 modes using the proven common contract.
- Re-plan if: TDM/single-player behavior or hook semantics are contradicted by upstream/current runtime evidence.

- Checkpoint: after all advertised modes pass simulation and package smoke.
- Evidence reviewed: mode matrix, install smoke, diagnostics, runtime self-test implementation.
- Decision: CONTINUE to release if no mode is falsely advertised; otherwise CHANGE or CUT SCOPE by disabling the failing mode until fixed.
- Reason: user-visible honesty is part of DONE.
- Next action: release/merge.
- Re-plan if: automated runtime self-test cannot verify plugin initialization on the target stack.
