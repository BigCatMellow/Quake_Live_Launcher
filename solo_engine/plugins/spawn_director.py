"""Spawn-point learning and fair spawn placement for the Solo Director.

Engine facts this module relies on (verified in shinqlx 0.7.0 source):

* The ``player_spawn`` hook fires *after* the engine's ClientSpawn, so the
  position read inside the hook is the spawn point the engine picked, and a
  position written there is where the player actually starts.
* The engine's anti-overlap KillBox already ran at the engine-chosen point, so
  a relocated spawn must keep its own clearance from every other player.

Not available from the engine, so never assumed here: view angles, line of
sight, and bot goal selection. "Behind the player" is approximated from the
player's horizontal velocity and only when they are clearly moving.

Spawn points are learned rather than parsed from map files: every engine
spawn reveals a valid, reachable floor position, which works for stock and
Workshop maps alike and needs no access to (possibly encrypted) pk3 files.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import random
import time
from typing import Iterable, Optional, Sequence

MERGE_RADIUS = 72.0              # two spawns closer than this are one point
MAX_POINTS_PER_MAP = 96
MIN_POINTS_FOR_PLACEMENT = 4     # below this, leave the engine's choice alone
MIN_PLAYER_DISTANCE = 700.0      # fairness law 4: never spawn on the player
MIN_OCCUPANT_SPACING = 96.0      # KillBox does not re-run after a move
RECENT_USE_SECONDS = 4.0         # avoid stacking consecutive spawns
MOVING_SPEED = 150.0             # below this the player's heading is unknown
SAVE_INTERVAL = 5.0

Vec = tuple[float, float, float]


def distance(a: Vec, b: Vec) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def as_vec(value) -> Optional[Vec]:
    """Accept a minqlx Vector3, a (x, y, z) sequence, or anything with x/y/z."""
    if value is None:
        return None
    try:
        if hasattr(value, "x"):
            return (float(value.x), float(value.y), float(value.z))
        x, y, z = value
        return (float(x), float(y), float(z))
    except Exception:
        return None


@dataclass
class SpawnPoint:
    x: float
    y: float
    z: float
    seen: int = 1
    last_used: float = -1e9

    @property
    def pos(self) -> Vec:
        return (self.x, self.y, self.z)


class SpawnPointBook:
    """Per-map spawn points learned from real engine spawns, persisted to JSON."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.maps: dict[str, list[SpawnPoint]] = {}
        self.dirty = False
        self.last_save = 0.0
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for name, rows in (data.get("maps") or {}).items():
                points = []
                for row in rows:
                    if isinstance(row, (list, tuple)) and len(row) >= 3:
                        seen = int(row[3]) if len(row) >= 4 else 1
                        points.append(SpawnPoint(float(row[0]), float(row[1]), float(row[2]), seen))
                self.maps[str(name).lower()] = points[:MAX_POINTS_PER_MAP]
        except Exception:
            self.maps = {}

    def points(self, map_name: str) -> list[SpawnPoint]:
        return self.maps.get(str(map_name).lower(), [])

    def learn(self, map_name: str, position) -> bool:
        """Record an engine-chosen spawn. Returns True when it is a new point."""
        pos = as_vec(position)
        if pos is None or not map_name:
            return False
        if pos == (0.0, 0.0, 0.0):
            return False  # unset/placeholder origin, not a real spawn
        points = self.maps.setdefault(str(map_name).lower(), [])
        for point in points:
            if distance(point.pos, pos) <= MERGE_RADIUS:
                point.seen += 1
                return False
        if len(points) >= MAX_POINTS_PER_MAP:
            return False
        points.append(SpawnPoint(*pos))
        self.dirty = True
        return True

    def save(self, force: bool = False) -> bool:
        now = time.time()
        if not self.dirty or (not force and now - self.last_save < SAVE_INTERVAL):
            return False
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "maps": {
                    name: [[round(p.x, 1), round(p.y, 1), round(p.z, 1), p.seen] for p in points]
                    for name, points in sorted(self.maps.items())
                },
            }
            temp = self.path.with_name(self.path.name + ".tmp")
            temp.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
            temp.replace(self.path)
            self.dirty = False
            self.last_save = now
            return True
        except Exception:
            return False


@dataclass(frozen=True)
class PlacementRequest:
    human_pos: Vec
    human_vel: Optional[Vec]
    occupants: Sequence[Vec]
    preferred: float                 # ideal distance from the player
    max_distance: float
    flank: bool = False
    min_distance: float = MIN_PLAYER_DISTANCE


@dataclass(frozen=True)
class PlacementDecision:
    point: Optional[SpawnPoint]
    moved: bool
    reason: str
    distance: float = 0.0


def _heading(vel: Optional[Vec]) -> Optional[tuple[float, float]]:
    if vel is None:
        return None
    speed = math.hypot(vel[0], vel[1])
    if speed < MOVING_SPEED:
        return None
    return (vel[0] / speed, vel[1] / speed)


def _clear_of(pos: Vec, occupants: Iterable[Vec]) -> bool:
    return all(distance(pos, other) >= MIN_OCCUPANT_SPACING for other in occupants)


def acceptable(pos: Vec, request: PlacementRequest) -> bool:
    d = distance(pos, request.human_pos)
    return request.min_distance <= d <= request.max_distance and _clear_of(pos, request.occupants)


def score_point(point: SpawnPoint, request: PlacementRequest, now: float) -> Optional[float]:
    """Lower is better; None when the point is not allowed at all."""
    if not acceptable(point.pos, request):
        return None
    d = distance(point.pos, request.human_pos)
    score = abs(d - request.preferred) / max(1.0, request.preferred)
    if now - point.last_used < RECENT_USE_SECONDS:
        score += 1.0
    heading = _heading(request.human_vel)
    if heading is not None:
        dx, dy = point.x - request.human_pos[0], point.y - request.human_pos[1]
        length = math.hypot(dx, dy) or 1.0
        cos = (dx / length) * heading[0] + (dy / length) * heading[1]
        if request.flank:
            # Behind (cos=-1) is best, beside is fine, straight ahead is worst.
            score += (cos + 1.0) * 0.6
        else:
            # Front-liners should be met, not appear behind the player.
            score += max(0.0, -cos) * 0.3
    return score


def choose_spawn(
    points: Sequence[SpawnPoint],
    engine_pos: Optional[Vec],
    request: PlacementRequest,
    *,
    now: Optional[float] = None,
    rng: Optional[random.Random] = None,
) -> PlacementDecision:
    """Decide whether to keep the engine's spawn or move to a learned point.

    The engine's choice is kept whenever it is already fair and useful (in the
    distance band, clear of other players, and not a flank request), so the
    Director only intervenes when it adds something.
    """
    now = time.time() if now is None else now
    rng = rng or random
    if len(points) < MIN_POINTS_FOR_PLACEMENT:
        return PlacementDecision(None, False, "too_few_points")
    if engine_pos is not None and not request.flank and acceptable(engine_pos, request):
        return PlacementDecision(None, False, "engine_choice_ok", distance(engine_pos, request.human_pos))
    scored = []
    for point in points:
        score = score_point(point, request, now)
        if score is not None:
            scored.append((score + rng.random() * 0.05, point))
    if not scored:
        return PlacementDecision(None, False, "no_fair_point")
    scored.sort(key=lambda item: item[0])
    best = scored[0][1]
    best.last_used = now
    return PlacementDecision(best, True, "flank" if request.flank else "band", distance(best.pos, request.human_pos))
