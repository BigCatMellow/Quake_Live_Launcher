# Roadmap: Quake Live Launcher v5

- State: WORKING
- Current MAPS decision: CHANGE, then CONTINUE
- Center of gravity: a one-player Quake Live launcher where every visible mode reliably starts, plays, ends correctly, and uses bots that create fun, mode-appropriate pressure without cheating.

## Current reality

- `v5-alpha` is a complete installable launcher with native Arcade, 15 scripted Solo modes, map scanning/sync, movement, setup, diagnostics and CI packaging.
- Repository/package proof is strong: compile, shell/JSON validation, install/import/uninstall smoke and archive/source equality pass in GitHub Actions.
- Real Linux Mint + QLDS + shinqlx execution is now proven far enough to launch and play scripted combat.
- Real gameplay disproved the previous assumption that plugin readiness/self-test was sufficient final proof:
  - Horde launched and was fightable, but bots felt too passive/disinterested.
  - multiple other scripted modes appeared to end immediately after starting.
- The first live repair is committed: pre-ACTIVE human deaths are ignored, scripted bots receive real combat loadouts, and bot AI cvars are tightened. Clean GitHub Actions run `33126587095` passes 57 tests, including regressions for both observed live symptoms.
- The live repair still requires target-machine confirmation.
- A new Director subsystem is now part of the product plan. Design authority: `docs/DIRECTOR_DESIGN.md`.

## Definition of DONE

A v5 release is DONE only when all of the following are true:

1. Fresh checkout/install succeeds on the target Linux family without requiring admin access for normal use.
2. Quick Play, Arcade, Solo and Custom Match remain intact.
3. Every visible scripted Solo mode reliably starts, reaches gameplay, progresses and reaches its intended terminal/failure state.
4. No mode can end because of a joining/team-transition death before gameplay becomes ACTIVE.
5. QLDS process + socket + shinqlx/plugin readiness are verified before client launch.
6. Repository/package CI passes compile, lifecycle simulation, install smoke, archive/source equality and executable-mode checks.
7. Real target-machine gameplay validates representative mode families; automated self-test alone is not sufficient.
8. Every scripted Solo mode has an explicit Director profile or a documented neutral profile.
9. Director difficulty is expressed primarily through encounter composition, pressure, engagement timing and recovery—not hidden aim/damage cheating.
10. Bots do not spend material portions of an active objective wandering, searching for disabled pickups, or failing to contribute without the Director detecting/recovering them.
11. Easy/Normal/Hard/Nightmare remain recognizably different while obeying the Director fairness laws in `docs/DIRECTOR_DESIGN.md`.
12. Diagnostics can explain Director interventions and the reason for them.
13. PR #1 remains draft until the target gameplay gates pass; merge/release happens only after the evidence supports DONE.

## Product boundaries

### In scope

- Linux-first one-human Quake Live launcher.
- Native Arcade modes.
- 15 scripted Solo modes.
- Mode-specific bot encounters managed by a common Director.
- Map recommendations/sync, movement, setup, diagnostics and packaging.

### Not doing

- Public multiplayer matchmaking/server administration.
- Replacing Quake Live navigation, aiming or shooting with a custom AI engine.
- Direct Director control of bot aim/fire.
- Hidden difficulty cheats that invalidate player skill.
- Destructive user configuration changes.

### Engine ownership

- Quake Live owns physics, navigation, aiming, shooting, weapons and collision.
- `solo_controller` owns objective lifecycle and enemy ownership.
- `solo_arcade` adapts Quake events to the mode runtime.
- The Director observes and recommends bounded encounter actions; it **does not become a second lifecycle state machine**.

## Mission meeting outcome — Director

The intended mental model is: **the Director is playing the opposing pieces against the human.**

It may decide:

- which bot roles make up an encounter;
- when replacements arrive;
- how many enemies should meaningfully pressure at once;
- what combat loadout a role receives;
- whether a bot is idle/non-contributing and needs recovery;
- when a player should receive a brief recovery window;
- how pressure should rise as a mode progresses;
- how each difficulty preset changes encounter pressure.

It may not:

- aim or shoot for a bot;
- grant impossible reactions;
- teleport a hostile bot directly into unavoidable danger;
- silently multiply damage because the player is doing well;
- hard-counter every successful player weapon/build;
- increase every difficulty axis at once.

Difficulty priority:

