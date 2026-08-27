from __future__ import annotations

from dataclasses import dataclass
import random

BOT_ROSTER = ("slash", "keel", "visor", "anarki", "sarge", "ranger", "doom", "bones")


@dataclass
class HordeState:
    seed: int
    wave: int = 1
    complete: bool = False

    def plan(self) -> dict:
        rng = random.Random(self.seed + self.wave * 31)
        count = min(9, 2 + self.wave // 2)
        skill = min(5, 2 + self.wave // 4)
        names = [rng.choice(BOT_ROSTER) for _ in range(count)]
        elite = self.wave % 5 == 0
        if elite:
            names.append("keel")
            skill = min(5, skill + 1)
        return {"wave": self.wave, "bots": names, "count": len(names), "skill": skill, "elite": elite}

    def clear_wave(self) -> None:
        self.wave += 1

    def player_died(self) -> None:
        self.complete = True
