from __future__ import annotations

import json
import math
import os
import random
import time
from pathlib import Path
from typing import Iterable, Mapping, Optional


SCHEMA_VERSION = 1
ROLE_PRESSURE = {
    "chaser": 1.18,
    "gunner": 1.14,
    "marksman": 0.82,
    "bruiser": 0.96,
    "skirmisher": 1.00,
    "berserker": 1.20,
    "target": 0.72,
    "boss": 1.00,
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def ema(old: Optional[float], new: float, weight: float = 0.24) -> float:
    if old is None:
        return float(new)
    weight = clamp(weight, 0.01, 1.0)
    return float(old) * (1.0 - weight) + float(new) * weight


def composition_key(roles: Iterable[str]) -> str:
    values = sorted(str(role) for role in roles if role)
    return "+".join(values) if values else "none"


class DirectorLearning:
    """Persistent, auditable learning layer for the Solo Director.

    The learner may shape encounter pacing and future role selection, but it has
    no authority over aim, bot damage, hidden player counters, or teleporting.
    Difficulty remains the outer safety envelope; learned pressure shifts are
    deliberately small and decay toward neutral over time.
    """

    def __init__(self, runtime_dir: Path, mode: str, difficulty: str, seed: int):
        self.runtime_dir = Path(runtime_dir)
        self.mode = str(mode)
        self.difficulty = str(difficulty).lower()
        self.seed = int(seed)
        self.player_file = self.runtime_dir / "director_player.json"
        self.playbook_file = self.runtime_dir / "director_playbook.json"
        self.sessions_dir = self.runtime_dir / "director_sessions"
        self.player = self._load_json(self.player_file, self._new_player())
        self.playbook = self._load_json(self.playbook_file, self._new_playbook())
        self.session_started = time.time()
        self.session_id = f"{time.strftime('%Y%m%dT%H%M%S', time.localtime(self.session_started))}-{self.mode}-{self.seed}"
        self.session_file = self.sessions_dir / f"{self.session_id}.jsonl"
        self.objective: Optional[dict] = None
        self.pending: dict[str, dict] = {}
        self.action_serial = 0
        self.finalized = False
        self.last_snapshot = None
        self._decay_old_model()
        self._session_event("session_start", difficulty=self.difficulty, seed=self.seed)

    # ---------- public model ----------
    def pressure_shift(self) -> float:
        mode = self.player.get("modes", {}).get(self.mode, {})
        if int(mode.get("objectives", 0)) < 3:
            return 0.0
        return clamp(float(mode.get("pressure_shift", 0.0)), -6.0, 6.0)

    def begin_objective(self, now: float) -> None:
        self.finish_objective(float(now), reason="next_objective")
        self.objective = {
            "started": float(now),
            "ticks": 0,
            "pressure_sum": 0.0,
            "low_ticks": 0,
            "high_ticks": 0,
            "severe_ticks": 0,
            "damage_taken_sum": 0.0,
            "damage_dealt_sum": 0.0,
            "roles_seen": set(),
        }
        self._session_event("objective_start", pressure_shift=self.pressure_shift())

    def note_snapshot(self, snapshot, roles: Iterable[str], now: float) -> list[dict]:
        self.last_snapshot = snapshot
        if self.objective is None:
            self.begin_objective(float(now))
        obj = self.objective
        if obj is not None:
            obj["ticks"] += 1
            obj["pressure_sum"] += float(snapshot.pressure)
            obj["low_ticks"] += int(float(snapshot.pressure) < float(snapshot.pressure_low))
            obj["high_ticks"] += int(float(snapshot.pressure) > float(snapshot.pressure_high))
            obj["severe_ticks"] += int(float(snapshot.recent_damage_taken) >= 70.0 or int(snapshot.player_health) + int(snapshot.player_armor) <= 45)
            obj["damage_taken_sum"] += float(snapshot.recent_damage_taken)
            obj["damage_dealt_sum"] += float(snapshot.recent_damage_dealt)
            obj["roles_seen"].update(str(role) for role in roles if role)
        return self.evaluate_pending(float(now), snapshot, roles)

    def record_role_outcome(self, role: str, damage_dealt: float, damage_received: float, killed_by_human: bool) -> None:
        role = str(role)
        mode_pb = self.playbook.setdefault("modes", {}).setdefault(self.mode, {})
        roles = mode_pb.setdefault("roles", {})
        row = roles.setdefault(role, {"attempts": 0, "dealt_ema": None, "received_ema": None, "efficiency_ema": None})
        row["attempts"] = int(row.get("attempts", 0)) + 1
        dealt = max(0.0, float(damage_dealt))
        received = max(0.0, float(damage_received))
        efficiency = clamp((dealt + 25.0) / (received + 50.0), 0.0, 2.0) / 2.0
        if killed_by_human:
            efficiency *= 0.92
        row["dealt_ema"] = ema(row.get("dealt_ema"), dealt)
        row["received_ema"] = ema(row.get("received_ema"), received)
        row["efficiency_ema"] = ema(row.get("efficiency_ema"), efficiency)
        row["last_seen"] = time.time()

    def choose_role(self, base_role: str, allowed_roles: Iterable[str], current_roles: Iterable[str], snapshot=None, *, special: bool = False) -> str:
        base_role = str(base_role)
        if special:
            return base_role
        allowed = sorted({str(role) for role in allowed_roles if role in ROLE_PRESSURE})
        if base_role not in allowed or len(allowed) < 2:
            return base_role
        mode_model = self.player.get("modes", {}).get(self.mode, {})
        if int(mode_model.get("objectives", 0)) < 2:
            return base_role

        current = list(current_roles)
        target_direction = 0
        if snapshot is not None:
            if float(snapshot.pressure) < float(snapshot.pressure_low) - 4.0:
                target_direction = 1
            elif float(snapshot.pressure) > float(snapshot.pressure_high) + 4.0:
                target_direction = -1

        role_stats = self.playbook.get("modes", {}).get(self.mode, {}).get("roles", {})
        comps = self.playbook.get("modes", {}).get(self.mode, {}).get("compositions", {})

        def score(role: str) -> float:
            value = 0.0
            row = role_stats.get(role, {})
            attempts = int(row.get("attempts", 0))
            if attempts >= 3 and row.get("efficiency_ema") is not None:
                confidence = min(1.0, attempts / 10.0)
                value += (float(row["efficiency_ema"]) - 0.5) * 0.34 * confidence
            key = composition_key(current + [role])
            comp = comps.get(key, {})
            c_attempts = int(comp.get("attempts", 0))
            if c_attempts >= 2 and comp.get("score_ema") is not None:
                confidence = min(1.0, c_attempts / 8.0)
                value += (float(comp["score_ema"]) - 0.5) * 0.28 * confidence
            if target_direction:
                value += target_direction * (ROLE_PRESSURE.get(role, 1.0) - 1.0) * 0.36
            if role == base_role:
                value += 0.06
            return value

        scored = sorted(((score(role), role) for role in allowed), reverse=True)
        best_score, best_role = scored[0]
        base_score = next(value for value, role in scored if role == base_role)
        # Learning must have a meaningful advantage before overriding the mode's
        # authored role plan. This keeps adaptation subtle and comprehensible.
        if best_role != base_role and best_score - base_score >= 0.08:
            return best_role
        return base_role

    def adjust_reinforcement_delay(self, base_delay: float, snapshot=None) -> float:
        delay = max(0.1, float(base_delay))
        snapshot = snapshot or self.last_snapshot
        if snapshot is None:
            return delay
        if float(snapshot.recent_damage_taken) >= 55.0 or float(snapshot.pressure) > float(snapshot.pressure_high):
            return min(3.0, delay + 0.45)
        if float(snapshot.pressure) < float(snapshot.pressure_low) - 10.0 and float(snapshot.recent_damage_taken) < 30.0:
            return max(0.18, delay * 0.82)
        return delay

    # ---------- action experiments ----------
    def open_action(self, action, snapshot, roles: Iterable[str], now: float) -> str:
        self.action_serial += 1
        action_id = f"{self.session_id}-{self.action_serial:04d}"
        kind = str(action.kind)
        expectation = "pressure_toward_target"
        if kind == "hold_reinforcements":
            expectation = "danger_falls_without_pressure_spike"
        experiment = {
            "id": action_id,
            "kind": kind,
            "reason": str(action.reason),
            "bot_id": action.bot_id,
            "role": action.role,
            "opened": float(now),
            "evaluate_after": float(now) + 4.5,
            "baseline_pressure": float(snapshot.pressure),
            "baseline_damage_taken": float(snapshot.recent_damage_taken),
            "baseline_effective_health": int(snapshot.player_health) + int(snapshot.player_armor),
            "target": [float(snapshot.pressure_low), float(snapshot.pressure_high)],
            "composition": composition_key(roles),
            "expectation": expectation,
            "execution": "pending",
        }
        self.pending[action_id] = experiment
        self._session_event("director_decision", **experiment)
        return action_id

    def mark_execution(self, action_id: str, result: str, **details) -> None:
        experiment = self.pending.get(str(action_id))
        if experiment is not None:
            experiment["execution"] = str(result)
            experiment["execution_details"] = details
        self._session_event("director_execution", action_id=action_id, result=str(result), **details)

    def evaluate_pending(self, now: float, snapshot, roles: Iterable[str]) -> list[dict]:
        evaluations: list[dict] = []
        for action_id, experiment in list(self.pending.items()):
            if float(now) < float(experiment.get("evaluate_after", 0.0)):
                continue
            before = float(experiment.get("baseline_pressure", 0.0))
            after = float(snapshot.pressure)
            low, high = experiment.get("target", [snapshot.pressure_low, snapshot.pressure_high])
            center = (float(low) + float(high)) / 2.0
            improvement = abs(before - center) - abs(after - center)
            danger = (
                after > float(high) + 12.0
                or float(snapshot.recent_damage_taken) > max(70.0, float(experiment.get("baseline_damage_taken", 0.0)) + 45.0)
            )
            kind = str(experiment.get("kind", ""))
            if kind == "hold_reinforcements":
                success = not danger and (
                    float(snapshot.recent_damage_taken) <= float(experiment.get("baseline_damage_taken", 0.0)) * 0.65
                    or after <= float(high)
                )
            else:
                success = not danger and (float(low) <= after <= float(high) or improvement >= 5.0)
            score = clamp(0.5 + improvement / 45.0 - (0.35 if danger else 0.0), 0.0, 1.0)
            evaluation = {
                "action_id": action_id,
                "kind": kind,
                "success": bool(success),
                "score": score,
                "pressure_before": before,
                "pressure_after": after,
                "pressure_improvement": improvement,
                "danger": bool(danger),
                "damage_taken_after": float(snapshot.recent_damage_taken),
                "composition_after": composition_key(roles),
                "execution": experiment.get("execution", "unknown"),
            }
            evaluations.append(evaluation)
            self._learn_from_experiment(experiment, evaluation)
            self._session_event("director_evaluation", **evaluation)
            self.pending.pop(action_id, None)
        if evaluations:
            self._save_models()
        return evaluations

    # ---------- objective/session learning ----------
    def finish_objective(self, now: float, *, reason: str) -> None:
        obj = self.objective
        if not obj or int(obj.get("ticks", 0)) <= 0:
            self.objective = None
            return
        ticks = max(1, int(obj["ticks"]))
        duration = max(0.0, float(now) - float(obj["started"]))
        avg_pressure = float(obj["pressure_sum"]) / ticks
        low_fraction = float(obj["low_ticks"]) / ticks
        high_fraction = float(obj["high_ticks"]) / ticks
        severe_fraction = float(obj["severe_ticks"]) / ticks
        avg_taken = float(obj["damage_taken_sum"]) / ticks
        avg_dealt = float(obj["damage_dealt_sum"]) / ticks
        score = clamp(1.0 - low_fraction * 0.45 - high_fraction * 0.35 - severe_fraction * 0.65, 0.0, 1.0)
        key = composition_key(obj["roles_seen"])
        self._update_composition(key, score, avg_pressure, severe_fraction)
        self._update_player_objective(avg_pressure, low_fraction, high_fraction, severe_fraction, avg_taken, avg_dealt, duration)
        self._session_event(
            "objective_result",
            reason=reason,
            duration=duration,
            average_pressure=avg_pressure,
            low_fraction=low_fraction,
            high_fraction=high_fraction,
            severe_fraction=severe_fraction,
            average_damage_taken=avg_taken,
            average_damage_dealt=avg_dealt,
            composition=key,
            score=score,
            learned_pressure_shift=self.pressure_shift(),
        )
        self.objective = None
        self._save_models()

    def finish_session(self, *, result: str, kills: int, deaths: int, duration: float, now: Optional[float] = None) -> None:
        if self.finalized:
            return
        now = time.time() if now is None else float(now)
        self.finish_objective(now, reason="session_end")
        mode = self.player.setdefault("modes", {}).setdefault(self.mode, {})
        mode["sessions"] = int(mode.get("sessions", 0)) + 1
        mode["kills_ema"] = ema(mode.get("kills_ema"), max(0, int(kills)))
        mode["deaths_ema"] = ema(mode.get("deaths_ema"), max(0, int(deaths)))
        mode["duration_ema"] = ema(mode.get("duration_ema"), max(0.0, float(duration)))
        self.player["sessions"] = int(self.player.get("sessions", 0)) + 1
        self.player["last_played"] = now
        self._session_event(
            "session_end",
            result=str(result),
            kills=max(0, int(kills)),
            deaths=max(0, int(deaths)),
            duration=max(0.0, float(duration)),
            pressure_shift=self.pressure_shift(),
        )
        self.finalized = True
        self._save_models()

    # ---------- private learning ----------
    def _update_player_objective(self, avg_pressure, low_fraction, high_fraction, severe_fraction, avg_taken, avg_dealt, duration):
        mode = self.player.setdefault("modes", {}).setdefault(self.mode, {})
        mode["objectives"] = int(mode.get("objectives", 0)) + 1
        mode["pressure_ema"] = ema(mode.get("pressure_ema"), avg_pressure)
        mode["low_fraction_ema"] = ema(mode.get("low_fraction_ema"), low_fraction)
        mode["high_fraction_ema"] = ema(mode.get("high_fraction_ema"), high_fraction)
        mode["danger_ema"] = ema(mode.get("danger_ema"), severe_fraction)
        mode["damage_taken_ema"] = ema(mode.get("damage_taken_ema"), avg_taken)
        mode["damage_dealt_ema"] = ema(mode.get("damage_dealt_ema"), avg_dealt)
        mode["objective_duration_ema"] = ema(mode.get("objective_duration_ema"), duration)

        shift = float(mode.get("pressure_shift", 0.0))
        delta = 0.0
        if severe_fraction >= 0.15 or high_fraction >= 0.42:
            delta = -0.65
        elif low_fraction >= 0.55 and avg_dealt > max(15.0, avg_taken * 1.10):
            delta = 0.45
        elif low_fraction >= 0.70:
            delta = 0.25
        mode["pressure_shift"] = clamp(shift + delta, -6.0, 6.0)
        mode["updated"] = time.time()

    def _update_composition(self, key: str, score: float, avg_pressure: float, danger: float) -> None:
        mode_pb = self.playbook.setdefault("modes", {}).setdefault(self.mode, {})
        comps = mode_pb.setdefault("compositions", {})
        row = comps.setdefault(key, {"attempts": 0, "success_ema": None, "score_ema": None, "pressure_ema": None, "danger_ema": None})
        row["attempts"] = int(row.get("attempts", 0)) + 1
        row["success_ema"] = ema(row.get("success_ema"), float(score >= 0.58))
        row["score_ema"] = ema(row.get("score_ema"), score)
        row["pressure_ema"] = ema(row.get("pressure_ema"), avg_pressure)
        row["danger_ema"] = ema(row.get("danger_ema"), danger)
        row["last_seen"] = time.time()

    def _learn_from_experiment(self, experiment: Mapping[str, object], evaluation: Mapping[str, object]) -> None:
        mode_pb = self.playbook.setdefault("modes", {}).setdefault(self.mode, {})
        actions = mode_pb.setdefault("actions", {})
        key = str(experiment.get("kind", "unknown"))
        row = actions.setdefault(key, {"attempts": 0, "success_ema": None, "score_ema": None})
        row["attempts"] = int(row.get("attempts", 0)) + 1
        row["success_ema"] = ema(row.get("success_ema"), float(bool(evaluation.get("success"))))
        row["score_ema"] = ema(row.get("score_ema"), float(evaluation.get("score", 0.0)))
        row["last_seen"] = time.time()

    def _decay_old_model(self) -> None:
        # Old preferences should inform a new session, not fossilize it. Decay
        # toward neutral each time the learning layer is constructed.
        mode = self.player.get("modes", {}).get(self.mode)
        if isinstance(mode, dict):
            mode["pressure_shift"] = float(mode.get("pressure_shift", 0.0)) * 0.985

    # ---------- persistence/debug ----------
    def _session_event(self, kind: str, **payload) -> None:
        try:
            self.sessions_dir.mkdir(parents=True, exist_ok=True)
            row = {"schema": SCHEMA_VERSION, "time": time.time(), "kind": str(kind), "mode": self.mode}
            row.update(payload)
            with self.session_file.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True, default=self._json_default) + "\n")
        except Exception:
            pass

    def _save_models(self) -> None:
        self.player["schema"] = SCHEMA_VERSION
        self.playbook["schema"] = SCHEMA_VERSION
        self._atomic_json(self.player_file, self.player)
        self._atomic_json(self.playbook_file, self.playbook)

    @staticmethod
    def _new_player() -> dict:
        return {"schema": SCHEMA_VERSION, "sessions": 0, "modes": {}}

    @staticmethod
    def _new_playbook() -> dict:
        return {"schema": SCHEMA_VERSION, "modes": {}}

    @staticmethod
    def _load_json(path: Path, fallback: dict) -> dict:
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else fallback
        except Exception:
            return fallback

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
        try:
            path = Path(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(path.suffix + ".tmp")
            temp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=DirectorLearning._json_default) + "\n", encoding="utf-8")
            os.replace(temp, path)
        except Exception:
            pass

    @staticmethod
    def _json_default(value):
        if isinstance(value, set):
            return sorted(value)
        raise TypeError(f"not JSON serializable: {type(value).__name__}")
