from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import math
import random
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True)
class RoleSpec:
    name: str
    weapons: tuple[int, ...]
    primary: int
    description: str


ROLE_SPECS: dict[str, RoleSpec] = {
    "chaser": RoleSpec("Chaser", (3, 5), 5, "close/medium rocket and shotgun pressure"),
    "gunner": RoleSpec("Gunner", (6, 8), 6, "sustained LG/plasma tracking pressure"),
    "marksman": RoleSpec("Marksman", (2, 7), 7, "limited long-range rail pressure"),
    "bruiser": RoleSpec("Bruiser", (3, 5), 3, "readable close-range pressure"),
    "skirmisher": RoleSpec("Skirmisher", (2, 5, 6), 5, "mobile mixed pressure"),
    "berserker": RoleSpec("Berserker", (1, 3), 3, "short-range disruption"),
    "target": RoleSpec("Target", (2, 3), 3, "objective target with modest self-defense"),
    "boss": RoleSpec("Boss", (5,), 5, "objective-owned boss loadout"),
}


@dataclass(frozen=True)
class DirectorProfile:
    name: str
    pressure_low: float
    pressure_high: float
    max_engaged: int
    idle_timeout: float
    far_distance: float
    engage_distance: float
    recovery_cooldown: float
    recovery_floor: float
    skill_cap: int
    recovery_enabled: bool
    roles: tuple[str, ...]


BASE_PROFILES: dict[str, DirectorProfile] = {
    "horde": DirectorProfile("Hunt", 42, 68, 3, 6.0, 1900, 950, 8.0, 2.5, 4, True,
        ("chaser", "gunner", "chaser", "skirmisher", "bruiser", "gunner", "marksman")),
    "arena_run": DirectorProfile("Roguelite", 40, 66, 3, 7.0, 1900, 950, 9.0, 3.0, 5, False,
        ("skirmisher", "chaser", "gunner", "bruiser", "marksman")),
    "gun_game": DirectorProfile("Flow", 48, 72, 3, 4.5, 1650, 900, 7.0, 1.5, 4, True,
        ("skirmisher", "chaser", "gunner", "skirmisher")),
    "boss_rush": DirectorProfile("Duel", 34, 58, 1, 8.0, 2200, 1150, 10.0, 3.5, 5, False, ("boss",)),
    "wipeout_solo": DirectorProfile("Squad", 42, 67, 3, 7.0, 1900, 950, 9.0, 2.5, 5, False,
        ("chaser", "gunner", "skirmisher", "bruiser")),
    "gauntlet_run": DirectorProfile("Trial", 40, 66, 3, 7.0, 1900, 950, 9.0, 2.5, 5, False,
        ("skirmisher", "chaser", "gunner", "marksman")),
    "last_stand": DirectorProfile("Siege", 48, 75, 4, 5.5, 1800, 900, 7.5, 2.0, 5, False,
        ("chaser", "gunner", "bruiser", "skirmisher", "chaser")),
    "one_life": DirectorProfile("Tension", 37, 60, 2, 6.5, 1850, 950, 9.0, 3.5, 4, False,
        ("skirmisher", "chaser", "gunner")),
    "bounty_hunt": DirectorProfile("Escort", 40, 64, 3, 6.0, 1850, 950, 8.5, 2.5, 4, False,
        ("skirmisher", "bruiser", "gunner", "chaser")),
    "rocket_tag": DirectorProfile("Chase", 44, 68, 3, 5.0, 1750, 950, 8.0, 2.0, 4, False,
        ("chaser", "chaser", "skirmisher")),
    "movement_hunter": DirectorProfile("Pursuit", 39, 62, 3, 5.5, 1800, 950, 8.0, 3.0, 4, False,
        ("chaser", "skirmisher", "gunner")),
    "predator": DirectorProfile("Swarm", 50, 78, 4, 5.0, 1750, 900, 7.0, 1.5, 5, False,
        ("chaser", "skirmisher", "gunner", "chaser", "bruiser")),
    "accuracy_trial": DirectorProfile("Target", 28, 48, 2, 7.0, 1750, 1000, 10.0, 4.0, 3, False,
        ("target", "skirmisher", "target")),
    "speedrun_combat": DirectorProfile("Feed", 52, 78, 4, 4.0, 1600, 900, 6.5, 1.0, 4, True,
        ("chaser", "skirmisher", "gunner", "chaser")),
    "random_loadout": DirectorProfile("Improvisation", 40, 65, 3, 6.0, 1850, 950, 8.5, 2.5, 4, False,
        ("skirmisher", "chaser", "gunner", "marksman")),
}


