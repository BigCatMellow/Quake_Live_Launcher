# MAPS Checkpoint — Encounter Director D0-D2

- State: CONTINUE
- Branch: `v5-alpha`
- Production integration commit: `9c055e8d7046ef9b723149c392f670463cc3f271`
- Clean product CI: GitHub Actions run `33173627036` PASS on `eb97f1ade16f1bd77ba25d69e050aaab95b9fb8e`
- CI artifact: `9686633905`

## Evidence reviewed

- `solo_engine/plugins/solo_director.py` — pure deterministic mode-aware Director.
- `solo_engine/plugins/director_runtime.py` — live minqlx adapter and telemetry writer.
- `solo_engine/plugins/solo_arcade.py` — Director integrated into bot spawn/death, damage observation, reinforcement timing, frame observation, loadouts and `!run` diagnostics.
- `tests/test_director.py` — profile, fairness, deterministic role, pressure, recovery-window and safe-replacement tests.
- `tests/test_live_runtime_regressions.py` — runtime role/loadout and Horde recovery-without-objective-progress tests.
- Existing 15-mode fake-minqlx lifecycle suite remains green.

## D0 — Observe: IMPLEMENTED

The Director samples encounter state at low frequency rather than every frame for decision-making. It records:

- mode/profile/difficulty;
- inferred pressure and target band;
- alive/engaged/idle/far enemy counts;
- player health and armor;
- recent damage taken/dealt;
- reinforcement hold state;
- every Director action and reason.

Runtime evidence is appended to:

`~/.local/share/quake-live-launcher/solo_runtime/director.jsonl`

The file rotates at approximately 2 MB to `director.previous.jsonl`.

`!run` now exposes a compact Director summary alongside mode lifecycle state.

## D1 — Engagement recovery: IMPLEMENTED, deliberately narrow

Active recovery is enabled only for:

- Horde;
- Gun Game;
- Speedrun Combat.

A bot can be recycled only when all of the following are true:

- the mode permits recovery;
- the objective is ACTIVE;
- total encounter pressure is below the profile floor;
- the bot has been non-contributing for the full mode timeout;
- it is outside useful engagement range;
- its recovery cooldown has elapsed;
- the player has not already damaged it.

The Director removes the bot from objective ownership before kicking it, then spawns a role-preserving replacement. This prevents recovery from counting as a kill, wave clear, target score or objective progress.

No teleport/relocation is implemented.

## D2 — Roles: INITIAL IMPLEMENTATION

Every Solo mode has a Director profile and deterministic seeded role sequence. Roles currently affect loadout and bounded health/armor identity while native Quake AI still owns aim, fire and navigation.

Current roles:

- Chaser
- Gunner
- Marksman
- Bruiser
- Skirmisher
- Berserker
- Target
- Boss

Boss/trial mode-specific loadouts override generic role loadouts where the existing mode contract requires them.

## Fairness evidence

Regression tests prove that:

- a bot that has already taken player damage is not recycled for fresh health;
- recent contact prevents idle recovery;
- severe incoming damage creates a bounded reinforcement hold instead of escalation;
- non-recovery modes observe without manipulating bot population;
- Director recovery does not clear or advance Horde;
- difficulty changes pressure/encounter parameters before native bot skill.

## Not yet enabled

The following remain D3+ work and are intentionally not presented as finished:

- continuously changing simultaneous engager count;
- adaptive role-composition rewriting during an active objective;
- performance-based skill changes;
- spawn-location control;
- teleport/relocation;
- Arena build counter-composition;
- dynamic health/damage rubber-banding.

## MAPS decision

`CONTINUE`.

Reason: the observation layer, safe first intervention and initial role system all pass deterministic and fake-minqlx integration tests without weakening the existing 15-mode lifecycle contracts. The next Director decisions should be tuned from real `director.jsonl` evidence before widening D3 intervention authority.
