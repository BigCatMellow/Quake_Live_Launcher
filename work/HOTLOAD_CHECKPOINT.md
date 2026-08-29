# MAPS Checkpoint — Solo Forfeit Guard + Persistent Match Hot-Load

- State: CONTINUE TO LIVE VALIDATION
- Scope: scripted Solo modes on the local QLDS/shinqlx runtime

## Triggering evidence

Real Mint play exposed two product failures that automated readiness did not prove:

1. Horde could enter Quake Live's ordinary one-player multiplayer forfeiture path immediately after launch.
2. Trying another scripted Solo mode required closing and reopening the Quake client.

These are product-level failures even when QLDS, UDP and plugin readiness are healthy.

## Root-cause model

Upstream shinqlx `allow_single_player(True)` changes the current level's training-map flag. If it is called before a CurrentLevel exists, the call is harmless but cannot mutate the future level. Therefore a constructor-time call alone is not a sufficient anti-forfeit contract.

The runtime now treats single-player permission as an invariant rather than a one-time setup action:

- QLDS requests `g_training 1` before the initial `+map` and in server.cfg;
- `solo_directed` calls `allow_single_player(True)` on new-game, map, player-loaded and player-spawn boundaries;
- the live frame loop reasserts the training contract at low frequency, with the first frame eligible immediately after each map transition;
- hot-loaded map changes reset the frame assertion gate so the new CurrentLevel is covered as well.

## Persistent scripted-Solo session

The local QLDS process is now intended to outlive individual scripted Solo matches.

Flow after the first successful scripted Solo launch:

1. Launcher writes the new normal `solo_session.json`.
2. If the existing local Solo server has a matching healthy plugin handshake, the launcher writes `solo_runtime/match_request.json` instead of starting another QLDS/Quake process.
3. `solo_directed` consumes the request from the live frame loop.
4. The old Director session is finalized as `switched` so learning evidence is not lost.
5. Existing objective bots are removed through normal ownership-safe cleanup.
6. Mode/controller/Director/session state is rebuilt from the new session.
7. The human remains the same connected client and remains on RED.
8. QLDS performs `map <requested-map> tdm`; the connected Quake client follows the server map change.
9. The next player spawn starts the requested scripted mode.
10. `match_status.json` records loading/started/active/failed state for launcher diagnostics.

A failed hot-load is not silently treated as success. The launcher waits for an explicit request ID acknowledgement and matching plugin readiness.

## Current boundary

Hot-load is implemented for the 15 **scripted Solo** modes because they share one QLDS/plugin lifecycle.

Native Arcade/Quick Play presets still rely on client startup commands/factories and are not yet advertised as in-place hot-loadable. They require a separate migration if persistent-client switching is desired there.

## Automated proof

New fake-minqlx regressions prove:

- Horde starts with single-player/training permission active;
- an artificial loss of the training flag is restored from the live frame loop while Horde remains ACTIVE;
- an active Horde match can be switched to Gun Game through `match_request.json`;
- the same human client ID is retained;
- the human remains RED;
- the new map command is issued;
- plugin readiness changes to the new mode;
- request status advances loading -> active;
- Gun Game reaches an ACTIVE objective with live enemy ownership after the handoff.

The all-mode lifecycle matrix and Director learning/fairness regressions remain in the same product CI gate.

## MAPS challenge

### Could we simply restart QLDS but leave Quake running?

Rejected as the primary design. A stopped local server disconnects the client and leaves reconnect orchestration dependent on client-side command injection that the launcher does not reliably own once Quake is running. Keeping one server alive gives the plugin an authoritative control path and preserves the connection naturally through server map changes.

### Could every launcher mode use this immediately?

No. Scripted Solo has a common server/plugin contract. Native Arcade currently does not. Claiming universal hot-load now would recreate the same product-integrity problem this roadmap is intended to prevent.

## Remaining release evidence

- [ ] Real Mint: Horde no longer forfeits at launch.
- [ ] Real Mint: after one fresh scripted Solo launch, selecting another scripted Solo mode from the launcher changes the running match without closing Quake.
- [ ] Real Mint: multiple sequential switches do not leave stale bots/objective state.
- [ ] Real Mint: Director player/playbook memory survives and records `switched` sessions correctly.
- [ ] Decide separately whether native Arcade should migrate onto the persistent local server model.

Until those are confirmed, PR #1 remains draft.
