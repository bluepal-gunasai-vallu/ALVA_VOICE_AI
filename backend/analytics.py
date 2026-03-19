"""
analytics.py — Monitoring & Analytics Module
Covers TC088–TC095.

All data is now persisted to MySQL via analytics_db.py so history
survives server restarts and browser tab closes.
In-memory stores are kept as a fast write-through cache for the
current server session only.
"""

import time
import uuid
import math
from datetime import datetime
from typing import Optional

# ── DB layer (persistent storage) ──────────────────────
try:
    from backend.analytics_db import (
        db_record_call_outcome,
        db_get_call_success_rate,
        db_get_containment_rate,
        db_record_state_transition,
        db_get_state_transition_report,
        db_record_dropoff,
        db_get_dropoff_report,
        db_record_latency,
        db_get_latency_report,
        db_start_transcript,
        db_log_turn,
        db_end_transcript,
        db_get_transcript,
        db_get_all_transcripts,
        db_log_error,
        db_get_error_summary,
        db_get_full_analytics_snapshot,
    )
    _DB_AVAILABLE = True
except Exception as _db_import_err:
    print(f"[analytics] DB layer unavailable, falling back to in-memory: {_db_import_err}")
    _DB_AVAILABLE = False

# ══════════════════════════════════════════════════════
# IN-MEMORY FALLBACK STORES (used when DB unavailable)
# ══════════════════════════════════════════════════════

_call_outcomes: dict[str, str] = {}
_state_transitions: list[dict] = []
_dropoffs: list[dict] = []
_latency_records: list[float] = []
P95_ALERT_THRESHOLD_MS = 2500.0
_transcripts: dict[str, dict] = {}
_error_log: list[dict] = []
_contained_calls: set[str] = set()
_escalated_calls: set[str] = set()


# ══════════════════════════════════════════════════════
# TC088 – CALL SUCCESS RATE
# ══════════════════════════════════════════════════════

def record_call_outcome(call_id: str, outcome: str):
    """TC088: Record the outcome of a call — persisted to DB."""
    valid = {"success", "failed", "escalated"}
    if outcome not in valid:
        raise ValueError(f"outcome must be one of {valid}")

    if _DB_AVAILABLE:
        db_record_call_outcome(call_id, outcome)
    else:
        _call_outcomes[call_id] = outcome
        if outcome == "success":
            _contained_calls.add(call_id)
            _escalated_calls.discard(call_id)
        else:
            _escalated_calls.add(call_id)
            _contained_calls.discard(call_id)


def get_call_success_rate() -> dict:
    """TC088: Returns live call success rate — reads from DB."""
    if _DB_AVAILABLE:
        return db_get_call_success_rate()
    total = len(_call_outcomes)
    successes = sum(1 for v in _call_outcomes.values() if v == "success")
    rate = round(successes / total * 100, 1) if total > 0 else 0.0
    return {
        "success_count": successes, "total_calls": total,
        "success_rate_pct": rate,
        "last_updated": datetime.utcnow().isoformat() + "Z",
    }


# ══════════════════════════════════════════════════════
# TC089 – STATE TRANSITION TRACKING
# ══════════════════════════════════════════════════════

def record_state_transition(appointment_id, from_state: str, to_state: str, call_id: str = None):
    """TC089: Log every FSM state transition — persisted to DB."""
    if _DB_AVAILABLE:
        db_record_state_transition(appointment_id, from_state, to_state, call_id)
    else:
        _state_transitions.append({
            "appointment_id": str(appointment_id),
            "from_state": from_state, "to_state": to_state,
            "transition_key": f"{from_state}→{to_state}",
            "call_id": call_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })


def get_state_transition_report() -> dict:
    """TC089: Return per-transition counts — reads from DB."""
    if _DB_AVAILABLE:
        return db_get_state_transition_report()
    counts: dict[str, int] = {}
    for t in _state_transitions:
        k = t["transition_key"]
        counts[k] = counts.get(k, 0) + 1
    return {"transition_counts": counts, "total_transitions": len(_state_transitions)}


# ══════════════════════════════════════════════════════
# TC090 – DROP-OFF POINT DETECTION
# ══════════════════════════════════════════════════════

