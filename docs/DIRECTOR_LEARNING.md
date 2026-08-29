# Director Learning Contract

## Core idea

The bots are Quake Live's pieces. The Director is the opposing encounter manager.

The Director learns only how to arrange and pace legal encounters. It does not learn how to cheat. Native Quake Live remains responsible for bot navigation, aiming, firing and physics.

## Three time scales

### Immediate encounter feedback

Every meaningful Director intervention becomes a small experiment:

1. record why the Director acted;
2. record the expected pressure effect;
3. record whether the action actually executed;
4. observe the encounter for a bounded window;
5. score whether pressure moved toward the target without causing an unfair danger spike;
6. apply a small temporary correction for later decisions in the same encounter.

A dangerous intervention makes the Director back off. An ineffective low-pressure intervention lets it push slightly harder. Successful behavior moves the temporary correction back toward neutral.

Temporary correction is capped and cannot change bot aim, hidden damage, native skill, or authored mode contracts.

### Session/player memory

`director_player.json` stores decaying per-mode tendencies:

- objective/session counts;
- pressure trend;
- fraction of time below/above the target band;
- severe-pressure trend;
- damage dealt/taken trend;
- objective/session duration;
- kills/deaths;
- bounded learned pressure offset.

Persistent pressure personalization only activates after repeated objective evidence. Old evidence decays toward neutral so the Director does not fossilize an old version of the player's ability.

### Tactical playbook

`director_playbook.json` stores evidence about the Director's own pieces and plays:

- role effectiveness;
- counted role-composition outcomes;
- Director action success/score trends;
- confidence/attempt counts.

Learned evidence may influence future role selection only when:

- the role is already legal for that mode;
- the special Boss/Target contract is not being overridden;
- the role's hard composition cap is not exceeded;
- enough evidence exists;
- the learned option beats the authored seeded role by an explicit margin.

## Debug evidence

### Continuous telemetry

`~/.local/share/quake-live-launcher/solo_runtime/director.jsonl`

Contains pressure, target band, learned shift, player state, engagement counts, role assignments, proposed actions and completed evaluations.

### Action audit

`~/.local/share/quake-live-launcher/solo_runtime/director_actions.jsonl`

Contains the explicit chain:

`decision -> validation/execution -> actual result -> later evaluation`

Skipped actions are logged too, including ownership loss, missing bot, missing track, invested player damage and kick failure.

### Persistent model

- `director_player.json`
- `director_playbook.json`

### Raw session evidence

`director_sessions/<timestamp>-<mode>-<seed>.jsonl`

The raw session stream records objective starts/results, Director decisions, execution results, evaluations and session result. It exists so a learned preference can be audited back to evidence rather than treated as an opaque AI choice.

## Fairness boundaries

The learning system cannot gain authority over:

- direct aiming or firing;
- hidden accuracy/reaction changes;
- hidden damage multipliers;
- hostile teleporting;
- restoring a damaged enemy through Director recovery;
- exceeding hard role-composition caps;
- violating rocket-only, boss, trial or other authored mode contracts;
- instantly counter-picking the player's latest weapon or Arena upgrade.

Difficulty remains the outer safety envelope. Learning operates inside it.

## What counts as a successful learned move

A move is not successful merely because the player took damage.

The Director evaluates whether the encounter moved toward its target pressure band without creating a danger spike. A useful action should improve engagement while preserving readable, recoverable combat.

This is intentionally an approximation. Live play evidence remains authoritative for whether the pressure model corresponds to fun.

## MAPS proof bar

The learning track is not DONE until real sessions show all of the following:

- the Director can recover from a bad decision during the same encounter;
- persistent memory improves future encounter setup rather than causing obvious rubber-banding;
- role/composition learning produces recognizable useful tactics without repetitive spam;
- the player can still understand why a major intervention happened from logs;
- learned behavior does not violate any mode lifecycle or fairness contract;
- old or corrupt memory safely decays/falls back instead of destabilizing the game.
