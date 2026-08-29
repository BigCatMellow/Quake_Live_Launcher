# Director Design — Quake Live Launcher v5

## Purpose

The Director is a mode-aware encounter controller that **plays the bots against the human without playing their aim for them**.

Quake Live remains responsible for navigation, aiming, shooting, movement physics, weapon behavior and collision. The Director observes the encounter and decides how to compose and pace pressure: which bot roles are active, when enemies enter, how many should be engaging at once, what loadouts they receive, when an idle/stuck bot should be recovered, and when the player needs a short recovery window.

The desired feeling is not "the computer cheats harder." The desired feeling is "the opposing side is being intelligently managed."

## Center of gravity

**Fun, readable pressure before raw difficulty.**

Difficulty should normally increase in this order:

1. Better encounter composition.
2. Better pressure timing.
3. More useful positioning/proximity.
4. More coordinated role mix.
5. Less downtime between meaningful fights.
6. Only then: modest increases to bot skill, health, armor or movement.

Accuracy/reaction buffs are a last resort, not the primary difficulty system.

## Definition of DONE

The Director track is DONE when:

- every scripted Solo mode has an explicit Director profile;
- the Director can identify disengaged/stuck/non-contributing enemies and recover them without breaking the mode lifecycle;
- encounter pressure adapts within bounded limits to player performance and current mode state;
- bot role/loadout selection is deterministic from run seed + Director state where reproducibility matters;
- no Director action can silently violate objective ownership, team separation, map-transition or generation-token contracts;
- fairness laws are enforced by code and regression tests;
- difficulty presets change target pressure bands rather than simply multiplying bot accuracy/damage;
- telemetry can explain why the Director intervened;
- every Director profile passes fake-minqlx scenario tests, including a dominant player, struggling player, idle bot, stuck/far bot, sudden low-health state and objective transition;
- real Mint/QLDS testing confirms that Director-managed bots feel engaged, appropriate to the mode and not obviously unfair.

## Fairness laws

The Director may manage the fight, but it must not become an aim-bot or hidden cheat layer.

1. **No direct aim or fire control.** Native Quake bot AI owns aiming and shooting.
2. **No impossible reaction times.** Bot skill/reaction remains within declared preset caps.
3. **No surprise damage multipliers.** Any health/armor/damage change must belong to the visible mode/difficulty profile.
4. **No hostile teleport into immediate danger.** Recovery relocation must prefer safe distance/out-of-contact positions and never place a bot directly on top of the player.
5. **No hard-counter cheating.** The Director may create variety around the player's build, but should not instantly counter-pick every successful weapon/upgrade.
6. **No punishment for success spikes.** Winning one fight does not immediately cause an invisible difficulty jump.
7. **No dogpile without budget.** Simultaneous pressure is capped per difficulty and mode.
8. **Recovery windows are intentional.** Low health or a recent high-damage burst can temporarily reduce incoming encounter pressure without making enemies inert.
9. **One change axis at a time when adapting.** Prefer changing pacing/composition before simultaneously increasing count, accuracy, health and damage.
10. **Every intervention is loggable.** Diagnostics should be able to say what the Director changed and why.

## Director model

### Observation state

At a fixed low-frequency tick (for example 2–4 times per second), collect only useful encounter signals:

- mode and objective phase;
- player health/armor;
- recent damage taken and dealt;
- recent kills/deaths;
- time since meaningful combat;
- active enemy count;
- intended enemy count;
- bot positions and distance from the player;
- bot last damage dealt / received / death / spawn time;
- approximate engaged count;
- idle duration per bot;
- objective-specific state (wave, boss, target, stage, lives, timer, Arena build).

Line-of-sight should only be used if a reliable current engine API is confirmed. Until then, engagement should be inferred from distance plus recent damage/contact.

### Pressure budget

Each Director profile exposes a target pressure band rather than a single difficulty number.

Example conceptual state:

- `pressure_target`: desired encounter intensity;
- `pressure_current`: inferred current intensity;
- `max_engaged`: maximum bots that should pressure simultaneously;
- `recovery_floor`: minimum respite after a severe damage event;
- `idle_timeout`: when a bot is considered non-contributing;
- `distance_band`: preferred proximity for meaningful contact;
- `composition_budget`: role mix allowed for the current stage;
- `skill_cap`: maximum native bot skill allowed by this preset.

The Director spends pressure through encounter decisions. It does not need to spend every available point.

### Intervention order

When pressure is **too low**:

1. Wake/recover an idle or very distant bot.
2. Improve role mix.
3. Reduce next spawn/replacement delay.
4. Allow one more simultaneous engager within the profile cap.
5. Move future spawns into a more useful distance band.
6. Increase individual bot skill only if the profile permits it.

When pressure is **too high**:

1. Delay replacement/spawn timing.
2. Reduce simultaneous engager target.
3. Hold a reinforcement briefly rather than weakening a bot already in combat.
4. Create a short recovery window after the current contact breaks.
5. Lower future bot skill/composition intensity if sustained struggle continues.

This avoids obvious rubber-banding during a firefight.

## Bot roles

Roles are loadout/behavior contracts layered over native bots. They should be simple enough that Quake's existing AI can express them.

- **Chaser** — close/medium pressure; shotgun/rocket; higher movement emphasis.
- **Gunner** — sustained tracking pressure; LG/plasma.
- **Marksman** — railgun; prefers fewer simultaneous marksmen to avoid unfair burst damage.
- **Bruiser** — moderate speed, more health/armor, shotgun/rocket.
- **Skirmisher** — mobile mixed loadout; fills gaps and prevents predictable compositions.
- **Berserker** — high movement, short-range weapon bias; used sparingly.
- **Target/Boss** — objective-owned special role with explicit profile limits.

Roles should influence loadout, health/armor, spawn timing and replacement policy. They should not require custom pathfinding or direct aim scripting.

## Difficulty philosophy

Difficulty presets should primarily control encounter management.

### Easy

- low simultaneous pressure;
- longer recovery windows;
- generous idle-recovery threshold;
- fewer marksman/burst combinations;
- native skill capped low/moderate;
- Director avoids surrounding pressure.

### Normal

- steady contact with recognizable breathing room;
- mixed roles;
- Director replaces non-contributing bots promptly;
- moderate simultaneous pressure;
- native skill remains fair rather than perfect.

### Hard

- shorter downtime;
- more deliberate role combinations;
- slightly higher simultaneous pressure;
- faster idle recovery;
- stronger flanking/spatial pressure where the engine permits it;
- skill rises only after encounter-quality knobs are exhausted.

### Nightmare

- highest allowed pressure budget;
- aggressive composition and replacement timing;
- minimal but nonzero recovery windows;
- higher native bot skill within a defined cap;
- still obeys every fairness law: no hidden aim, no instant counter-picking, no impossible spawn attacks.

## Mode profiles

### Horde — Hunt Director

Goal: enemies should feel like a wave hunting the player.

- maintain a minimum meaningful-contact rate;
- mix Chasers, Gunners and occasional Marksmen/Bruisers;
- recover bots that wander or stop contributing;
- keep only a bounded number fully pressuring at once;
- increase composition complexity by wave before raising raw bot accuracy;
- elite waves may add a special role rather than simply inflating every bot.

### Arena Run — Roguelite Director

Goal: make each round test the player's build without invalidating it.

- respect round theme and boss/trial identity;
- read upgrade/build effects to avoid useless encounters;
- never immediately hard-counter the latest picked upgrade;
- scale composition, tempo and encounter count with run progression;
- allow a recovery beat between high-pressure rounds;
- boss rounds use deliberate boss pressure rather than ordinary wave spam.

### Gun Game — Flow Director

Goal: keep the player fighting and progressing through the weapon ladder.

