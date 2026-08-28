# Roadmap: Quake Live Launcher v5

- State: WORKING

## Current reality

- `v5-alpha` is the complete installable launcher/product branch.
- Real Linux Mint gameplay proved that QLDS + shinqlx + plugin readiness can succeed while encounter quality is still wrong; live gameplay quality is therefore a required release gate rather than post-release polish.
- The shared lifecycle fixes, startup-death guard, team sandbox, combat-ready bots, package verification and installed runtime gate are retained.
- The Encounter Director is now integrated into the production Solo runtime.
- Evidence: production Director integration commit `9c055e8d7046ef9b723149c392f670463cc3f271`; clean product CI run `33173627036` PASS on `eb97f1ade16f1bd77ba25d69e050aaab95b9fb8e`; artifact `9686633905`; detailed checkpoint `work/DIRECTOR_CHECKPOINT.md`.

## Definition of DONE

DONE requires all of the following:

1. complete installable launcher and all visible modes have implemented lifecycle behavior;
2. full product CI, install/import/uninstall and archive/source equality pass;
3. real target QLDS/shinqlx initialization passes;
4. every scripted Solo mode has an explicit Director profile;
5. Director fairness laws are enforced by code/tests;
6. representative live modes demonstrate engaged, mode-appropriate bots without obvious cheating, inert wandering, accidental instant endings or unfair pressure spikes;
7. telemetry can explain Director interventions and provide evidence for tuning.

A successful readiness handshake alone is not sufficient evidence of DONE.

## Product boundaries

- One-user Linux/offline Quake Live launcher.
- Quake Live owns navigation, aim, firing, movement physics, weapons and collision.
- `solo_arcade` owns mode lifecycle and scripted objectives.
- Encounter Director owns encounter composition, pressure observation, role assignment, reinforcement pacing and explicitly permitted recovery actions.
- Director does not directly aim, fire, path, or secretly multiply damage.

## Core product phases — completed foundation

- [x] Restore launcher, resources, map scanning/recommendations and installer.
- [x] Replace bootstrap-player workarounds with native single-player contract.
- [x] Use hidden RED-human vs BLUE-enemy team sandbox.
- [x] Separate intended spawns from living enemy ownership.
- [x] Guard delayed callbacks with lifecycle generations.
- [x] Require plugin readiness rather than UDP-only health.
- [x] Correct startup/team-transition death race.
- [x] Give scripted bots explicit combat-ready loadouts and suitable engine bot cvars.
- [x] Maintain 15-mode fake-minqlx lifecycle matrix.

## Encounter Director roadmap

See `docs/DIRECTOR_DESIGN.md` for the governing design and fairness laws.

### D0 — Observe and explain — IMPLEMENTED

- [x] Pure deterministic mode-aware Director core in `solo_engine/plugins/solo_director.py`.
- [x] Live adapter in `solo_engine/plugins/director_runtime.py`.
- [x] Low-frequency encounter observation rather than per-frame decision churn.
- [x] Measure pressure, alive/engaged/idle/far enemies, human health/armor and recent damage exchange.
- [x] Append explainable telemetry to `solo_runtime/director.jsonl` with bounded rotation.
- [x] Include compact Director state in `!run` diagnostics.
- [x] Tests for pressure, severe-damage recovery window, recent contact and profile validity.

### D1 — Recover non-contributing bots — IMPLEMENTED, NARROW AUTHORITY

Active recovery currently allowed only in:

- [x] Horde
- [x] Gun Game
- [x] Speedrun Combat

Safety contract:

- [x] objective must be ACTIVE;
- [x] total pressure must be below target floor;
- [x] candidate must be far and idle beyond mode threshold;
- [x] candidate cannot already have taken player damage;
- [x] recovery cooldown prevents churn;
- [x] remove objective ownership before kick/replacement so recovery never counts as progression;
- [x] regression test proves Horde remains ACTIVE and enemy count returns to expected population.

Not implemented: teleporting bots onto/near the player.

