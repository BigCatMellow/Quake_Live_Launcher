# Roadmap: Quake Live Launcher v5

- State: WORKING

## Current reality

- Checked facts: `v5-alpha` now contains the complete installable launcher shell, resources, native Arcade definitions, map recommendation data, rebuilt Solo runtime, setup/self-test tooling, diagnostics, and the full integration test harness. The obsolete root `plugins/` and `qlds/` alpha copies have been removed.
- Evidence/source paths: `README.md`, `solo_engine/`, `resources/`, `tests/`, `docs/V4_11_AUDIT.md`, `docs/V5_ARCHITECTURE.md`, `docs/V5_DRY_RUN.md`, GitHub Actions run `33088372440` on commit `2fddd52771546e5177557d63b91eaceb307d2d1d`.
- Verified CI evidence: Python/payload compilation PASS; 40 unit + fake-minqlx lifecycle tests PASS; shell validation PASS; resource JSON validation PASS; disposable-HOME install/uninstall smoke PASS.
- Important remaining assumption: the real Steam Quake Live Dedicated Server + shinqlx combination behaves consistently with the upstream APIs and the fake-minqlx contract. Generic GitHub CI cannot execute Steam's `qzeroded.x64`; the shipped `solo_engine/self_test.sh` is the target-machine proof for that boundary.

## Definition of DONE

- Finished result: one installable repo/release containing the complete launcher and a Solo runtime in which every visible playable card has a real implementation and verified lifecycle.
- Final proof:
  1. full GitHub CI product workflow passes;
  2. every advertised Solo mode passes lifecycle simulation, including adverse event orderings;
  3. package/install/uninstall smoke passes;
  4. installed target runs `solo_engine/self_test.sh`, which launches real QLDS + shinqlx + `solo_arcade` and requires the plugin readiness handshake.
- Who can perform/inspect final proof: CI/project agent for repository proof; target-machine self-test is automated and does not require manual gameplay.

## Boundaries

- In scope: launcher shell, native Arcade, 15 Solo modes, map recommendations/sync, movement, setup, diagnostics, packaging.
- Not doing: public multiplayer server product, new AI/navigation engine, destructive user-config edits.
- Effort limit: change architecture rather than continue patching if a core QLDS/shinqlx contract is disproven.
- Highest-risk unknown: live QLDS/shinqlx runtime behavior outside generic CI.

## Backward plan

1. Immediately before DONE: target-machine QLDS self-test passes against the same committed product that passed CI.
2. Before that: repository is converged, all visible modes pass lifecycle simulation, package smoke passes, and startup health requires a plugin handshake rather than only a listening socket. **VERIFIED.**
3. Before that: common Solo lifecycle, teams, spawn accounting, map resume, entity handling, movement and failure-state contracts are regression-tested. **VERIFIED.**
4. Prior state: v4 product shell and v5 server work were separate; integration tests were too idealized. **RESOLVED.**

## Mission meeting outcome

- Product boundary: one-user Linux launcher for offline/single-player Quake Live against bots.
- Engine boundary: Quake Live owns physics, navigation and weapon combat; `solo_arcade` owns scripted enemies, objectives, progression, lives, bosses, map-stage transitions and completion.
- Health boundary: UDP listening is insufficient; plugin readiness JSON is required.
- Team boundary: scripted combat uses TDM as an invisible sandbox, human RED, scripted enemies BLUE, friendly fire off, score/time limits disabled.
- Verification boundary: modes that cannot meet the lifecycle proof bar must be fixed or hidden; pure unit tests alone do not qualify a mode as working.
- Manual gameplay dependency: rejected. The remaining environment-specific proof is an automated installed-runtime self-test.

## First wave

- [x] `QLL-001` — Restore the complete launcher/product shell into `v5-alpha` without regressing map scanning, recommendations, native Arcade, setup UI, movement options or diagnostics.
- [x] `QLL-002` — Correct the shared Solo runtime contract: package imports, plugin-ready handshake, single-player state, red-vs-blue sandbox, spawn accounting, safe entity handling and failure states.
- [x] `QLL-003` — Add a fake-minqlx integration harness using the runtime-style `minqlx-plugins` package and adverse event orderings.
- [x] `QLL-004` — Prove Horde, Gun Game and Arena Run end-to-end in the harness, including map transitions, upgrades, player death, early kills and movement.

## Phase 0 — Foundation

- [x] Restore product shell and make repo installable.
- [x] Fix common runtime contract and plugin health handshake.
- [x] Separate intended spawn fulfillment from currently living enemy IDs.
- [x] Replace FFA scripted waves with RED-vs-BLUE TDM sandbox.
- [x] Add realistic integration simulator and fail CI on lifecycle regressions.

## Phase 1 — Delivery

- [x] Horde
- [x] Gun Game
- [x] Arena Run
- [x] Boss Rush
- [x] Wipeout Solo — now has a real five-round victory state.
- [x] The Gauntlet — ten-stage terminal state.
- [x] Last Stand
- [x] One Life
- [x] Bounty Hunt
- [x] Rocket Tag
- [x] Movement Hunter
- [x] Predator
- [x] Accuracy Trial
- [x] Speedrun Combat
- [x] Random Loadout
- [x] Keep only runtime-backed Arena upgrades and make descriptions match actual behavior.
- [x] Preserve ground dodge-hop / air thrust and reversible temporary binds.
- [x] Preserve Workshop/local PK3 sync and curated mode/map pools.

## Phase 2 — Integration and final proof

- [x] Add `solo_engine/self_test.sh` to launch real QLDS/shinqlx/plugin on a test port and require `plugin_ready.json`.
- [x] Make normal Solo startup require QLDS process + requested socket + plugin readiness + matching requested mode.
- [x] Run full tests, shell/JSON validation and disposable-HOME install/uninstall smoke locally.
- [x] Run the same full-product workflow in GitHub Actions: run `33088372440` PASS.
- [x] Remove obsolete root alpha runtime copies and obsolete pre-convergence tests.
- [x] Update README to actual install/use/architecture/recovery behavior.
- [ ] Target-machine runtime proof: `SET UP / REPAIR SOLO ENGINE` must complete `self_test.sh` and create `SELF_TEST_OK` + `READY`.
- [ ] Release/merge only after the final target-runtime proof passes; otherwise record the exact blocker and keep PR draft.

## Checkpoints

### Checkpoint 1 — common lifecycle

- Evidence reviewed: package import, early-kill spawning, team ownership, readiness handshake, Horde/Gun Game/Arena traces.
- Decision: `CONTINUE`.
- Reason: common lifecycle passed and no upstream API assumption was contradicted by source inspection or simulation.

### Checkpoint 2 — all advertised modes + package smoke

- Evidence reviewed: 15-mode lifecycle matrix, terminal-state tests, movement simulation, shell/JSON validation, install/uninstall smoke.
- Decision: `CONTINUE`.
- Reason: all advertised scripted modes now have runtime implementations and simulated terminal behavior; no known falsely advertised mode remains.

### Checkpoint 3 — GitHub full-product CI

- Evidence reviewed: GitHub Actions run `33088372440`, commit `2fddd52771546e5177557d63b91eaceb307d2d1d`.
- Decision: `CONTINUE` to target-runtime release gate.
- Reason: every repository-level proof passed on a clean Ubuntu 24.04 runner, including 40 lifecycle/product tests and disposable-HOME packaging smoke.
- Next action: preserve PR as draft until the installed QLDS/shinqlx self-test passes on the target stack.
- Re-plan if: self-test cannot initialize real `qzeroded.x64`, shinqlx, or `solo_arcade`, or if the readiness handshake disagrees with the requested mode.
