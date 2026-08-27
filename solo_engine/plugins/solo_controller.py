from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Phase(str, Enum):
    BOOTING = "booting"
    WAITING_FOR_PLAYER = "waiting_for_player"
    PREPARING = "preparing"
    ACTIVE = "active"
    BETWEEN_ROUNDS = "between_rounds"
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class PendingMap:
    map_name: str
    payload: Any = None


@dataclass
class SoloController:
    """Pure lifecycle controller for every scripted Solo mode.

    ``fulfilled_spawns`` deliberately differs from ``enemy_ids``. A bot can be
    killed while later members of a staggered wave are still joining; that kill
    must not make the controller wait forever for an impossible number of living
    enemies before entering ACTIVE.
    """

    mode: str
    phase: Phase = Phase.BOOTING
    generation: int = 0
    player_id: Optional[int] = None
    expected_spawns: int = 0
    fulfilled_spawns: int = 0
    initial_spawn_ids: set[int] = field(default_factory=set)
    enemy_ids: set[int] = field(default_factory=set)
    pending_map: Optional[PendingMap] = None
    failure: Optional[str] = None
    auto_clear: bool = True

    @property
    def expected_enemies(self) -> int:
        """Compatibility alias used by diagnostics/UI."""
        return self.expected_spawns

    def wait_for_player(self) -> None:
        self._bump()
        self.phase = Phase.WAITING_FOR_PLAYER
        self.player_id = None
        self._reset_objective()

    def player_loaded(self, player_id: int) -> bool:
        self.player_id = int(player_id)
        if self.phase == Phase.WAITING_FOR_PLAYER:
            self.phase = Phase.PREPARING
            return True
        return False

    def begin_objective(self, expected_enemies: int, *, auto_clear: bool = True) -> int:
        if self.phase in (Phase.COMPLETE, Phase.FAILED):
            raise RuntimeError(f"cannot begin objective in {self.phase.value}")
        self._bump()
        self.phase = Phase.PREPARING
        self.expected_spawns = max(0, int(expected_enemies))
        self.fulfilled_spawns = 0
        self.initial_spawn_ids.clear()
        self.enemy_ids.clear()
        self.auto_clear = bool(auto_clear)
        if self.expected_spawns == 0:
            self.phase = Phase.ACTIVE
        return self.generation

    def enemy_spawned(self, client_id: int) -> bool:
        """Register a bot spawn; return True only when objective first activates."""
        if self.phase not in (Phase.PREPARING, Phase.ACTIVE):
            return False
        cid = int(client_id)
        self.enemy_ids.add(cid)
        if self.phase == Phase.PREPARING and cid not in self.initial_spawn_ids:
            self.initial_spawn_ids.add(cid)
            self.fulfilled_spawns += 1
            if self.fulfilled_spawns >= self.expected_spawns:
                self.phase = Phase.ACTIVE
                return True
        return False

    def enemy_died(self, client_id: int) -> bool:
        """Remove an owned living enemy; return True when an auto-clear objective clears."""
        cid = int(client_id)
        if cid not in self.enemy_ids:
            return False
        self.enemy_ids.remove(cid)
        if self.phase == Phase.ACTIVE and self.auto_clear and not self.enemy_ids:
            self.phase = Phase.BETWEEN_ROUNDS
            return True
        return False

    def remove_enemy(self, client_id: int) -> None:
        self.enemy_ids.discard(int(client_id))

    def request_map(self, map_name: str, payload: Any = None) -> int:
        if self.phase in (Phase.COMPLETE, Phase.FAILED):
            raise RuntimeError(f"cannot request map in {self.phase.value}")
        self._bump()
        self.phase = Phase.PREPARING
        self.pending_map = PendingMap(str(map_name).lower(), payload)
        self._reset_objective(keep_phase=True)
        return self.generation

    def resume_map_if_ready(self, map_name: str, player_id: int) -> Any:
        if not self.pending_map:
            return None
        if str(map_name).lower() != self.pending_map.map_name:
            return None
        if self.player_id is not None and int(player_id) != self.player_id:
            return None
        payload = self.pending_map.payload
        self.pending_map = None
        self.player_id = int(player_id)
        self.phase = Phase.PREPARING
        return payload

    def finish(self) -> None:
        self._bump()
        self.phase = Phase.COMPLETE
        self.pending_map = None
        self._reset_objective(keep_phase=True)

    def fail(self, reason: str) -> None:
        self._bump()
        self.phase = Phase.FAILED
        self.failure = str(reason)
        self.pending_map = None
        self._reset_objective(keep_phase=True)

    def token(self) -> int:
        return self.generation

    def token_valid(self, token: int, *phases: Phase) -> bool:
        if int(token) != self.generation:
            return False
        return not phases or self.phase in phases

    def _reset_objective(self, *, keep_phase: bool = False) -> None:
        self.expected_spawns = 0
        self.fulfilled_spawns = 0
        self.initial_spawn_ids.clear()
        self.enemy_ids.clear()
        self.auto_clear = True
        if not keep_phase and self.phase not in (Phase.WAITING_FOR_PLAYER, Phase.COMPLETE, Phase.FAILED):
            self.phase = Phase.PREPARING

    def _bump(self) -> None:
        self.generation += 1
