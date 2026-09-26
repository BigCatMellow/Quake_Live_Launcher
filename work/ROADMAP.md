# Roadmap: Quake Live Launcher v5

- State: WORKING / LIVE QUALITY VALIDATION

## Current reality

- `v5-alpha` is the complete installable launcher/product branch.
- Real Linux Mint gameplay proved that QLDS + shinqlx + plugin readiness can succeed while encounter quality is still wrong; live gameplay quality is therefore a required release gate rather than post-release polish.
- Real play also exposed a one-player forfeit race and the cost of restarting Quake between scripted modes. The runtime now treats single-player training state as a continuously enforced invariant and supports in-place scripted-Solo match switching over one persistent QLDS/client connection.
- The shared lifecycle fixes, startup-death guard, team sandbox, combat-ready bots, package verification and installed runtime gate are retained.
- The Encounter Director is now the single production Solo encounter brain. `solo_directed` is the live compatibility/control entrypoint over `solo_arcade` + `DirectorRuntime` + `DirectorLearning` and now also owns the persistent match-handoff bridge.
- Director learning records hypotheses/actions/outcomes, adapts bounded pressure during the current encounter, persists a decaying per-mode player model, and learns role/composition/action results across sessions.
- Latest product evidence: hot-load/anti-forfeit code commit `3826a401e11519988b94979363c18fc7c6a3c3c2`; GitHub Actions run `33192578550` PASS; **116 tests PASS**; install/import/uninstall PASS; archive/source equality PASS for 39 shipped files; artifact `9694367525`.

## Definition of DONE

DONE requires all of the following:

1. complete installable launcher and all visible modes have implemented lifecycle behavior;
2. full product CI, install/import/uninstall and archive/source equality pass;
3. real target QLDS/shinqlx initialization passes;
4. scripted Solo one-player training state survives initial map load and subsequent map/mode transitions without forfeiture;
5. after the first scripted Solo launch, another scripted Solo mode can be selected without closing/reopening the Quake client;
6. every scripted Solo mode has an explicit Director profile;
7. Director fairness laws are enforced by code/tests;
8. Director decisions, executions and later outcomes are auditable;
9. persistent learning is bounded, decays, survives corrupt/missing memory, and never gains authority over aim or hidden damage;
10. representative live modes demonstrate engaged, mode-appropriate bots without obvious cheating, inert wandering, accidental instant endings or unfair pressure spikes;
11. live sessions show the learning loop improving encounter quality rather than obvious rubber-banding or overfitting.

A successful readiness handshake or green CI alone is not sufficient evidence of DONE.

## Product boundaries

- One-user Linux/offline Quake Live launcher.
- Quake Live owns navigation, aim, firing, movement physics, weapons and collision.
- `solo_arcade` owns mode lifecycle and scripted objectives.
- `solo_directed` owns the live scripted-Solo control boundary: training-state reinforcement, file-backed match handoff and Director diagnostics.
- `SoloDirector` owns deterministic encounter observation/decision policy.
- `DirectorRuntime` owns the Quake-facing observation/action bridge and explicit action audit.
- `DirectorLearning` owns bounded live feedback, persistent player tendencies and tactical playbook evidence.
- Director does not directly aim, fire, path, teleport into immediate danger, or secretly multiply damage.
- In-place hot-load is currently a scripted-Solo capability. Native Arcade/Quick Play still use their existing client-start/factory path and are not falsely advertised as hot-loadable.

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
- [x] Treat `allow_single_player(True)` as a live-level invariant rather than a constructor-only action.
- [x] Request training mode before initial map and reassert on new-game/map/player/frame boundaries.
- [x] Add `match_request.json` / `match_status.json` scripted-Solo hot-load protocol.
- [x] Preserve the same connected human through Horde -> Gun Game simulated in-place handoff.

## Persistent scripted-Solo session — IMPLEMENTED, LIVE VALIDATION REQUIRED

See `work/HOTLOAD_CHECKPOINT.md`.

### Anti-forfeit contract

- [x] QLDS requests `g_training 1` before the initial `+map` and in server.cfg.
- [x] `solo_directed` reasserts shinqlx single-player allowance on new game, map, player load and player spawn.
- [x] first eligible live frame after map load reasserts the actual CurrentLevel training flag; low-frequency reassertion continues while the server is alive.
- [x] regression deliberately clears the fake training flag during ACTIVE Horde and proves the next frame restores it without ending the objective.
- [ ] Real Mint confirmation: Horde no longer forfeits immediately.

### In-place match handoff

- [x] launcher recognizes a healthy running Solo server and writes a uniquely identified match request rather than starting another QLDS/client process;
- [x] old Director session is finalized as `switched` before state reset;
- [x] objective-owned bots are cleared without scoring/progression side effects;
- [x] mode/controller/Director/movement/session state is rebuilt from the newly written normal Solo session;
- [x] human client ID/team are retained;
- [x] server changes to requested map and client follows the existing connection;
- [x] launcher waits for explicit matching request status + plugin readiness rather than assuming success;
- [x] simulated active Horde -> Gun Game reaches ACTIVE Gun Game with the same connected human;
- [ ] Real Mint confirmation: sequential scripted-Solo selections switch the running match without closing Quake;
- [ ] determine separately whether native Arcade should migrate onto the persistent server model.

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

- [ ] Horde — no forfeit + hunting/contact quality + evidence that failed Director moves self-correct;
- [ ] Arena Run — build/theme fairness without hard-counter learning;
- [ ] Gun Game — hot-load continuity + continuous reachable fights and recovery-window quality;
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
- Evidence: `DirectorLearning`, unified `DirectorRuntime`, explicit action audit, persistent player/playbook files, same-encounter feedback correction and complete product CI.
- Challenge result: learning authority is intentionally limited to pressure target, legal role choice, replacement choice and reinforcement timing. It cannot mutate native aim/skill during live correction and cannot override authored mode contracts.

### Runtime Checkpoint D — anti-forfeit + hot-load

- Decision: CONTINUE TO REAL MINT VALIDATION.
- Evidence: `work/HOTLOAD_CHECKPOINT.md`; product commit `3826a401e11519988b94979363c18fc7c6a3c3c2`; GitHub Actions run `33192578550` PASS; **116 tests PASS**; installed launcher smoke PASS; archive/source equality PASS; artifact `9694367525`.
- Challenge result: keeping one QLDS alive is preferred to restarting the server under an already-running client. Scripted Solo is in scope; native Arcade remains explicitly outside this hot-load claim.
- Remaining uncertainty: actual Quake Live client/server map transition and real forfeit state can only be closed by target play.

## Immediate next action

Install the hot-load/anti-forfeit build on Mint. Close any Quake/old Solo server once so the new plugin is definitely loaded. Start Horde and verify it remains playable. While Quake stays open, return to the launcher and start Gun Game (then another scripted Solo mode) and confirm the running client follows the new match. Preserve Director logs/memory if gameplay quality is wrong.