def record_dropoff(call_id: str, dialogue_stage: str, turn_number: int = None):
    """TC090: Record a customer drop-off — persisted to DB."""
    if _DB_AVAILABLE:
        db_record_dropoff(call_id, dialogue_stage, turn_number)
    else:
        _dropoffs.append({
            "call_id": call_id, "dialogue_stage": dialogue_stage,
            "turn_number": turn_number,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })


def get_dropoff_report() -> dict:
    """TC090: Return top 3 drop-off points — reads from DB."""
    if _DB_AVAILABLE:
        return db_get_dropoff_report()
    counts: dict[str, int] = {}
    for d in _dropoffs:
        s = d["dialogue_stage"]
        counts[s] = counts.get(s, 0) + 1
    sorted_stages = sorted(counts.items(), key=lambda x: x[1], reverse=True)
    top3 = [{"stage": s, "count": c} for s, c in sorted_stages[:3]]
    return {"top_dropoff_points": top3, "all_stage_counts": counts, "total_dropoffs": len(_dropoffs)}


# ══════════════════════════════════════════════════════
# TC091 – END-TO-END RESPONSE LATENCY
# ══════════════════════════════════════════════════════

def record_latency(latency_ms: float, call_id: str = None, turn: int = None):
    """TC091: Record a single turn latency — persisted to DB."""
    if _DB_AVAILABLE:
        db_record_latency(latency_ms, call_id, turn)
    else:
        _latency_records.append(latency_ms)


def get_latency_report() -> dict:
    """TC091: Return P95 latency — reads from DB."""
    if _DB_AVAILABLE:
        return db_get_latency_report()
    if not _latency_records:
        return {
            "p95_ms": None, "p50_ms": None, "avg_ms": None,
            "min_ms": None, "max_ms": None, "sample_count": 0,
            "alert_triggered": False, "kpi_passed": None,
        }
    sorted_lat = sorted(_latency_records)
    n = len(sorted_lat)
    def percentile(p):
        idx = math.ceil(p / 100 * n) - 1
        return round(sorted_lat[max(0, idx)], 2)
    p95 = percentile(95)
    return {
        "p95_ms": p95, "p50_ms": percentile(50),
        "avg_ms": round(sum(sorted_lat) / n, 2),
        "min_ms": round(sorted_lat[0], 2), "max_ms": round(sorted_lat[-1], 2),
        "sample_count": n, "kpi_passed": p95 <= 2000,
        "alert_triggered": p95 > P95_ALERT_THRESHOLD_MS,
        "alert_threshold_ms": P95_ALERT_THRESHOLD_MS,
    }


# ══════════════════════════════════════════════════════
# TC092 – CONVERSATION TRANSCRIPT LOGGING
# ══════════════════════════════════════════════════════

