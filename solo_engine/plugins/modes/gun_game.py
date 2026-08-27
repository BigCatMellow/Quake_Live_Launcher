from __future__ import annotations

from dataclasses import dataclass

WEAPON_SEQUENCE = (2, 3, 4, 8, 6, 5, 7, 1)
WEAPON_NAMES = {
    1: "Gauntlet", 2: "Machine Gun", 3: "Shotgun", 4: "Grenade Launcher",
    5: "Rocket Launcher", 6: "Lightning Gun", 7: "Railgun", 8: "Plasma Gun",
}


@dataclass
class GunGameState:
    index: int = 0
    complete: bool = False

    @property
    def weapon(self) -> int:
        return WEAPON_SEQUENCE[min(self.index, len(WEAPON_SEQUENCE) - 1)]

    @property
    def weapon_name(self) -> str:
        return WEAPON_NAMES[self.weapon]

    def scored_kill(self) -> bool:
        if self.complete:
            return True
        if self.index >= len(WEAPON_SEQUENCE) - 1:
            self.complete = True
            return True
        self.index += 1
        return False
