# Quake Live Launcher Solo Engine v5

The Solo Engine is an optional local runtime for scripted single-player modes.
Native launcher/Arcade modes do not depend on it.

## Runtime stack

- Quake Live Dedicated Server (Steam app 349090)
- shinqlx/minqlx Python plugin bridge
- `solo_arcade.py`
- pure `solo_controller.py` lifecycle state
- pure `solo_core.py` Arena Run rules

## Setup

Run from the launcher with **SET UP / REPAIR SOLO ENGINE**, or directly:

```bash
bash setup_solo_engine.sh
```

Setup installs missing dependencies when authorized, updates QLDS, builds shinqlx,
copies the complete plugin package, then runs `self_test.sh`.

## Self-test

`self_test.sh` temporarily launches QLDS on `127.0.0.1:27961`, loads shinqlx and
`solo_arcade`, and requires a valid `plugin_ready.json` handshake for Horde. Setup
creates `SELF_TEST_OK`/`READY` only after that passes.

## Normal startup

`start_solo.sh` uses port 27960 and refuses to report success until both the game
socket and the requested-mode plugin handshake are verified.

## Combat sandbox

Scripted Solo runs use TDM internally:

- human = red
- bots = blue
- friendly fire = off
- automatic minimum bots = off
- score/time limits = off

The plugin owns exact enemy client IDs and the mode lifecycle.