- prioritize constant reachable targets;
- recover distant/idle bots quickly;
- avoid long empty-map periods;
- adjust role mix to create reasonable opportunities for the current weapon;
- never intentionally make a weapon stage impossible through counter-composition.

### Boss Rush — Duel Director

Goal: each boss should feel distinct and threatening, not simply over-statted.

- one primary boss pressure profile at a time;
- distinct boss role/loadout/movement tendencies;
- cap burst damage and reaction skill;
- use health/armor and pressure rhythm to create identity;
- no extra adds unless a boss definition explicitly includes them.

### Wipeout Solo — Squad Director

Goal: make the enemy squad feel coordinated while preserving the wipe window.

- role-balanced squad composition;
- control respawn cadence so a wipe is achievable but earned;
- prevent one hidden/distant bot from ruining the round;
- keep pressure spread rather than all bots entering simultaneously at maximum force.

### The Gauntlet — Trial Director

Goal: each stage teaches/tests a different combat shape.

- rail stage: controlled sightline pressure, limited simultaneous marksmen;
- rocket stage: medium/close movement pressure;
- LG stage: sustained tracking engagements;
- plasma stage: movement/control pressure;
- survival stage: mixed composition;
- boss stage: duel profile.

### Last Stand — Siege Director

Goal: mounting pressure around a single life.

- gradual escalation in active pressure;
- favor encirclement/composition over accuracy buffs;
- clear recovery windows become rarer as the run progresses;
- never spawn an unavoidable lethal burst directly beside the player.

### One Life — Tension Director

Goal: sustained fair tension where mistakes matter.

- conservative burst composition because one death ends the run;
- meaningful but readable engagements;
- no sudden high-skill spike after a kill streak;
- pressure increases through pacing and role mix.

### Bounty Hunt — Escort Director

Goal: make identifying and reaching the target interesting.

- target gets a distinct role/loadout;
- non-target bots create bounded escort/interference pressure;
- prevent target from idling indefinitely far from contact;
- do not bury the target behind an unfair full-map swarm.

### Rocket Tag — Chase Director

Goal: mobile rocket-focused pursuit.

- favor Chaser/Skirmisher movement profiles;
- maintain medium engagement distance;
- prevent rail/LG-style loadout pollution;
- recover bots that fail to join the chase.

### Movement Hunter — Pursuit Director

Goal: force the player to use movement without turning enemies into perfect shots.

- pressure comes from pursuit count and approach timing;
- accuracy remains deliberately capped;
- idle bots are aggressively recovered;
- recovery windows appear after severe damage so movement, not unavoidable focus fire, determines survival.

### Predator — Swarm Director

Goal: player feels powerful while a smart swarm keeps pressure meaningful.

- more targets rather than superhuman individual bots;
- rapid replacement cadence;
- role composition prevents every enemy from being identical;
- player sustain mechanics remain meaningful.

### Accuracy Trial — Target Director

Goal: create useful aim practice, not a lethal duel.

- bot movement should be readable but varied;
- low incoming lethality;
- spacing tuned for the trial weapon;
- Director prioritizes available targets and movement patterns over combat pressure.

### Speedrun Combat — Feed Director

Goal: remove dead time and make speed depend on combat execution.

- fast replacement;
- aggressive idle recovery;
- useful spawn-distance bands;
- no artificial health inflation that converts the trial into damage sponge time.

### Random Loadout — Improvisation Director

Goal: make random weapons interesting without secretly countering them.

- build composition around broad engagement variety;
- avoid repeated extreme mismatch against current loadout;
- vary roles each loadout round;
- no hard-counter reaction immediately after a loadout roll.

## Architecture

Proposed modules:

```text
solo_engine/plugins/
├── solo_arcade.py          # lifecycle adapter / mode integration
├── solo_controller.py      # objective state ownership
├── director.py             # common Director state + pressure budget
├── director_profiles.py    # per-mode/preset configuration
├── director_roles.py       # role/loadout definitions
└── director_telemetry.py   # event observations + diagnostic records
```

