#!/usr/bin/env python3
"""Symlink local/Workshop PK3s into the private QLDS baseq3 directory."""
from __future__ import annotations
import hashlib, json, os, sys
from pathlib import Path

APP_ID='282440'

def steam_roots():
    home=Path.home(); roots=[]
    for p in [home/'.local/share/Steam',home/'.steam/steam',home/'.var/app/com.valvesoftware.Steam/.local/share/Steam']:
        if p.exists() and p not in roots: roots.append(p)
    i=0
    import re
    while i<len(roots):
        r=roots[i]; i+=1
        vdf=r/'steamapps/libraryfolders.vdf'
        if not vdf.exists(): continue
        try:
            txt=vdf.read_text(errors='ignore')
            for m in re.finditer(r'"path"\s*"([^"]+)"',txt):
                q=Path(m.group(1).replace('\\\\','\\')).expanduser()
                if q.exists() and q not in roots: roots.append(q)
        except Exception: pass
    return roots

def candidates(game_dir: Path):
    seen=set()
    roots=[game_dir/'baseq3']
    try:
        roots += [x/'baseq3' for x in game_dir.iterdir() if x.is_dir() and x.name.isdigit()]
    except Exception: pass
    legacy=Path.home()/'.quakelive/quakelive/home/baseq3'
    if legacy.exists(): roots.append(legacy)
    for r in steam_roots():
        w=r/'steamapps/workshop/content'/APP_ID
        if w.exists(): roots.append(w)
    for root in roots:
        if not root.exists(): continue
        for dp,_,files in os.walk(root,followlinks=False):
            for f in files:
                if not f.lower().endswith('.pk3'): continue
                p=Path(dp)/f
                try: key=str(p.resolve())
                except Exception: key=str(p)
                if key not in seen:
                    seen.add(key); yield p

def main():
    if len(sys.argv)<3:
        print('usage: sync_maps.py <Quake Live game dir> <QLDS baseq3>'); return 2
    game=Path(sys.argv[1]).expanduser(); target=Path(sys.argv[2]).expanduser()
    target.mkdir(parents=True,exist_ok=True)
    manifest=target/'.qllauncher-map-links.json'
    old=[]
    try: old=json.loads(manifest.read_text()).get('links',[])
    except Exception: pass
    for name in old:
        p=target/name
        if p.is_symlink():
            try: p.unlink()
            except Exception: pass
    links=[]
    for src in candidates(game):
        digest=hashlib.sha1(str(src).encode()).hexdigest()[:10]
        name=f'qll_{digest}_{src.name}'
        dst=target/name
        try:
            dst.symlink_to(src.resolve())
            links.append(name)
        except Exception:
            pass
    manifest.write_text(json.dumps({'links':links},indent=2)+'\n')
    print(f'Synced {len(links)} local/Workshop PK3 packages to Solo Engine.')
    return 0
if __name__=='__main__': raise SystemExit(main())
