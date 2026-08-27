import json, tempfile, unittest
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'solo_engine/plugins'))
import solo_core as sc

class SoloCoreTests(unittest.TestCase):
    def test_upgrade_roll_is_seeded(self):
        a=sc.new_state(1234); b=sc.new_state(1234)
        self.assertEqual(sc.roll_upgrade_choices(a),sc.roll_upgrade_choices(b)); self.assertEqual(len(a.choices),3)
    def test_pick_and_effects(self):
        s=sc.new_state(42); s.choices=['damage_1','health_1','lg_damage']; s.waiting_for_pick=True
        result=sc.pick_upgrade(s,1); self.assertEqual(result['upgrade']['id'],'damage_1'); self.assertAlmostEqual(sc.upgrade_effects(s)['damage_mult'],0.10)
    def test_stormbringer_synergy(self):
        s=sc.new_state(1); s.upgrades={'lg_damage':2,'lg_overcharge':1}; s.choices=['lg_vampire']; s.waiting_for_pick=True
        result=sc.pick_upgrade(s,1); self.assertIn('stormbringer',s.synergies); self.assertTrue(result['synergies'])
    def test_boss_every_fifth_round(self):
        s=sc.new_state(1); s.round=4; self.assertFalse(sc.round_plan(s)['boss']); s.round=5; self.assertTrue(sc.round_plan(s)['boss']); self.assertGreater(sc.round_plan(s)['health'],500)
    def test_arena_run_round_themes(self):
        s=sc.new_state(1); s.round=4; self.assertEqual(sc.round_plan(s)['theme'],'lg'); s.round=5; self.assertEqual(sc.round_plan(s)['theme'],'boss'); s.round=6; self.assertEqual(sc.round_plan(s)['theme'],'rocket'); s.round=7; self.assertEqual(sc.round_plan(s)['theme'],'rail'); s.round=9; self.assertEqual(sc.round_plan(s)['theme'],'elite')
    def test_run_end_and_endless(self):
        s=sc.new_state(1,length=2); s.round=2; self.assertTrue(sc.advance_round(s)); self.assertTrue(s.complete)
        e=sc.new_state(1,length=0); e.round=100; self.assertFalse(sc.advance_round(e)); self.assertEqual(e.round,101)
    def test_persistence(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'run.json'; s=sc.new_state(9); s.upgrades={'health_1':2}; sc.save_state(path,s); loaded=sc.load_state(path); self.assertEqual(loaded.seed,9); self.assertEqual(loaded.upgrades['health_1'],2)

if __name__=='__main__': unittest.main()