def _mask_pii(text: str) -> str:
    import re
    return re.sub(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", "[EMAIL_MASKED]", text)


def start_transcript(call_id: str, appointment_id=None, mask_pii: bool = True):
    """TC092: Initialise a transcript — persisted to DB."""
    if _DB_AVAILABLE:
        db_start_transcript(call_id, appointment_id, mask_pii)
    else:
        _transcripts[call_id] = {
            "call_id": call_id,
            "appointment_id": str(appointment_id) if appointment_id else None,
            "started_at": datetime.utcnow().isoformat() + "Z",
            "ended_at": None, "turns": [], "mask_pii": mask_pii,
        }


def log_turn(call_id: str, role: str, text: str, metadata: dict = None):
    """TC092: Append a dialogue turn — persisted to DB."""
    if _DB_AVAILABLE:
        db_log_turn(call_id, role, text)
    else:
        if call_id not in _transcripts:
            start_transcript(call_id)
        rec = _transcripts[call_id]
        content = _mask_pii(text) if rec.get("mask_pii") else text
        rec["turns"].append({
            "turn_index": len(rec["turns"]) + 1, "role": role,
            "text": content, "timestamp": datetime.utcnow().isoformat() + "Z",
            **(metadata or {}),
        })


def end_transcript(call_id: str):
    """TC092: Mark transcript as complete — persisted to DB."""
    if _DB_AVAILABLE:
        db_end_transcript(call_id)
    else:
        if call_id in _transcripts:
            _transcripts[call_id]["ended_at"] = datetime.utcnow().isoformat() + "Z"


def get_transcript(call_id: str) -> Optional[dict]:
    """TC092: Retrieve full transcript by call_id — reads from DB."""
    if _DB_AVAILABLE:
        return db_get_transcript(call_id)
    return _transcripts.get(call_id)


def get_all_transcripts() -> list[dict]:
    """TC092: Return all stored transcripts — reads from DB."""
    if _DB_AVAILABLE:
        return db_get_all_transcripts()
    return list(_transcripts.values())


# ══════════════════════════════════════════════════════
# TC093 – ERROR EVENT LOGGING
# ══════════════════════════════════════════════════════

def log_error(call_id: str, component: str, error_type: str,
              detail: str = "", recovery_action: str = "none"):
    """TC093: Log a structured error event — persisted to DB."""
    if _DB_AVAILABLE:
        db_log_error(call_id, component, error_type, detail, recovery_action)
    else:
        _error_log.append({
            "error_id": str(uuid.uuid4())[:8], "call_id": call_id,
            "component": component, "error_type": error_type,
            "detail": detail, "recovery_action_taken": recovery_action,
            "timestamp": datetime.utcnow().isoformat() + "Z",
        })


def get_error_log() -> list[dict]:
    if _DB_AVAILABLE:
        return db_get_error_summary().get("errors", [])
    return _error_log


def get_error_summary() -> dict:
    """TC093: Error counts grouped by component and type."""
    if _DB_AVAILABLE:
        return db_get_error_summary()
    by_component: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for e in _error_log:
        by_component[e["component"]] = by_component.get(e["component"], 0) + 1
        by_type[e["error_type"]]     = by_type.get(e["error_type"], 0) + 1
    return {"total_errors": len(_error_log), "by_component": by_component,
            "by_type": by_type, "errors": _error_log}


# ══════════════════════════════════════════════════════
# TC094 – APPOINTMENT PIPELINE VISIBILITY
# ══════════════════════════════════════════════════════

def get_pipeline_snapshot() -> dict:
    """TC094: Live appointment count per FSM state — always from live DB."""
    from backend.db import get_all_appointments
    appointments = get_all_appointments()
    state_counts: dict[str, int] = {
        "INQUIRY": 0, "TENTATIVE": 0, "CONFIRMED": 0,
        "RESCHEDULED": 0, "CANCELLED": 0, "NO_SHOW": 0, "COMPLETED": 0,
    }
    for a in appointments:
        s = (a.get("state") or "INQUIRY").upper()
        state_counts[s] = state_counts.get(s, 0) + 1
    return {
        "state_counts": state_counts, "total": len(appointments),
        "snapshot_at": datetime.utcnow().isoformat() + "Z",
    }


# ══════════════════════════════════════════════════════
# TC095 – CONTAINMENT RATE KPI
# ══════════════════════════════════════════════════════

def get_containment_rate() -> dict:
    """TC095: Containment rate — reads from DB."""
    if _DB_AVAILABLE:
        return db_get_containment_rate()
    total = len(_call_outcomes)
    contained = len(_contained_calls)
    escalated = len(_escalated_calls)
    rate = round(contained / total * 100, 1) if total > 0 else 0.0
    return {
        "contained_calls": contained, "escalated_calls": escalated,
        "total_calls": total, "containment_rate_pct": rate,
        "kpi_passed": rate >= 80.0 if total > 0 else None, "kpi_target_pct": 80.0,
    }


# ══════════════════════════════════════════════════════
# COMPOSITE DASHBOARD SNAPSHOT
# ══════════════════════════════════════════════════════

def get_full_analytics_snapshot() -> dict:
    """Return all analytics metrics in one call — all from persistent DB."""
    if _DB_AVAILABLE:
        return db_get_full_analytics_snapshot()
    return {
        "call_success_rate": get_call_success_rate(),
        "state_transitions": get_state_transition_report(),
        "dropoff_analysis":  get_dropoff_report(),
        "latency":           get_latency_report(),
        "errors":            get_error_summary(),
        "pipeline":          get_pipeline_snapshot(),
        "containment":       get_containment_rate(),
    }