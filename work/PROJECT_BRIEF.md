# Project Brief: Quake Live Launcher v5

- Owner: project agent, with repository owner as operator/playtest authority
- Status: ACTIVE — LIVE QUALITY VALIDATION
- Goal: deliver an installable Linux-first Quake Live launcher whose native Arcade modes and scripted Solo modes reliably work for one human and whose bots create fun, mode-appropriate, adaptively paced encounters without cheating.
- User/operator: Linux Mint Quake Live player focused on offline/single-player play.

## Product promise

The launcher should make Quake Live feel intentionally designed for solo play rather than merely launching multiplayer modes with bots.

For scripted Solo modes:

- Quake Live owns physics, navigation, aiming, shooting, weapons and collision.
- The Solo state machine owns objectives, progression and completion.
- A common Director **plays the opposing pieces against the human** by managing encounter composition, engagement pressure, bot roles/loadouts, spawn/replacement timing, idle recovery and recovery windows.
- The Director evaluates whether its own moves worked, corrects bounded mistakes during the current encounter, and carries forward decaying evidence about the player and useful tactics.
- The Director does not aim or fire for bots and does not use hidden superhuman difficulty cheats.

## Current reality

- The full launcher, setup, native Arcade, map systems and 15 scripted Solo modes are integrated on `v5-alpha`.
- CI proves compilation, simulated lifecycle behavior, install/import/uninstall, packaging, source/archive equality and executable modes.
- Real target QLDS/shinqlx startup is proven far enough to launch and fight scripted bots.
- First real gameplay revealed two gaps that automated readiness did not catch: Horde bots felt passive and several other modes appeared to terminate immediately after starting. Those lifecycle/loadout defects were repaired and regression-tested.
- Director D0-D7 foundations are now implemented: telemetry, narrow idle recovery, role identity, action feedback/evaluation, bounded same-encounter correction, persistent per-mode player memory, tactical playbook learning and confidence-gated legal role choice.
- The live runtime has one authoritative Director stack: `SoloDirector` + `DirectorRuntime` + `DirectorLearning`; `solo_directed` is only the compatibility plugin entrypoint.
- GitHub Actions run `33177798202` passed the full product gate with **99 tests**, install/import/uninstall smoke, archive/source equality and executable-mode checks.
- Real gameplay tuning is still required before release; green CI is not proof that the learned encounter policy feels good.

## Definition of DONE

A release is DONE only when:

- fresh install works on the target Linux family;
- every visible scripted mode starts, progresses and terminates correctly in simulation and representative real gameplay;
- startup health proves QLDS + socket + shinqlx/plugin readiness;
- representative live play is required in addition to automated self-test;
- every scripted mode has an explicit Director profile or documented neutral profile;
- Director-managed bots remain engaged enough for the mode while respecting fairness laws;
- difficulty presets primarily alter pressure/composition/pacing rather than hidden aim/damage;
- idle/non-contributing enemies are detected and safely recovered where active recovery is authorized;
- Director actions cannot violate objective ownership, generation tokens, map transitions or terminal states;
- every important Director intervention has decision, execution and outcome evidence;
- persistent learning is bounded, decaying, resilient to corrupt data and auditable;
- live sessions demonstrate that learning improves encounter quality rather than creating obvious rubber-banding, repetition or hard-counter behavior;
- package/repository CI stays green and the shipped archive equals verified source.

## Scope

### In scope

- Full launcher shell and resources.
- Native Arcade modes.
- 15 scripted Solo modes.
- Common Director framework and all mode profiles.
- Bot roles/loadouts, pressure pacing, idle recovery and difficulty calibration.
- Bounded current-session and cross-session Director learning.
- Persistent player model, tactical playbook, raw session evidence and Director action audit.
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
- Hostile teleporting or instant player-build counter-picking.

## Quality laws

1. No mode is playable merely because Python compiles.
2. Automated readiness is not equivalent to gameplay correctness.
3. The objective controller remains the single lifecycle authority.
4. Director adaptation must be bounded, explainable and reversible by fresh evidence.
5. Director difficulty changes encounter quality before raw bot power.
6. Delayed Director actions use lifecycle-safe ownership/generation contracts.
7. Persistent memory never outranks authored mode contracts or hard fairness limits.
8. A dangerous Director experiment must cause the Director to back off rather than double down.
9. Player configuration changes remain temporary/recoverable.
10. A mode or Director feature that cannot meet its proof bar is hidden/disabled rather than falsely advertised.

## Highest-risk unknowns

- How much useful engagement can be created around native Quake bot AI without custom pathfinding/aim control.
- Whether available position/damage/event signals are sufficient for a stable pressure estimator.
- Whether pressure metrics correlate strongly enough with subjective fun to support meaningful personalization.
- Whether persistent role/composition learning becomes repetitive or noticeable as rubber-banding in real play.
- Whether safe bot relocation can ever be proven fair; replacement-first recovery remains preferred.
- Mode-specific Director tuning requires real gameplay feedback even after simulation passes.

## Decision path

The project agent may decide internal architecture, telemetry, test structure, pressure algorithms, role definitions, memory decay, confidence thresholds and tuning mechanisms that obey the product/fairness contract.

Escalate to operator before:

- dropping a visible mode;
- changing primary platform;
- introducing paid/external runtime dependencies;
- weakening a fairness law;
- materially changing a mode's intended fantasy/goal.

## Planning authority

- Roadmap: `work/ROADMAP.md`
- Director design authority: `docs/DIRECTOR_DESIGN.md`
- Director learning contract: `docs/DIRECTOR_LEARNING.md`
- Risks: `work/RISK_REGISTER.md`
- Current roadmap decision: D0-D7 foundations are implemented and green; freeze authority growth and use real Mint telemetry/play quality to calibrate D8/D9 before expanding Director powers.
