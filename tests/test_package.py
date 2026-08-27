import json, tempfile, unittest, sys, gzip, base64
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def source_text(name):
    plain=ROOT/name
    stem=name.replace('.py','_impl.py.gz')
    parts=sorted(ROOT.glob(stem+'.b64part*'))
    if parts:
        raw=base64.b64decode(''.join(p.read_text().strip() for p in parts))
        return gzip.decompress(raw).decode('utf-8')
    return plain.read_text()

sys.path.insert(0,str(ROOT))
import launcher

class PackageTests(unittest.TestCase):
    def test_factory_ids_unique_and_presets_resolve(self):
        factories=json.loads((ROOT/'resources/qllauncher.factories').read_text()); ids=[x['id'] for x in factories]
        self.assertEqual(len(ids),len(set(ids)))
        available=set(ids)|{'ffa','iffa','duel','tdm','ca','ft','ift','ctf','ictf','oneflag','har','dom','ad','rr','infected','race','quadhog'}
        presets=json.loads((ROOT/'resources/presets.json').read_text())
        for name,p in presets.items():
            if p.get('normal_start'): continue
            self.assertIn(p['factory'],available,name)
    def test_every_arcade_card_has_preset(self):
        cards=json.loads((ROOT/'resources/arcade_modes.json').read_text()); presets=json.loads((ROOT/'resources/presets.json').read_text()); self.assertGreaterEqual(len(cards),15)
        for c in cards: self.assertIn(c['title'],presets)
    def test_solo_mode_count(self):
        modes=json.loads((ROOT/'resources/solo_modes.json').read_text()); self.assertEqual(len(modes),15); self.assertIn('arena_run',{m['id'] for m in modes})
    def test_chaos_cfg_generation(self):
        with tempfile.TemporaryDirectory() as td:
            game=Path(td); (game/'baseq3').mkdir(); cfg=launcher.write_session_cfg(game,'Weapon Chaos','weaponchaos','campgrounds',3,4); text=cfg.read_text(); self.assertIn('CHAOS:',text); self.assertIn('map_restart',text); self.assertIn('addbot',text)
    def test_map_ratings_cover_arcade_and_solo(self):
        ratings=json.loads((ROOT/'resources/mode_map_ratings.json').read_text()); arcade=json.loads((ROOT/'resources/arcade_modes.json').read_text()); solo=json.loads((ROOT/'resources/solo_modes.json').read_text())
        self.assertEqual({c['title'] for c in arcade},set(ratings['arcade'])); self.assertEqual({m['id'] for m in solo},set(ratings['solo']))
        for section in ('arcade','solo','arena_run_rounds','gauntlet_stages'):
            for _key,data in ratings[section].items(): self.assertTrue(data.get('5') or data.get('4'))
    def test_curated_map_resolver_matches_long_names(self):
        maps={'q3dm6':{'title':'Campgrounds','types':['ffa'],'sources':['Base']},'custom':{'title':'Overkill','types':['ca'],'sources':['Workshop 1']}}
        scores=launcher.curated_map_scores('arcade','Unholy Trinity',maps); self.assertEqual(scores['q3dm6'],5); self.assertEqual(scores['custom'],5)
    def test_gui_has_all_tabs(self):
        text=source_text('launcher_gui.py')
        for label in ('QUICK PLAY','ARCADE','SOLO','CUSTOM MATCH'): self.assertIn(f'label="{label}"',text)
    def test_solo_movement_session_and_bind_helpers(self):
        with tempfile.TemporaryDirectory() as td:
            game=Path(td); base=game/'baseq3'; base.mkdir(); user=game/'12345678901234567'/'baseq3'; user.mkdir(parents=True); cfg=user/'qzconfig.cfg'; cfg.write_text('bind A "+moveleft"\nbind D "+moveright"\nbind SPACE "+moveup"\n')
            left,right,originals=launcher.detect_strafe_keys(game); self.assertEqual((left,right),('A','D')); generated,_=launcher.write_solo_controls_cfg(game,True); text=generated.read_text(); self.assertIn('cmd qldash left',text); self.assertIn('cmd qldash right',text); self.assertNotIn('SPACE',text)
            cfg.write_text('bind A "+qll_side_left"\nbind D "+qll_side_right"\nbind SPACE "+moveup"\n'); self.assertTrue(launcher.restore_strafe_binds(game,originals)); restored=cfg.read_text(); self.assertIn('bind A "+moveleft"',restored); self.assertIn('bind D "+moveright"',restored); self.assertIn('bind SPACE "+moveup"',restored)
    def test_solo_movement_ui_present(self):
        text=source_text('launcher_gui.py'); self.assertIn("label='Air control'",text); self.assertIn('Side thrusters: ground dodge + air dash',text); self.assertIn("('Standard','Enhanced','High')",text)
    def test_ground_dash_hop_present(self):
        plugin=(ROOT/'solo_engine/plugins/solo_arcade.py').read_text(); self.assertIn('ground_dash_hop',plugin); self.assertIn("'THRUST' if was_airborne else 'DODGE'",plugin); self.assertIn('def request_side_dash',plugin); self.assertIn('quick dodge-hop',source_text('launcher_gui.py'))
    def test_detailed_solo_logging_present(self):
        text=source_text('launcher.py'); self.assertIn('SOLO_LOG_DIR',text); self.assertIn('write_solo_diagnostic_report',text); self.assertIn('status_callback',text)
        start=(ROOT/'solo_engine/start_solo.sh').read_text(); self.assertIn('HEALTH OK:',start); self.assertIn('PORT="${QLL_SOLO_PORT:-27960}"',start); self.assertIn('plugin handshake verified',start); self.assertIn('SHINQLX_LIB=',start)
        setup=(ROOT/'solo_engine/setup_solo_engine.sh').read_text(); self.assertIn('last_setup_log',setup)
        gui=source_text('launcher_gui.py')
        for label in ('VIEW LATEST LOG','RUN DIAGNOSTICS','OPEN LOG FOLDER'): self.assertIn(label,gui)
    def test_shinqlx_build_forces_nightly(self):
        setup=(ROOT/'solo_engine/setup_solo_engine.sh').read_text(); self.assertIn('export RUSTUP_TOOLCHAIN=nightly',setup); self.assertIn('cargo -Z help',setup); self.assertIn('Nightly Cargo preflight passed',setup); self.assertIn('pip install --upgrade -v shinqlx',setup); self.assertNotIn('rustup default nightly',setup)
    def test_solo_setup_reuses_existing_rust(self):
        setup=(ROOT/'solo_engine/setup_solo_engine.sh').read_text(); self.assertIn('Existing rustup found; skipping rustup installer download.',setup); self.assertIn('Nightly Rust toolchain already installed; skipping network install.',setup); self.assertIn('rustup +nightly component list --installed',setup); self.assertIn('export RUSTUP_TOOLCHAIN=nightly',setup)
    def test_solo_setup_configures_libclang(self):
        setup=(ROOT/'solo_engine/setup_solo_engine.sh').read_text(); self.assertIn('clang libclang-dev',setup); self.assertIn('Locating libclang for Rust bindgen',setup); self.assertIn('export LIBCLANG_PATH=',setup); self.assertIn('libclang selected:',setup)
    def test_solo_setup_progress_tracking(self):
        setup=(ROOT/'solo_engine/setup_solo_engine.sh').read_text(); self.assertIn('SETUP_PID="$RUNTIME/setup.pid"',setup); self.assertIn('SETUP_STATUS="$RUNTIME/setup_status"',setup); self.assertIn('set_status(){',setup); self.assertIn('Solo Engine setup is already running',setup)
        launcher_text=source_text('launcher.py'); self.assertIn('def solo_setup_pid()',launcher_text); self.assertIn('def solo_setup_status()',launcher_text)
        gui=source_text('launcher_gui.py'); self.assertIn('SOLO ENGINE SETUP RUNNING',gui); self.assertIn('GLib.timeout_add(1000,self.poll_solo_setup_status)',gui)
    def test_solo_single_player_contract(self):
        setup=(ROOT/'solo_engine/setup_solo_engine.sh').read_text()
        for token in ('set g_doWarmup "0"','set sv_warmupReadyPercentage "0"','set bot_minplayers "0"','set g_friendlyFire "0"'): self.assertIn(token,setup)
        start=(ROOT/'solo_engine/start_solo.sh').read_text(); self.assertIn('+set zmq_stats_enable 1',start); self.assertIn('+set bot_minplayers 0',start); self.assertIn('+map "$MAP" tdm',start)
        plugin=(ROOT/'solo_engine/plugins/solo_arcade.py').read_text(); self.assertIn('minqlx.allow_single_player(True)',plugin); self.assertIn('self.add_hook("player_loaded", self.handle_player_loaded)',plugin); self.assertIn('self._put_team(player, "red")',plugin); self.assertIn('self._put_team(player, "blue")',plugin)
    def test_all_generated_matches_are_solo_first(self):
        launcher_text=source_text('launcher.py'); self.assertIn('SINGLE_PLAYER_MATCH_CVARS = [',launcher_text)
        for token in ('set g_doWarmup "0"','set g_warmup "0"','set sv_warmupReadyPercentage "0"','set g_warmupDelay "0"','set bot_minplayers "0"'): self.assertIn(token,launcher_text)
        self.assertIn('lines.extend(SINGLE_PLAYER_MATCH_CVARS)',launcher_text)
    def test_v5_plugin_readiness_and_self_test_gate(self):
        plugin=(ROOT/'solo_engine/plugins/solo_arcade.py').read_text(); self.assertIn('PLUGIN_READY_FILE',plugin); self.assertIn('self._write_ready(True)',plugin)
        launcher_text=source_text('launcher.py'); self.assertIn('def solo_plugin_ready(',launcher_text); self.assertIn('SELF_TEST_OK',launcher_text)
        setup=(ROOT/'solo_engine/setup_solo_engine.sh').read_text(); self.assertIn('Running real QLDS/shinqlx/plugin self-test',setup); self.assertIn('SELF_TEST_OK',setup)
        selftest=(ROOT/'solo_engine/self_test.sh').read_text(); self.assertIn('QLL_SKIP_READY_CHECK=1',selftest); self.assertIn('27961',selftest)

if __name__=='__main__': unittest.main()