### D2 — Role composition — INITIAL IMPLEMENTATION

Every scripted mode has a Director profile and seeded role mix.

- [x] Chaser
- [x] Gunner
- [x] Marksman
- [x] Bruiser
- [x] Skirmisher
- [x] Berserker
- [x] Target
- [x] Boss

Roles currently influence bounded loadout/health/armor identity while native Quake AI still performs actual combat behavior.

- [x] role selection reproducible from seed/objective/spawn order;
- [x] Marksman weighting kept low in pressure-heavy profiles;
- [x] boss/trial-specific existing loadouts override generic role loadout where appropriate;
- [x] runtime regression verifies Horde bots receive a valid role and matching armed loadout.

### D3 — Adaptive pressure budget — NEXT

Build only after D0 telemetry from real gameplay is reviewed.

Planned authority:

- [ ] tune replacement/reinforcement timing around pressure band;
- [ ] bounded simultaneous-engager targets;
- [ ] recovery windows after severe incoming damage;
- [ ] composition adjustments between objectives/waves, not abrupt mid-firefight counter-picks;
- [ ] performance trend smoothing across multiple encounters rather than reaction to one kill/death;
- [ ] difficulty presets primarily alter encounter-management bounds before native skill.

Explicitly prohibited for D3:

- hidden dynamic damage buffs;
- direct aim/fire control;
- hostile teleport into immediate danger;
- replacing damaged enemies to restore health;
- instant counter-picking of Arena upgrades/weapons.

### D4 — Mode-specific Director identities

- [x] Horde — Hunt profile exists
- [x] Arena Run — Roguelite profile exists
- [x] Gun Game — Flow profile exists
- [x] Boss Rush — Duel profile exists
- [x] Wipeout — Squad profile exists
- [x] Gauntlet — Trial profile exists
- [x] Last Stand — Siege profile exists
- [x] One Life — Tension profile exists
- [x] Bounty Hunt — Escort profile exists
- [x] Rocket Tag — Chase profile exists
- [x] Movement Hunter — Pursuit profile exists
- [x] Predator — Swarm profile exists
- [x] Accuracy Trial — Target profile exists
- [x] Speedrun Combat — Feed profile exists
- [x] Random Loadout — Improvisation profile exists
- [ ] tune each profile from telemetry/live evidence rather than fixed guesses.

### D5 — Difficulty profiles

- [x] Easy/Normal/Hard/Nightmare pressure-profile structure exists.
- [x] tests verify encounter pressure changes before native bot skill.
- [ ] calibrate target pressure bands from real sessions.
- [ ] expose useful Director difficulty explanation in UI/diagnostics.

### D6 — Live quality gate

Representative live families:

- [ ] Horde — hunting/contact quality
- [ ] Arena Run — build/theme fairness
- [ ] Gun Game — continuous reachable fights
- [ ] Boss Rush — distinct readable duel pressure
- [ ] Movement Hunter — pursuit without laser aim
- [ ] Accuracy Trial — useful targets without excessive lethality

Then sample remaining profiles before merge.

## MAPS checkpoints

### Director Checkpoint A — architecture

- Decision: CONTINUE.
- Evidence: Director design/fairness laws and all 15 profiles defined.

### Director Checkpoint B — D0/D1/D2 integration

- Decision: CONTINUE.
- Evidence: `solo_director.py`, `director_runtime.py`, production `solo_arcade.py` integration, Director pure tests, runtime recovery tests, clean full-product CI run `33173627036` PASS.
- Reason: observation, explainability, role identity and the safest active recovery behavior work without weakening existing mode lifecycle tests.
- Constraint: D3 authority remains intentionally limited until real `director.jsonl` evidence is reviewed.

## Immediate next action

Run the Director build on real Mint, especially Horde. Collect `~/.local/share/quake-live-launcher/solo_runtime/director.jsonl` along with the normal Solo log. Use that evidence to calibrate D3 pressure bands and engagement thresholds rather than increasing bot skill blindly.