1. encounter quality;
2. pacing;
3. useful proximity;
4. role composition;
5. simultaneous pressure;
6. native bot skill/health/armor only within explicit caps.

## Backward plan from DONE

1. Immediately before release: representative live mode families pass target gameplay quality gates and no open high-severity Director/lifecycle risk remains.
2. Before that: all 15 Director profiles pass simulation and fairness-law tests; difficulty presets operate through pressure bands.
3. Before that: common Director pressure budget, telemetry, idle recovery, roles and recovery windows are proven in Horde/Gun Game/Speedrun.
4. Before that: the current live lifecycle repair is confirmed on Mint and all visible modes stop falsely terminating at startup.
5. Before that: repository/package/runtime bootstrap remains green and reproducible. **VERIFIED.**

## Immediate wave — stabilize live gameplay

- [x] `QLL-LIVE-001` — Capture first real target-machine result: QLDS/shinqlx launches; Horde plays; other modes can terminate immediately; Horde AI feels passive.
- [x] `QLL-LIVE-002` — Ignore human death events before objective phase ACTIVE.
- [x] `QLL-LIVE-003` — Give every scripted bot a combat-ready loadout when map pickups are disabled.
- [x] `QLL-LIVE-004` — Add regression tests for startup-death and Horde combat-readiness symptoms; clean CI run `33126587095` PASS with 57 tests.
- [ ] `QLL-LIVE-005` — Confirm on target Mint that Boss Rush/Wipeout/One Life/Arena no longer terminate at startup.
- [ ] `QLL-LIVE-006` — Confirm Horde bots now engage materially better; record remaining passivity as Director telemetry requirements rather than ad-hoc AI cvar tweaks.

## Director first wave

- [ ] `QLL-D001` — Add observation-only Director telemetry; no gameplay changes.
- [ ] `QLL-D002` — Add deterministic pressure model and diagnostics: meaningful-combat gaps, engaged estimate, player damage state, bot contribution/idle time.
- [ ] `QLL-D003` — Add idle/non-contributing recovery for Horde, Gun Game and Speedrun Combat.
- [ ] `QLL-D004` — Add role/loadout system: Chaser, Gunner, Marksman, Bruiser, Skirmisher, Berserker, Target/Boss.
- [ ] `QLL-D005` — Add pressure-budget pacing and recovery windows with fairness-law tests.
- [ ] `QLL-D006` — Convert difficulty presets from mostly skill/intensity values into pressure-band profiles.

## Director mode matrix

| Mode | Director profile | Primary job |
| --- | --- | --- |
| Horde | Hunt Director | Make a wave actively hunt while avoiding unfair dogpiles |
| Arena Run | Roguelite Director | Shape rounds around theme/build without hard-counter cheating |
| Gun Game | Flow Director | Keep reachable fights flowing through the weapon ladder |
| Boss Rush | Duel Director | Give bosses distinct pressure rhythms rather than raw stat inflation |
| Wipeout Solo | Squad Director | Coordinate a readable squad while preserving an achievable wipe window |
| Gauntlet | Trial Director | Make each weapon/stage produce its intended combat shape |
| Last Stand | Siege Director | Escalate pressure through composition/pacing |
| One Life | Tension Director | Keep one-life stakes high without lethal difficulty spikes |
| Bounty Hunt | Escort Director | Make the target reachable but meaningfully protected |
| Rocket Tag | Chase Director | Maintain mobile rocket-range pursuit |
| Movement Hunter | Pursuit Director | Force movement through pursuit, not perfect enemy accuracy |
| Predator | Swarm Director | Preserve power fantasy with intelligent swarm pressure |
| Accuracy Trial | Target Director | Supply useful moving targets with low incoming lethality |
| Speedrun Combat | Feed Director | Eliminate dead time so execution determines speed |
| Random Loadout | Improvisation Director | Create varied fights without counter-picking the random loadout |

Full behavior contract: `docs/DIRECTOR_DESIGN.md`.

## Director phases

### Phase D0 — Measure before adapting

- [ ] Implement low-frequency observation state.
- [ ] Log pressure inputs without changing behavior.
- [ ] Establish Horde/Gun Game/Speedrun baseline traces.

**Gate:** a passive Horde match can be explained in terms of contact gaps, idle contribution and distance rather than subjective guessing alone.

### Phase D1 — Engagement floor

