# Roadmap: Quake Live Launcher v5

- State: WORKING / LIVE QUALITY VALIDATION

## Current reality

- `v5-alpha` is the complete installable launcher/product branch.
- Real Linux Mint gameplay proved that QLDS + shinqlx + plugin readiness can succeed while encounter quality is still wrong; live gameplay quality is therefore a required release gate rather than post-release polish.
- The shared lifecycle fixes, startup-death guard, team sandbox, combat-ready bots, package verification and installed runtime gate are retained.
- The Encounter Director is now the single production Solo encounter brain. The old duplicate `solo_directed` Director implementation was removed; `solo_directed` is only a compatibility entrypoint over `solo_arcade` + `DirectorRuntime` + `DirectorLearning`.
- Director learning now records hypotheses/actions/outcomes, adapts bounded pressure during the current encounter, persists a decaying per-mode player model, and learns role/composition/action results across sessions.
- Evidence: Director live-adaptation product commit `21517ae69e1defbdb75f2036da27c6c0f3c446ea`; GitHub Actions run `33177798202` PASS; **99 tests PASS**; archive/source equality PASS for 39 shipped files.

## Definition of DONE

DONE requires all of the following:

1. complete installable launcher and all visible modes have implemented lifecycle behavior;
2. full product CI, install/import/uninstall and archive/source equality pass;
3. real target QLDS/shinqlx initialization passes;
4. every scripted Solo mode has an explicit Director profile;
5. Director fairness laws are enforced by code/tests;
6. Director decisions, executions and later outcomes are auditable;
7. persistent learning is bounded, decays, survives corrupt/missing memory, and never gains authority over aim or hidden damage;
8. representative live modes demonstrate engaged, mode-appropriate bots without obvious cheating, inert wandering, accidental instant endings or unfair pressure spikes;
9. live sessions show the learning loop improving encounter quality rather than obvious rubber-banding or overfitting.

A successful readiness handshake or green CI alone is not sufficient evidence of DONE.

## Product boundaries

- One-user Linux/offline Quake Live launcher.
- Quake Live owns navigation, aim, firing, movement physics, weapons and collision.
- `solo_arcade` owns mode lifecycle and scripted objectives.
- `SoloDirector` owns deterministic encounter observation/decision policy.
- `DirectorRuntime` owns the Quake-facing observation/action bridge and explicit action audit.
- `DirectorLearning` owns bounded live feedback, persistent player tendencies and tactical playbook evidence.
- Director does not directly aim, fire, path, teleport into immediate danger, or secretly multiply damage.

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

See `docs/DIRECTOR_DESIGN.md` and `docs/DIRECTOR_LEARNING.md`.

### D0 — Observe and explain — IMPLEMENTED

- [x] Pure deterministic mode-aware Director core in `solo_engine/plugins/solo_director.py`.
- [x] Live adapter in `solo_engine/plugins/director_runtime.py`.
- [x] Low-frequency encounter observation rather than per-frame decision churn.
- [x] Measure pressure, alive/engaged/idle/far enemies, human health/armor and recent damage exchange.
- [x] Append explainable telemetry to `solo_runtime/director.jsonl` with bounded rotation.
- [x] Include compact Director state in `!run` / `!director` diagnostics.

### D1 — Recover non-contributing bots — IMPLEMENTED, NARROW AUTHORITY

Active recovery currently allowed only in Horde, Gun Game and Speedrun Combat.

- [x] objective must be ACTIVE;
- [x] total pressure must be below target floor;
- [x] candidate must be far and idle beyond mode threshold;
- [x] candidate cannot already have taken player damage;
- [x] recovery cooldown prevents churn;
- [x] remove objective ownership before kick/replacement so recovery never counts as progression;
- [x] action result records whether replacement was actually scheduled/spawned;
- [x] regression proves Horde remains ACTIVE and population returns to expected count.

Teleport recovery remains unimplemented.

### D2 — Role composition — IMPLEMENTED FOUNDATION

- [x] Chaser, Gunner, Marksman, Bruiser, Skirmisher, Berserker, Target and Boss roles.
- [x] every scripted mode has seeded permitted role mix;
- [x] authored boss/trial/rocket-only contracts override generic role selection;
- [x] role effectiveness records damage dealt/received and survival outcome;
- [x] learned role selection cannot leave the mode's allowed role set;
- [x] hard composition caps prevent learned Marksman/Gunner/Berserker spam.

### D3 — Feedback loop — IMPLEMENTED

Every meaningful Director intervention is treated as a small experiment.

- [x] record decision/hypothesis, reason, pressure baseline, target band and composition;
- [x] record execution result including skipped/failed actions;
- [x] evaluate outcome after a bounded observation window;
- [x] score whether pressure moved toward the target without creating danger;
- [x] persist action success/score history in `director_playbook.json`;
- [x] explicit audit stream in `director_actions.jsonl`;
- [x] raw session evidence in `director_sessions/*.jsonl`.

### D4 — Live adaptation — INITIAL IMPLEMENTATION

The Director can now correct itself while the match is still running.