DIFFICULTY_ADJUSTMENTS = {
    "easy": (-8.0, -8.0, -1, 1.5, 1.5, -1),
    "normal": (0.0, 0.0, 0, 0.0, 0.0, 0),
    "hard": (5.0, 7.0, 1, -0.8, -0.5, 0),
    "nightmare": (8.0, 10.0, 1, -1.2, -0.8, 1),
}


def profile_for(mode: str, difficulty: str = "normal") -> DirectorProfile:
    base = BASE_PROFILES.get(mode, BASE_PROFILES["horde"])
    low, high, engaged, idle, recovery, skill = DIFFICULTY_ADJUSTMENTS.get(
        str(difficulty).lower(), DIFFICULTY_ADJUSTMENTS["normal"]
    )
    return DirectorProfile(
        name=base.name,
        pressure_low=max(10.0, base.pressure_low + low),
        pressure_high=max(20.0, base.pressure_high + high),
        max_engaged=max(1, base.max_engaged + engaged),
        idle_timeout=max(3.0, base.idle_timeout + idle),
        far_distance=base.far_distance,
        engage_distance=base.engage_distance,
        recovery_cooldown=base.recovery_cooldown,
        recovery_floor=max(0.5, base.recovery_floor + recovery),
        skill_cap=max(1, min(5, base.skill_cap + skill)),
        recovery_enabled=base.recovery_enabled,
        roles=base.roles,
    )


@dataclass
class BotTrack:
    client_id: int
    name: str
    role: str
    spawned_at: float
    last_contact: float
    last_recovery: float = -1e9
    distance: Optional[float] = None
    damage_dealt: float = 0.0
    damage_received: float = 0.0


@dataclass(frozen=True)
class DirectorAction:
    kind: str
    reason: str
    bot_id: Optional[int] = None
    role: Optional[str] = None
    duration: float = 0.0


@dataclass(frozen=True)
class DirectorSnapshot:
    mode: str
    profile: str
    pressure: float
    pressure_low: float
    pressure_high: float
    alive: int
    engaged: int
    idle: int
    far: int
    player_health: int
    player_armor: int
    recent_damage_taken: float
    recent_damage_dealt: float
    hold_until: float

    def summary(self) -> str:
        return (
            f"{self.profile} pressure={self.pressure:.0f} target={self.pressure_low:.0f}-{self.pressure_high:.0f} "
            f"engaged={self.engaged}/{self.alive} idle={self.idle} far={self.far} "
            f"hp={self.player_health}+{self.player_armor}"
        )


