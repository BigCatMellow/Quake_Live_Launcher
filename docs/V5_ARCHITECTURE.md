# v5 architecture

## Contract

Quake Live is the combat sandbox; the plugin owns scripted-mode state.

- one human is allowed by `minqlx.allow_single_player(True)`;
- `bot_minplayers` remains `0`;
- ZMQ stats is enabled on the qzeroded command line before plugin loading;
- existing cvars are changed with `set_cvar()`, not `set_cvar_once()`;
- the controller owns exact enemy client IDs;
- every delayed callback captures a controller generation token;
- map transitions resume from lifecycle events, not fixed sleep times;
- `COMPLETE` and `FAILED` are real terminal phases.

## Controller phases

```text
BOOTING
  -> WAITING_FOR_PLAYER
  -> PREPARING
  -> ACTIVE
  -> BETWEEN_ROUNDS
  -> PREPARING ...
  -> COMPLETE

Any state may transition to FAILED.
```

## v5-alpha scope

Horde and Gun Game are the first live integration targets. Arena Run is next after this controller survives real QLDS testing.