`solo_arcade` should ask the Director for decisions; the Director should not directly own Quake lifecycle state.

Example boundary:

```text
Quake events
   ↓
solo_arcade
   ↓ observations
Director
   ↓ recommendation
solo_arcade
   ↓ bounded engine action
Quake bot AI
```

This keeps one owner for objective lifecycle and prevents the Director from becoming a second competing state machine.

## MAPS execution roadmap

### D0 — Measurement before adaptation

- Add Director telemetry with no behavior changes.
- Record player pressure, contact gaps, bot idle time, contribution, distance and intervention candidates.
- Add deterministic simulated encounter traces.
- Establish baseline values from current Horde and at least two other modes.

**Gate:** telemetry can explain why Horde felt passive without changing the fight.

### D1 — Idle recovery + engagement floor

- Add idle/non-contributing bot detection.
- Add safe recovery action: replacement first; relocation only after engine-safe placement rules are proven.
- Add per-mode preferred distance/engagement bands.
- Horde, Gun Game and Speedrun are first proving modes.

**Gate:** seeded simulations show no objective corruption, duplicate bots or sudden unfair proximity; live Horde has materially less wandering.

### D2 — Roles + composition

- Implement role definitions and loadout contracts.
- Add composition budget and role caps.
- Add Horde wave composition progression.
- Add Gauntlet/Boss/Accuracy special profiles.

**Gate:** difficulty can increase through composition while native skill stays fixed.

### D3 — Pressure budget + recovery windows

- Implement pressure estimate and target band.
- Add recent-damage/recent-kill smoothing.
- Add spawn/replacement pacing decisions.
- Add recovery windows after severe pressure.
- Ensure adaptation changes one major axis at a time.

**Gate:** dominant and struggling simulated players produce different pacing without any forbidden fairness-law intervention.

### D4 — All-mode Director profiles

- Wire each of the 15 modes to its explicit profile above.
- Add profile-specific scenario tests and terminal-state tests.
- Preserve existing objective state machine as authority.

**Gate:** every advertised mode has a tested profile or explicitly uses a documented neutral Director profile.

### D5 — Difficulty calibration

- Convert Easy/Normal/Hard/Nightmare into pressure-budget presets.
- Use bot skill as a capped secondary knob.
- Add diagnostic summary: average engaged count, combat gap, interventions, pressure high/low time, role mix.

**Gate:** presets differ primarily in encounter pressure/pacing, not hidden damage or accuracy.

### D6 — Live validation and tuning

- Test Horde first, then representative modes: Arena Run, Gun Game, Boss Rush, Movement Hunter, Accuracy Trial.
- Record subjective problems as measurable Director symptoms (too quiet, dogpile, unavoidable burst, target hidden, too much downtime).
- Tune profile values rather than adding mode-specific hacks where possible.

**Gate:** live behavior is described as engaged and appropriately difficult without feeling overpowered; no fairness law is violated.

## Verification scenarios

Every profile should at minimum simulate:

- player dominates for 30 seconds;
- player takes a severe damage burst;
- player remains low health without contact;
- one bot contributes nothing for the idle timeout;
- one bot remains extremely far from the player;
- enemy dies during PREPARING;
- objective transitions while a Director action is queued;
- map changes while pressure state exists;
- mode completes while a recovery/replacement action is queued;
- difficulty preset changes expected pressure caps but not lifecycle semantics.

Director callbacks must use the existing generation/token contract so no intervention can mutate a later objective.

## Success metrics

Metrics are diagnostic, not competitive scoring targets.

- meaningful-combat gap distribution;
- percentage of active bots contributing within a rolling window;
- average simultaneous engaged enemies;
- time spent above/below target pressure band;
- idle recoveries per minute;
- replacement/relocation count;
- severe-damage events followed by additional dogpile pressure;
- player death cause context;
- Director intervention reason counts.

The Director is successful when these metrics help explain the feel of a match and allow tuning without replacing human playtesting with arbitrary numbers.
