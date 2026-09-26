# Director Capabilities — what the engine actually allows

Researched 2026-09-26 against shinqlx 0.7.0 source (PyPI sdist), minqlx, and the
GPL Quake 3 source (ioquake3 botlib / game). Quake Live's game and bot code are
closed, so anything marked **probe** must be confirmed on the real server with
`solo_engine/run_director_probe.sh` before a feature depends on it.

## Verified — safe to build on

| Capability | Evidence | Used by |
| --- | --- | --- |
| Read/write a player's position and velocity | `set_position` / `set_velocity`, `Player.position()` / `velocity()` | spawn placement |
| `player_spawn` fires **after** the engine's ClientSpawn | `hooks.rs::shinqlx_client_spawn` calls the real spawn, then dispatches | learn engine spawn points; relocate in the same call |
| Engine anti-overlap (KillBox) does **not** re-run after a relocation | spawn logic runs before the hook | placement keeps ≥96u from every player |
| Spawn a real dropped item that never expires | `spawn_item` → `LaunchItem`, think cleared | probe, future lures |
| Remove every dropped item | `remove_dropped_items` | probe cleanup |
| Bot deaths are resolved by **name** (first match) | `stats_listener.rs::player_by_name` | unique bot names (5.0-alpha-modes1) |
| Bot userinfo readable (`skill`, `characterfile`) | `Player.cvars` | probe |
| Server console output can be captured | `console_print` event | probe reads bot-loader lines |
| Player state: health, armor, weapons, ammo, powerups, holdable | `PlayerState` | probe item classification |

## Not available — do not design around these

- **View angles / facing.** `PlayerState` has no view angles. "Behind the player" is approximated from horizontal velocity when moving faster than 150 u/s.
- **Line of sight.** No trace API. Engagement stays inferred from distance and damage.
- **Bot goals.** No way to tell a bot where to go or what to target.
- **Item pickup events.** Pickup counts only exist in end-of-match stats, and the Solo sandbox never ends a match (permanent warmup).
- **Item name → ID.** `spawn_item` only takes a numeric ID and nothing in Python maps names to IDs.

## Unsafe — never call

- **`replace_items`** (shinqlx 0.7.0): `GameEntity::replace_item` writes the updated item table with `set_configstring(item_id, …)` instead of `CS_ITEMS`, overwriting configstring slot `item_id` (low slots hold server info, warmup state and scores). The probe has a regression test that it never calls it.

## Needs the probe (Quake 3 behavior, unconfirmed in Quake Live)

| Question | Quake 3 behavior | Probe method |
| --- | --- | --- |
| Custom `scripts/*.bot` files load | `G_LoadBots` reads `scripts/bots.txt` and every `scripts/*.bot` | `botlist` output + whether `addbot qllprobea` joins |
| Custom character files load | botlib loads `botfiles/<aifile>`; missing traits fill from `default_c.c`; logs `loaded skill N from <file>` or falls back to default | bot loader console lines + bot `characterfile` |
| Fractional skill | `addbot` parses a float; traits interpolate between skill 1/4/5 blocks | bot `skill` userinfo after `addbot anarki 2.5` |
| Item IDs | — | spawn each ID on a bot, classify from what it gained |
| Bots chase dropped items | botlib adds dropped items as goals once they stop moving; ignores jump pads; forgets after 30 s | closest approach vs a no-item control window, health jump on pickup |

Results are written to `solo_runtime/director_probe.json`.

## Design consequences

- **Spawn points are learned, not parsed.** Every engine spawn is a valid reachable floor position; learning works for Workshop maps and avoids reading `.pk3` files (Quake Live's own may be encrypted).
- **Item lures (if the probe confirms them) must be refreshed every <30 s** and placed at learned spawn points, never on jump pads.
- **Bot personalities (if confirmed)** would ship as `.bot` + character files under the QLDS home path, with unique names that also satisfy the name-resolution rule.
