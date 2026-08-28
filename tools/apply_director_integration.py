from pathlib import Path

p = Path('solo_engine/plugins/solo_arcade.py')
s = p.read_text(encoding='utf-8')
if 'DirectorRuntime' in s:
    print('director integration already present')
    raise SystemExit(0)

replacements = [
(
'''    from .solo_controller import Phase, SoloController
    from .solo_core import (
''',
'''    from .solo_controller import Phase, SoloController
    from .director_runtime import DirectorRuntime
    from .solo_core import (
'''),
(
'''    from solo_controller import Phase, SoloController
    from solo_core import (
''',
'''    from solo_controller import Phase, SoloController
    from director_runtime import DirectorRuntime
    from solo_core import (
'''),
(
'''        self.ground_ticks = {}

        self._require_runtime_contract()
''',
'''        self.ground_ticks = {}

        self.director_runtime = DirectorRuntime(self, self.mode, self.difficulty, self.seed, RUNTIME_DIR)

        self._require_runtime_contract()
'''),
(
'''        self.controller.enemy_ids.clear()
        self.preactive_dead_ids.clear()

    def _spawn_objective_bots(self, names, skill, *, auto_clear=True):
        names = list(names)
        self.clear_all_bots()
        self.pending_replacements = 0
''',
'''        self.controller.enemy_ids.clear()
        self.preactive_dead_ids.clear()
        if hasattr(self, "director_runtime"):
            self.director_runtime.reset()

    def _spawn_objective_bots(self, names, skill, *, auto_clear=True):
        names = list(names)
        self.clear_all_bots()
        self.director_runtime.begin_objective()
        self.pending_replacements = 0
'''),
(
'''    def _add_replacement_bot(self, name=None, skill=None, delay=0.35):
        token = self.controller.token()
        name = name or random.choice(BOT_ROSTER_RUNTIME)
        skill = self.skill if skill is None else skill
        @minqlx.delay(delay)
''',
'''    def _add_replacement_bot(self, name=None, skill=None, delay=0.35):
        token = self.controller.token()
        name = name or random.choice(BOT_ROSTER_RUNTIME)
        skill = self.skill if skill is None else skill
        delay = self.director_runtime.reinforcement_delay(delay)
        @minqlx.delay(delay)
'''),
(
'''            activated = self.controller.enemy_spawned(player.id)
            self._apply_bot_loadout(player)
''',
'''            activated = self.controller.enemy_spawned(player.id)
            role = self.director_runtime.bot_spawned(player)
            self._apply_bot_loadout(player)
'''),
(
'''                f"phase={self.controller.phase.value}"
            )
''',
'''                f"phase={self.controller.phase.value} role={role}"
            )
'''),
(
'''        phase_before = self.controller.phase
        killer_human = is_player_object(killer) and not is_bot(killer)
''',
'''        phase_before = self.controller.phase
        self.director_runtime.bot_died(victim, killer)
        killer_human = is_player_object(killer) and not is_bot(killer)
'''),
(
'''        self.player_deaths += 1
        if self.mode == "arena_run" and self.run:
''',
'''        self.director_runtime.human_died()
        self.player_deaths += 1
        if self.mode == "arena_run" and self.run:
'''),
(
'''    def _apply_bot_loadout(self, player):
        plan = self.current_plan or {}
        try:
''',
'''    def _apply_bot_loadout(self, player):
        plan = self.current_plan or {}
        if self.director_runtime.apply_bot_loadout(player, plan):
            return
        try:
'''),
(
'''    def handle_damage(self, target, attacker, damage, dflags, means_of_death):
        if self.mode != "arena_run" or not self.run:
''',
'''    def handle_damage(self, target, attacker, damage, dflags, means_of_death):
        try:
            self.director_runtime.note_damage(target, attacker, damage)
        except Exception as exc:
            self._log(f"director damage observation failed: {exc}")
        if self.mode != "arena_run" or not self.run:
'''),
(
'''            except Exception:
                continue

    # ---------- commands ----------
''',
'''            except Exception:
                continue
        try:
            self.director_runtime.tick()
        except Exception as exc:
            self._log(f"director tick failed: {exc}")

    # ---------- commands ----------
'''),
(
'''            f"alive={len(self.controller.enemy_ids)} spawns={self.controller.fulfilled_spawns}/{self.controller.expected_spawns}"
        )
''',
'''            f"alive={len(self.controller.enemy_ids)} spawns={self.controller.fulfilled_spawns}/{self.controller.expected_spawns} "
            f"director=[{self.director_runtime.summary()}]"
        )
'''),
]

for old, new in replacements:
    count = s.count(old)
    if count != 1:
        raise SystemExit(f'expected exactly one patch target, found {count}: {old[:100]!r}')
    s = s.replace(old, new)

p.write_text(s, encoding='utf-8')
print('director integration applied')
