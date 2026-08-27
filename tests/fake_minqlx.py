from __future__ import annotations

import heapq
import itertools
import sys
import types
from types import SimpleNamespace


class FakePlayer:
    def __init__(self, server, client_id, name, steam_id, team="free"):
        self.server = server
        self.id = client_id
        self.name = name
        self.steam_id = steam_id
        self.team = team
        self.health = 100
        self.armor = 0
        self.is_alive = True
        self._velocity = SimpleNamespace(x=0.0, y=0.0, z=0.0)
        self._ammo = SimpleNamespace(mg=0, sg=0, gl=0, rl=0, lg=0, rg=0, pg=0)
        self._weapons = {}
        self._weapon = 2
        self.tells = []
        self.centers = []

    def put(self, team): self.team = team
    def kick(self, reason=""): self.server.players.pop(self.id, None)
    def weapons(self, reset=False, **kwargs):
        if reset: self._weapons = {}
        self._weapons.update(kwargs); return SimpleNamespace(**self._weapons)
    def ammo(self, reset=False, **kwargs):
        if reset:
            for key in vars(self._ammo): setattr(self._ammo, key, 0)
        for key, value in kwargs.items(): setattr(self._ammo, key, value)
        return self._ammo
    def weapon(self, value=None):
        if value is not None: self._weapon=int(value); return True
        return self._weapon
    def powerups(self, **kwargs): return True
    def velocity(self, reset=False, **kwargs):
        if reset: self._velocity=SimpleNamespace(x=0.0,y=0.0,z=0.0)
        for key,value in kwargs.items(): setattr(self._velocity,key,float(value))
        return self._velocity
    def tell(self, message, **kwargs): self.tells.append(message)
    def center_print(self, message): self.centers.append(message)


class FakeServer:
    def __init__(self):
        self.players={}; self.next_client_id=1
        self.cvars={"zmq_stats_enable":"1","mapname":"campgrounds"}
        self.hooks={}; self.commands=[]; self.console=[]; self.messages=[]; self.scheduler=[]
        self._counter=itertools.count(); self.now=0.0
        self.game=SimpleNamespace(map="campgrounds", type_short="tdm")
        self.plugin=None; self.single_player_allowed=False
    def schedule(self, delay, func, args, kwargs): heapq.heappush(self.scheduler,(self.now+float(delay),next(self._counter),func,args,kwargs))
    def run_next(self):
        if not self.scheduler: return False
        when,_,func,args,kwargs=heapq.heappop(self.scheduler); self.now=when; func(*args,**kwargs); return True
    def run_all(self, max_steps=1000):
        steps=0
        while self.scheduler and steps<max_steps: self.run_next(); steps+=1
        if steps>=max_steps: raise RuntimeError("fake scheduler exceeded max_steps")
    def advance(self, seconds, max_steps=1000):
        target=self.now+seconds; steps=0
        while self.scheduler and self.scheduler[0][0]<=target and steps<max_steps: self.run_next(); steps+=1
        self.now=target
    def add_human(self, name="Human"):
        p=FakePlayer(self,0,name,76_561_198_000_000_001,"free"); self.players[p.id]=p; return p
    def add_bot(self, name, team="blue"):
        cid=self.next_client_id; self.next_client_id+=1
        p=FakePlayer(self,cid,name,90_000_000_000_000_000+cid,team); self.players[cid]=p; self.emit("player_spawn",p); return p
    def emit(self,event,*args):
        result=None
        for handler in list(self.hooks.get(event,[])): result=handler(*args)
        return result
    def death(self,victim,killer=None,data=None): victim.is_alive=False; self.emit("death",victim,killer,data or {})
    def console_command(self, command):
        self.commands.append(command); parts=str(command).split()
        if not parts: return
        if parts[0]=="addbot" and len(parts)>=2: self.add_bot(parts[1],parts[3] if len(parts)>=4 else "free")
        elif parts[0]=="kick" and len(parts)>=2:
            try: self.players.pop(int(parts[1]),None)
            except Exception: pass
        elif parts[0]=="map" and len(parts)>=2:
            self.game.map=parts[1]; self.cvars["mapname"]=parts[1]; factory=parts[2] if len(parts)>=3 else "tdm"; self.game.type_short=factory; self.emit("map",parts[1],factory)
        elif parts[0]=="set" and len(parts)>=3: self.cvars[parts[1]]=parts[2]


def install_fake_minqlx(server: FakeServer):
    module=types.ModuleType("minqlx")
    module.RET_STOP_ALL="STOP"; module.PRI_LOWEST=-100
    module.MOD_ROCKET=6; module.MOD_ROCKET_SPLASH=7; module.MOD_LIGHTNING=8; module.MOD_LIGHTNING_DISCHARGE=16
    module.MOD_RAILGUN=10; module.MOD_RAILGUN_HEADSHOT=31; module.MOD_PLASMA=9; module.MOD_PLASMA_SPLASH=14
    def delay(seconds):
        def decorate(func):
            def wrapped(*args,**kwargs): server.schedule(seconds,func,args,kwargs)
            wrapped.__name__=getattr(func,"__name__","delayed"); return wrapped
        return decorate
    class Plugin:
        def __init__(self): pass
        def add_hook(self,event,handler,priority=0): server.hooks.setdefault(event,[]).append(handler)
        def add_command(self,names,handler,**kwargs): return None
        @property
        def game(self): return server.game
        @classmethod
        def teams(cls):
            result={"free":[],"red":[],"blue":[],"spectator":[]}
            for player in server.players.values(): result.setdefault(player.team,[]).append(player)
            return result
        @classmethod
        def get_cvar(cls,name,return_type=str):
            value=server.cvars.get(name)
            if value is None: return None
            if return_type is int: return int(value)
            if return_type is bool: return bool(int(value))
            return value
        @classmethod
        def set_cvar(cls,name,value,flags=0): server.cvars[name]=str(value); return True
        @classmethod
        def msg(cls,message,**kwargs): server.messages.append(message)
    def allow_single_player(value): server.single_player_allowed=bool(value)
    module.Plugin=Plugin; module.delay=delay; module.allow_single_player=allow_single_player
    module.console_command=server.console_command; module.console_print=lambda text: server.console.append(text)
    sys.modules["minqlx"]=module; return module
