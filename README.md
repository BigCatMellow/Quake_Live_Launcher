# Quake Live Launcher

A Linux-first Quake Live launcher focused on **single-player/offline play against bots**, including curated map selection, native Arcade rule sets, and scripted Solo modes powered by a local Quake Live Dedicated Server plus shinqlx.

## Project status

The ZIP-based v4.x prototype proved that the launcher UI, Steam discovery, map scanning, Workshop support, curated map recommendations, custom factories, and local QLDS/shinqlx installation route are viable.

The scripted Solo lifecycle in v4.11 is being replaced rather than patched further. The audit in [`docs/V4_11_AUDIT.md`](docs/V4_11_AUDIT.md) found several API-level and state-machine problems, including:

- not using shinqlx's native `allow_single_player(True)` facility;
- ZMQ-dependent hooks without a guaranteed `zmq_stats_enable 1` startup configuration;
- `set_cvar_once()` being used for cvars that already exist;
- a race-prone `bot_minplayers` bootstrap competing with the plugin's own bot controller;
- map changes resumed by fixed timers instead of player/map lifecycle events;
- means-of-death values inferred with string heuristics instead of exported constants;
- upgrades whose declared effects were not fully implemented;
- tests that checked source strings more often than gameplay state transitions.

## Development model

- `main` is the documented stable baseline.
- `v5-alpha` is the architectural rebuild branch.
- Native Arcade modes remain Quake Live factory/cvar driven where possible.
- Scripted Solo modes run on a single explicit plugin state machine.
- Quake Live provides the combat sandbox; the Solo controller owns waves, objectives, progression and run completion.

## v5 priorities

1. Reliable one-human local server lifecycle.
2. Horde as the first end-to-end integration target.
3. Gun Game as the second target.
4. Arena Run as the full systems test (waves, upgrades, bosses, movement, map transitions, persistence).
5. Port remaining scripted modes only after the controller is stable.
6. Add a fake-minqlx test harness so CI can simulate player joins, bot deaths, wave clears and stale callbacks without launching Quake Live.

## Platform

Primary target: Linux Mint / Ubuntu-family x86_64 systems with the Steam version of Quake Live.
