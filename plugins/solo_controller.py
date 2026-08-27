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
    """Pure state machine shared by every scripted Solo mode.

    Quake Live is treated as a combat sandbox. This controller owns the
    objective lifecycle, enemy IDs and callback generation.
    """

    mode: str
    phase: Phase = Phase.BOOTING
    generation: int = 0
    player_id: Optional[int] = None
    expected_enemies: int = 0
    enemy_ids: set[int] = field(default_factory=set)
    pending_map: Optional[PendingMap] = None
    failure: Optional[str] = None

    def wait_for_player(self) -> None:
        self._bump()
        self.phase = Phase.WAITING_FOR_PLAYER
        self.player_id = None
        self.expected_enemies = 0
        self.enemy_ids.clear()

    def player_loaded(self, player_id: int) -> bool:
        self.player_id = player_id
        if self.phase == Phase.WAITING_FOR_PLAYER:
            self.phase = Phase.PREPARING
            return True
        return False

    def begin_objective(self, expected_enemies: int) -> int:
        if self.phase in (Phase.COMPLETE, Phase.FAILED):
            raise RuntimeError(f"cannot begin objective in {self.phase}")
        self._bump()
        self.phase = Phase.PREPARING
        self.expected_enemies = max(0, int(expected_enemies))
        self.enemy_ids.clear()
        if self.expected_enemies == 0:
            self.phase = Phase.ACTIVE
        return self.generation

    def enemy_spawned(self, client_id: int) -> bool:
        if self.phase not in (Phase.PREPARING, Phase.ACTIVE):
            return False
        self.enemy_ids.add(int(client_id))
        if self.phase == Phase.PREPARING and len(self.enemy_ids) >= self.expected_enemies:
            self.phase = Phase.ACTIVE
            return True
        return False

    def enemy_died(self, client_id: int) -> bool:
        cid = int(client_id)
        if cid not in self.enemy_ids:
            return False
        self.enemy_ids.remove(cid)
        if self.phase == Phase.ACTIVE and not self.enemy_ids:
            self.phase = Phase.BETWEEN_ROUNDS
            return True
        return False

    def remove_enemy(self, client_id: int) -> None:
        self.enemy_ids.discard(int(client_id))

    def request_map(self, map_name: str, payload: Any = None) -> int:
        self._bump()
        self.phase = Phase.PREPARING
        self.pending_map = PendingMap(str(map_name).lower(), payload)
        self.enemy_ids.clear()
        self.expected_enemies = 0
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
        self.enemy_ids.clear()
        self.expected_enemies = 0
        self.pending_map = None

    def fail(self, reason: str) -> None:
        self._bump()
        self.phase = Phase.FAILED
        self.failure = str(reason)
        self.enemy_ids.clear()
        self.expected_enemies = 0
        self.pending_map = None

    def token(self) -> int:
        return self.generation

    def token_valid(self, token: int, *phases: Phase) -> bool:
        if int(token) != self.generation:
            return False
        return not phases or self.phase in phases

    def _bump(self) -> None:
        self.generation += 1
