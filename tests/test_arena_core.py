import tempfile
import unittest
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugins"))
from solo_core import UPGRADE_BY_ID, advance_round, load_state, new_state, pick_upgrade, roll_upgrade_choices, round_plan, save_state, upgrade_effects

class ArenaCoreTests(unittest.TestCase):
    def test_choices_are_deterministic(self):
        a=new_state(1234); b=new_state(1234); self.assertEqual(roll_upgrade_choices(a),roll_upgrade_choices(b)); self.assertEqual(len(a.choices),3)
    def test_only_runtime_backed_upgrades_are_exposed(self):
        for removed in ("regen_1","rocket_splash","rocket_jump","double_jump","dash"): self.assertNotIn(removed,UPGRADE_BY_ID)
    def test_pick_and_synergy(self):
        state=new_state(1); state.upgrades.update({"lg_damage":2,"lg_overcharge":1}); state.choices=["lg_vampire"]; state.waiting_for_pick=True; result=pick_upgrade(state,1); self.assertEqual(result["synergies"][0]["id"],"stormbringer"); self.assertGreater(upgrade_effects(state)["lg_mult"],0.2)
    def test_boss_every_fifth_round(self):
        state=new_state(4); state.round=5; self.assertTrue(round_plan(state)["boss"]); state.round=6; self.assertFalse(round_plan(state)["boss"])
    def test_persistence(self):
        state=new_state(9); state.upgrades["damage_1"]=2
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/"run.json"; save_state(path,state); loaded=load_state(path); self.assertEqual(loaded.seed,9); self.assertEqual(loaded.upgrades["damage_1"],2)
    def test_finite_completion(self):
        state=new_state(1,length=2); self.assertFalse(advance_round(state)); self.assertTrue(advance_round(state)); self.assertTrue(state.complete)

if __name__ == "__main__": unittest.main()
