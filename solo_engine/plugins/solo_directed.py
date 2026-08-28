#!/usr/bin/env python3
"""Compatibility entrypoint for the Director-managed Solo runtime.

The real encounter intelligence now lives in ``solo_arcade.DirectorRuntime``
and ``director_learning.DirectorLearning``. Keeping this tiny entrypoint lets
existing QLDS startup/configuration continue loading ``solo_directed`` without
maintaining a second competing Director implementation.
"""
from __future__ import annotations

try:
    from .solo_arcade import solo_arcade
except ImportError:
    from solo_arcade import solo_arcade


class solo_directed(solo_arcade):
    """Solo runtime with one authoritative Director implementation."""

    def __init__(self):
        super().__init__()
        self.add_command("director", self.cmd_director)

    def cmd_director(self, player, msg, channel):
        runtime = self.director_runtime
        snapshot = runtime.director.last_snapshot
        if snapshot is None:
            player.tell(f"^6DIRECTOR:^7 {runtime.director.profile.name} — waiting for encounter data.")
            return
        player.tell("^6DIRECTOR:^7 " + runtime.summary())
        roles = {}
        for track in runtime.director.tracks.values():
            roles[track.role] = roles.get(track.role, 0) + 1
        if roles:
            player.tell("^7Roles: " + ", ".join(f"{name} x{count}" for name, count in sorted(roles.items())))
        if runtime.director.should_hold_reinforcements(runtime.now()):
            player.tell("^3Director recovery window:^7 holding future reinforcements briefly.")
        player.tell(
            "^7Learning: pressure shift "
            f"{runtime.learning.pressure_shift():+.1f}; actions and outcomes are logged for review."
        )

    def cmd_help(self, player, msg, channel):
        super().cmd_help(player, msg, channel)
        player.tell("^7Director: ^3!director ^7shows pressure, role mix, and learned pressure shift.")