class SoloDirector:
    """Pure encounter director.

    It never aims, fires, paths, or directly buffs damage. It observes pressure
    and emits bounded encounter-management actions for the runtime adapter.
    """

    def __init__(self, mode: str, difficulty: str, seed: int):
        self.mode = str(mode)
        self.difficulty = str(difficulty).lower()
        self.seed = int(seed)
        self.profile = profile_for(self.mode, self.difficulty)
        self.objective_serial = 0
        self.tracks: dict[int, BotTrack] = {}
        self.damage_taken: deque[tuple[float, float]] = deque()
        self.damage_dealt: deque[tuple[float, float]] = deque()
        self.hold_until = 0.0
        self.last_action_at = -1e9
        self.last_snapshot: Optional[DirectorSnapshot] = None

    def begin_objective(self, now: float) -> None:
        self.objective_serial += 1
        self.tracks.clear()
        self.damage_taken.clear()
        self.damage_dealt.clear()
        self.hold_until = 0.0
        self.last_action_at = float(now) - 999.0
        self.last_snapshot = None

    def reset(self) -> None:
        self.tracks.clear()
        self.damage_taken.clear()
        self.damage_dealt.clear()
        self.hold_until = 0.0
        self.last_snapshot = None

    def role_for_spawn(self, spawn_index: int, *, special: Optional[str] = None) -> str:
        if special in ROLE_SPECS:
            return str(special)
        roles = self.profile.roles or ("skirmisher",)
        rng = random.Random(self.seed + self.objective_serial * 1009 + int(spawn_index) * 9176)
        # Use deterministic jitter rather than a fixed repeating sequence so
        # seeded runs are reproducible without every wave looking identical.
        base_index = int(spawn_index) % len(roles)
        jitter = rng.randrange(len(roles)) if len(roles) > 1 else 0
        return roles[(base_index + jitter) % len(roles)]

    def register_bot(self, client_id: int, name: str, role: str, now: float) -> None:
        now = float(now)
        self.tracks[int(client_id)] = BotTrack(
            int(client_id), str(name), role if role in ROLE_SPECS else "skirmisher", now, now
        )

    def forget_bot(self, client_id: int) -> None:
        self.tracks.pop(int(client_id), None)

    def note_damage(
        self,
        *,
        now: float,
        damage: float,
        attacker_id: Optional[int],
        target_id: Optional[int],
        attacker_is_bot: bool,
        target_is_bot: bool,
    ) -> None:
        now = float(now)
        amount = max(0.0, float(damage))
        if attacker_is_bot and attacker_id in self.tracks:
            track = self.tracks[int(attacker_id)]
            track.last_contact = now
            track.damage_dealt += amount
        if target_is_bot and target_id in self.tracks:
            track = self.tracks[int(target_id)]
            track.last_contact = now
            track.damage_received += amount
        if attacker_is_bot and not target_is_bot:
            self.damage_taken.append((now, amount))
        elif target_is_bot and not attacker_is_bot:
            self.damage_dealt.append((now, amount))
        self._trim(now)

    def note_human_kill(self, now: float) -> None:
        # A kill is meaningful contact even if a damage event was not available.
        self.damage_dealt.append((float(now), 30.0))
        self._trim(float(now))

    def note_human_death(self, now: float) -> None:
        now = float(now)
        self.hold_until = max(self.hold_until, now + self.profile.recovery_floor)

    def should_hold_reinforcements(self, now: float) -> bool:
        return float(now) < self.hold_until

    def reinforcement_delay(self, now: float, base_delay: float) -> float:
        now = float(now)
        if not self.should_hold_reinforcements(now):
            return float(base_delay)
        return max(float(base_delay), self.hold_until - now)

    def tick(
        self,
        *,
        now: float,
        player_health: int,
        player_armor: int,
        bots: Iterable[Mapping[str, object]],
        active: bool,
    ) -> tuple[DirectorSnapshot, list[DirectorAction]]:
        now = float(now)
        self._trim(now)
        bot_rows = list(bots)
        living_ids: set[int] = set()
        engaged = idle = far = 0

        for row in bot_rows:
            try:
                cid = int(row["id"])
            except Exception:
                continue
            living_ids.add(cid)
            track = self.tracks.get(cid)
            if track is None:
                self.register_bot(cid, str(row.get("name", "bot")), str(row.get("role", "skirmisher")), now)
                track = self.tracks[cid]
            distance = row.get("distance")
            try:
                track.distance = None if distance is None else max(0.0, float(distance))
            except Exception:
                track.distance = None
            contact_age = max(0.0, now - track.last_contact)
            is_engaged = contact_age <= 3.0 or (track.distance is not None and track.distance <= self.profile.engage_distance)
            is_far = track.distance is not None and track.distance >= self.profile.far_distance
            is_idle = contact_age >= self.profile.idle_timeout and (
                track.distance is None or track.distance > self.profile.engage_distance
            )
            engaged += int(is_engaged)
            far += int(is_far)
            idle += int(is_idle)

        for cid in list(self.tracks):
            if cid not in living_ids:
                self.tracks.pop(cid, None)

        recent_taken = sum(value for ts, value in self.damage_taken if now - ts <= 4.0)
        recent_dealt = sum(value for ts, value in self.damage_dealt if now - ts <= 4.0)
        near_count = sum(
            1 for track in self.tracks.values()
            if track.distance is not None and track.distance <= self.profile.engage_distance * 0.65
        )
        pressure = min(100.0, engaged * 15.0 + near_count * 5.0 + min(28.0, recent_taken * 0.22))
        if int(player_health) + int(player_armor) <= 65:
            pressure = min(100.0, pressure + 8.0)

        snapshot = DirectorSnapshot(
            mode=self.mode,
            profile=self.profile.name,
            pressure=pressure,
            pressure_low=self.profile.pressure_low,
            pressure_high=self.profile.pressure_high,
            alive=len(living_ids),
            engaged=engaged,
            idle=idle,
            far=far,
            player_health=max(0, int(player_health)),
            player_armor=max(0, int(player_armor)),
            recent_damage_taken=recent_taken,
            recent_damage_dealt=recent_dealt,
            hold_until=self.hold_until,
        )
        self.last_snapshot = snapshot
        actions: list[DirectorAction] = []

        if not active:
            return snapshot, actions

        # A large damage burst or critically low stack earns a short recovery
        # window. We hold future reinforcement; enemies already fighting remain
        # untouched so the adaptation is not visible mid-shot.
        if recent_taken >= 70.0 or int(player_health) + int(player_armor) <= 45:
            new_hold = now + self.profile.recovery_floor
            if new_hold > self.hold_until + 0.25:
                self.hold_until = new_hold
                actions.append(DirectorAction(
                    "hold_reinforcements",
                    "player under severe recent pressure",
                    duration=self.profile.recovery_floor,
                ))

        if (
            self.profile.recovery_enabled
            and pressure < self.profile.pressure_low
            and now - self.last_action_at >= 1.0
        ):
            candidates: list[tuple[float, int, BotTrack]] = []
            for cid, track in self.tracks.items():
                contact_age = now - track.last_contact
                too_far = track.distance is not None and track.distance >= self.profile.far_distance
                idle_long = contact_age >= self.profile.idle_timeout
                cooled_down = now - track.last_recovery >= self.profile.recovery_cooldown
                if cooled_down and (too_far or idle_long):
                    distance_score = (track.distance or 0.0) / max(1.0, self.profile.far_distance)
                    candidates.append((contact_age + distance_score * 2.0, cid, track))
            if candidates:
                _, cid, track = max(candidates, key=lambda item: item[0])
                track.last_recovery = now
                self.last_action_at = now
                actions.append(DirectorAction(
                    "recover_bot",
                    f"{track.role} non-contributing for {max(0.0, now-track.last_contact):.1f}s",
                    bot_id=cid,
                    role=track.role,
                ))

        return snapshot, actions

    def _trim(self, now: float) -> None:
        cutoff = float(now) - 8.0
        while self.damage_taken and self.damage_taken[0][0] < cutoff:
            self.damage_taken.popleft()
        while self.damage_dealt and self.damage_dealt[0][0] < cutoff:
            self.damage_dealt.popleft()


def distance_between(a: Optional[tuple[float, float, float]], b: Optional[tuple[float, float, float]]) -> Optional[float]:
    if a is None or b is None:
        return None
    try:
        return math.sqrt(sum((float(x) - float(y)) ** 2 for x, y in zip(a, b)))
    except Exception:
        return None