- [ ] Detect idle/non-contributing bots.
- [ ] Recover through replacement first.
- [ ] Investigate relocation only if safe placement can be proven.
- [ ] Add per-profile preferred engagement/distance bands.

**Gate:** simulated and live Horde has materially less wandering, with no objective corruption or hostile teleport unfairness.

### Phase D2 — Roles and composition

- [ ] Implement shared bot role contracts.
- [ ] Add composition budgets and role caps.
- [ ] Progress Horde through role complexity before bot-skill inflation.
- [ ] Give Boss/Gauntlet/Accuracy explicit role rules.

**Gate:** a harder encounter can be generated while holding native bot skill constant.

### Phase D3 — Pressure budget

- [ ] Estimate current pressure.
- [ ] Define target pressure bands per mode/difficulty.
- [ ] Add recent-damage smoothing and short recovery windows.
- [ ] Change one major difficulty axis at a time.

**Gate:** dominant and struggling simulated players produce different pacing without hidden damage, aim or impossible reaction changes.

### Phase D4 — All-mode profiles

- [ ] Wire every scripted Solo mode to its profile.
- [ ] Add mode-specific dominant/struggling/idle/stale-callback tests.
- [ ] Preserve objective state machine as sole lifecycle owner.

**Gate:** every visible Solo mode has a tested Director contract.

### Phase D5 — Difficulty calibration

- [ ] Easy/Normal/Hard/Nightmare map to pressure targets and caps.
- [ ] Native bot skill becomes a bounded secondary knob.
- [ ] Diagnostics report average engaged count, combat gaps, intervention reasons and time outside pressure band.

**Gate:** difficulty levels feel distinct primarily because the encounter is managed differently—not because enemies secretly become superhuman.

### Phase D6 — Live quality gate

Representative live suite:

- [ ] Horde — engagement and wave pressure.
- [ ] Arena Run — build/theme adaptation and bosses.
- [ ] Gun Game — encounter flow.
- [ ] Boss Rush — readable duel pressure.
- [ ] Movement Hunter — pursuit without laser aim.
- [ ] Accuracy Trial — useful targets rather than lethal opponents.

Then sample remaining modes before release.

**Gate:** bots feel engaged, mode-appropriate and appropriately difficult without being overpowered; no fairness law is violated.

## Verification requirements

Every Director profile must simulate at least:

- dominant player streak;
- severe incoming damage burst;
- sustained low-health player;
- idle/non-contributing bot;
- extremely distant bot;
- enemy death during PREPARING;
- queued Director intervention across objective transition;
- map change with pressure state present;
- mode completion while an intervention is queued;
- each difficulty preset.

Every delayed Director action uses the same generation/token validity boundary as objective callbacks.

## Risk-driven checkpoints

### Checkpoint 1 — lifecycle architecture

- Decision: CONTINUE.
- Evidence: package import, ownership, early-kill spawning, team sandbox and generation guards pass simulation.

### Checkpoint 2 — repository/package

- Decision: CONTINUE.
- Evidence: installable product, archive/source equality and CI are green.

### Checkpoint 3 — automated runtime readiness

- Previous decision: CONTINUE to release.
- New decision: CHANGE.
- Evidence: real gameplay showed that successful readiness does not prove mode correctness or bot quality.
- Consequence: target gameplay quality is now a formal DONE gate.

### Checkpoint 4 — first live gameplay repair

- Evidence: live report + commit `062fb46a38fd08fcbba8a4b8bab251abf179ff10` + clean CI run `33126587095` with 57 passing tests.
- Decision: CONTINUE to target retest.
- Re-plan if: modes still terminate immediately or pre-ACTIVE death is not the live cause.

### Checkpoint 5 — Director observation

- Decision pending.
- CONTINUE if telemetry can explain engagement failures without invasive bot control.
- CHANGE if useful pressure cannot be inferred reliably from available QLDS/shinqlx signals.

### Checkpoint 6 — Director engagement

- Decision pending.
- CONTINUE if idle recovery and composition measurably improve contact without unfair interventions.
- CHANGE if Director intervention creates obvious teleporting, dogpiling, lifecycle corruption or rubber-banding.

## Release rule

PR #1 stays draft. The product is not DONE because QLDS starts or because CI is green. Release requires both software correctness **and** representative live gameplay evidence that the modes and their Director profiles deliver the intended one-player experience.
