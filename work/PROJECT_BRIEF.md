# Project Brief: Quake Live Launcher v5

- Owner: project agent, with repository owner as operator/playtest authority
- Status: ACTIVE
- Goal: deliver an installable Linux-first Quake Live launcher whose native Arcade modes and scripted Solo modes reliably work for one human and whose bots create fun, mode-appropriate, adaptively paced encounters without cheating.
- User/operator: Linux Mint Quake Live player focused on offline/single-player play.

## Product promise

The launcher should make Quake Live feel intentionally designed for solo play rather than merely launching multiplayer modes with bots.

For scripted Solo modes:

- Quake Live owns physics, navigation, aiming, shooting, weapons and collision.
- The Solo state machine owns objectives, progression and completion.
- A common Director **plays the opposing pieces against the human** by managing encounter composition, engagement pressure, bot roles/loadouts, spawn/replacement timing, idle recovery and recovery windows.
- The Director does not aim or fire for bots and does not use hidden superhuman difficulty cheats.

## Current reality

- The full launcher, setup, native Arcade, map systems and 15 scripted Solo modes are integrated on `v5-alpha`.
- CI proves compilation, simulated lifecycle behavior, install/import/uninstall, packaging, source/archive equality and executable modes.
- Real target QLDS/shinqlx startup is proven far enough to launch and fight scripted bots.
- First real gameplay revealed two gaps that automated readiness did not catch: Horde bots felt passive and several other modes appeared to terminate immediately after starting.
- First live repair is committed and green in GitHub Actions run `33126587095` with 57 tests: pre-ACTIVE human deaths are ignored and scripted bots receive combat-ready loadouts/aggressive baseline cvars.
- That repair still needs target confirmation.
- Director design/roadmap is now formalized in `docs/DIRECTOR_DESIGN.md` and `work/ROADMAP.md`.

## Definition of DONE

A release is DONE only when:

- fresh install works on the target Linux family;
- every visible scripted mode starts, progresses and terminates correctly in simulation and representative real gameplay;
- startup health proves QLDS + socket + shinqlx/plugin readiness;
- representative live play is required in addition to automated self-test;
- every scripted mode has an explicit Director profile or documented neutral profile;
- Director-managed bots remain engaged enough for the mode, while respecting fairness laws;
- difficulty presets primarily alter pressure/composition/pacing rather than hidden aim/damage;
- idle/non-contributing enemies are detected and safely recovered;
- Director actions cannot violate objective ownership, generation tokens, map transitions or terminal states;
- diagnostics can explain important Director interventions;
- package/repository CI stays green and the shipped archive equals verified source.

## Scope

### In scope

- Full launcher shell and resources.
- Native Arcade modes.
- 15 scripted Solo modes.
- Common Director framework and all mode profiles.
- Bot roles/loadouts, pressure pacing, idle recovery and difficulty calibration.
- Arena upgrades that have real runtime effects.
- Movement features.
- Map recommendations, local/Workshop sync.
- Logs, Director telemetry, diagnostics, installer/uninstaller and release packaging.

### Not doing

- Multiplayer matchmaking/server administration.
- Custom bot pathfinding/aim engine.
- Direct scripted aiming/firing.
- Destructive user config edits.
- Hidden adaptive damage/accuracy cheating.

## Quality laws

1. No mode is playable merely because Python compiles.
2. Automated readiness is not equivalent to gameplay correctness.
3. The objective controller remains the single lifecycle authority.
4. Director adaptation must be bounded and explainable.
5. Director difficulty changes encounter quality before raw bot power.
6. Delayed Director actions use generation/token guards.
7. Player configuration changes remain temporary/recoverable.
8. A mode or Director feature that cannot meet its proof bar is hidden/disabled rather than falsely advertised.

## Highest-risk unknowns

- How much useful engagement can be created around native Quake bot AI without custom pathfinding/aim control.
- Whether available position/damage/event signals are sufficient for a stable pressure estimator.
- Whether safe bot relocation can be proven fair; replacement-first recovery is preferred until then.
- Mode-specific Director tuning will require real gameplay feedback even after simulation passes.

## Decision path

The project agent may decide internal architecture, telemetry, test structure, pressure algorithms, role definitions and tuning mechanisms that obey the product/fairness contract.

Escalate to operator before:

- dropping a visible mode;
- changing primary platform;
- introducing paid/external runtime dependencies;
- weakening a fairness law;
- materially changing a mode's intended fantasy/goal.

## Planning authority

- Roadmap: `work/ROADMAP.md`
- Director design authority: `docs/DIRECTOR_DESIGN.md`
- Risks: `work/RISK_REGISTER.md`
- Current roadmap decision: stabilize the first live lifecycle repair, then execute Director D0 measurement-first.
