from __future__ import annotations

from dataclasses import dataclass

WEAPON_SEQUENCE = (2, 3, 4, 8, 6, 5, 7, 1)
WEAPON_NAMES = {
    1: "Gauntlet", 2: "Machine Gun", 3: "Shotgun", 4: "Grenade Launcher",
    5: "Rocket Launcher", 6: "Lightning Gun", 7: "Railgun", 8: "Plasma Gun",
}


@dataclass
class GunGameState:
    """Weapon ladder: ``kills_per_tier`` kills per weapon, one Gauntlet kill to finish.

    A bot Gauntlet kill on the player demotes one tier (classic humiliation).
    """

    kills_per_tier: int = 2
    index: int = 0
    tier_kills: int = 0
    complete: bool = False
    demotions: int = 0

    @property
    def weapon(self) -> int:
        return WEAPON_SEQUENCE[min(self.index, len(WEAPON_SEQUENCE) - 1)]

    @property
    def weapon_name(self) -> str:
        return WEAPON_NAMES[self.weapon]

    @property
    def is_final_tier(self) -> bool:
        return self.index >= len(WEAPON_SEQUENCE) - 1

    @property
    def tier_goal(self) -> int:
        return 1 if self.is_final_tier else max(1, int(self.kills_per_tier))

    @property
    def total_kills_required(self) -> int:
        return (len(WEAPON_SEQUENCE) - 1) * max(1, int(self.kills_per_tier)) + 1

    def scored_kill(self) -> str:
        """Record a kill. Returns "complete", "advance" or "progress"."""
        if self.complete:
            return "complete"
        if self.is_final_tier:
            self.complete = True
            return "complete"
        self.tier_kills += 1
        if self.tier_kills >= self.tier_goal:
            self.index += 1
            self.tier_kills = 0
            return "advance"
        return "progress"

    def demote(self) -> bool:
        if self.complete or self.index <= 0:
            self.tier_kills = 0
            return False
        self.index -= 1
        self.tier_kills = 0
        self.demotions += 1
        return True