- [x] severe/high pressure delays future reinforcements;
- [x] very low pressure can modestly shorten future reinforcement delay;
- [x] dangerous evaluated intervention creates a small session-only negative pressure bias;
- [x] ineffective low-pressure intervention creates a small positive pressure bias;
- [x] successful interventions decay the temporary bias toward neutral;
- [x] live correction immediately refreshes the Director's pressure target for subsequent decisions in the same encounter;
- [x] live correction is capped and tests prove it does not alter native skill or authored engager cap.

Still intentionally deferred:

- [ ] direct mid-fight population increases beyond existing mode population contract;
- [ ] dynamic teleport/relocation;
- [ ] aim/accuracy modification;
- [ ] hidden damage modification.

### D5 — Persistent player model — IMPLEMENTED, BOUNDED

Stored at `solo_runtime/director_player.json`.

- [x] per-mode objective/session counts;
- [x] pressure, low/high-pressure fractions and danger trend EMAs;
- [x] damage dealt/taken trend EMAs;
- [x] objective/session-duration trends;
- [x] kill/death session trends;
- [x] persistent pressure shift only activates after repeated objective evidence;
- [x] persistent shift capped to ±6 pressure points;
- [x] old pressure preference decays toward neutral on later sessions;
- [x] missing/corrupt model safely falls back to defaults.

### D6 — Tactical playbook learning — IMPLEMENTED FOUNDATION

Stored at `solo_runtime/director_playbook.json`.

- [x] role attempts/effectiveness;
- [x] counted composition outcomes rather than unordered unique-role sets;
- [x] action attempts/success/score trends;
- [x] confidence thresholds before learned evidence can override authored base role;
- [x] hard role caps remain above learned preferences;
- [x] special Boss/Target contracts cannot be replaced by learned generic roles.

### D7 — Strategic Director — INITIAL IMPLEMENTATION

The Director can now use player + current encounter + past playbook evidence to choose among legal moves.

- [x] under-pressure/over-pressure context influences future role choice;
- [x] proven role effectiveness can influence future legal role assignment after sufficient evidence;
- [x] proven composition quality can influence future legal role assignment after sufficient evidence;
- [x] learned preference must exceed an explicit confidence/advantage threshold before overriding seeded base role;
- [x] one authoritative Director brain is used by live QLDS.

Next strategic extensions must be earned by live evidence, not added automatically.

### D8 — Difficulty personalization — PARTIAL

- [x] Easy/Normal/Hard/Nightmare define outer pressure/skill safety envelopes.
- [x] tests verify encounter pressure changes before native bot skill.
- [x] learned pressure adjustment stays inside a small bounded offset within the selected difficulty.
- [ ] calibrate target pressure bands from real sessions.
- [ ] expose useful learned tendencies in launcher diagnostics/UI.

### D9 — Mode specialization — PROFILE FOUNDATION IMPLEMENTED

All 15 profiles exist: Hunt, Roguelite, Flow, Duel, Squad, Trial, Siege, Tension, Escort, Chase, Pursuit, Swarm, Target, Feed and Improvisation.

- [ ] tune each mode's playbook/pressure interpretation from real telemetry;
- [ ] determine which additional modes may safely receive active idle recovery;
- [ ] prove each specialized Director preserves that mode's intended fantasy.

### D10 — Live quality gate — NEXT

Representative live families:

- [ ] Horde — hunting/contact quality + evidence that failed Director moves self-correct;
- [ ] Arena Run — build/theme fairness without hard-counter learning;
- [ ] Gun Game — continuous reachable fights and recovery-window quality;
- [ ] Boss Rush — distinct readable duel pressure;
- [ ] Movement Hunter — pursuit without laser aim;
- [ ] Accuracy Trial — useful targets without excessive lethality.

Then sample remaining profiles before merge.

## MAPS checkpoints

### Director Checkpoint A — architecture

- Decision: CONTINUE.
- Evidence: Director design/fairness laws and all 15 profiles defined.

### Director Checkpoint B — D0/D1/D2 integration

- Decision: CONTINUE.
- Evidence: observation, role identity and safe idle recovery passed production/fake-minqlx tests.

### Director Checkpoint C — learning loop

- Decision: CONTINUE TO LIVE QUALITY VALIDATION.
- Evidence: `DirectorLearning`, unified `DirectorRuntime`, explicit action audit, persistent player/playbook files, same-encounter feedback correction, 99-test suite, GitHub Actions run `33177798202` PASS, install/package/source-equality PASS.
- Challenge result: learning authority is intentionally limited to pressure target, legal role choice, replacement choice and reinforcement timing. It cannot mutate native aim/skill during live correction and cannot override authored mode contracts.
- Remaining uncertainty: whether the learned pressure/composition signals correlate with *fun* on real Quake maps. That cannot be established by fake-minqlx alone.

## Immediate next action

Run this learning build on real Mint. Start with Horde, then Gun Game and one contrasting mode such as Boss Rush. Preserve:

- `solo_runtime/director.jsonl`
- `solo_runtime/director_actions.jsonl`
- `solo_runtime/director_player.json`
- `solo_runtime/director_playbook.json`
- latest file in `solo_runtime/director_sessions/`
- normal Solo server log

Use those artifacts plus subjective play quality to tune D8/D9. Do not increase native bot skill merely because a pressure metric is low.
