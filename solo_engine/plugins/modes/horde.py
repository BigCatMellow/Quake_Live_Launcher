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
        wave = self.wave
        count = min(9, 2 + wave // 2)
        skill = min(5, 2 + wave // 4)
        rng = random.Random(self.seed + wave * 31)
        roster = list(BOT_ROSTER)
        bots = rng.sample(roster, k=min(count, len(roster)))
        while len(bots) < count:
            bots.append(rng.choice(roster))
        elite = wave % 5 == 0
        if elite:
            if "keel" not in bots:
                bots[-1] = "keel"
            skill = min(5, skill + 1)
        return {"wave": wave, "count": len(bots), "skill": skill, "bots": bots, "elite": elite}

    def clear_wave(self) -> None:
        self.wave += 1

    def player_died(self) -> None:
        self.complete = True
