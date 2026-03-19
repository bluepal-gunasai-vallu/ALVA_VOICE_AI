"""
analytics_routes.py — FastAPI routes for TC088–TC095 Monitoring & Analytics
Mount this in main.py:
    from backend.analytics_routes import router as analytics_router
    app.include_router(analytics_router)
"""

from fastapi import APIRouter
from backend.analytics import (
    # TC088
    record_call_outcome,
    get_call_success_rate,
    # TC089
    record_state_transition,
    get_state_transition_report,
    # TC090
    record_dropoff,
    get_dropoff_report,
    # TC091
    record_latency,
    get_latency_report,
    # TC092
    start_transcript,
    log_turn,
    end_transcript,
    get_transcript,
    get_all_transcripts,
    # TC093
    log_error,
    get_error_log,
    get_error_summary,
    # TC094
    get_pipeline_snapshot,
    # TC095
    get_containment_rate,
    # composite
    get_full_analytics_snapshot,
)

router = APIRouter(prefix="/analytics", tags=["analytics"])


# ─────────────────────────────────────────────
# TC088 — Call Success Rate
# ─────────────────────────────────────────────

@router.post("/call/outcome")
def post_call_outcome(call_id: str, outcome: str):
    """
    TC088: Record call outcome.
    outcome: 'success' | 'failed' | 'escalated'
    """
    record_call_outcome(call_id, outcome)
    return {"message": "Outcome recorded", "call_id": call_id, "outcome": outcome}


@router.get("/call/success-rate")
def call_success_rate():
    """TC088: Live call success rate with numerator & denominator."""
    return get_call_success_rate()


# ─────────────────────────────────────────────
# TC089 — State Transition Tracking
# ─────────────────────────────────────────────

@router.post("/transitions/record")
def post_state_transition(
    appointment_id: str,
    from_state: str,
    to_state: str,
    call_id: str = None,
):
    """TC089: Log a single FSM state transition."""
    record_state_transition(appointment_id, from_state, to_state, call_id)
    return {"message": "Transition recorded"}


@router.get("/transitions")
def state_transitions():
    """TC089: Per-transition counts and full transition log."""
    return get_state_transition_report()


# ─────────────────────────────────────────────
# TC090 — Drop-Off Detection
# ─────────────────────────────────────────────

@router.post("/dropoff")
def post_dropoff(call_id: str, dialogue_stage: str, turn_number: int = None):
    """TC090: Record a customer drop-off at a specific dialogue stage."""
    record_dropoff(call_id, dialogue_stage, turn_number)
    return {"message": "Drop-off recorded"}


@router.get("/dropoff")
def dropoff_report():
    """TC090: Top 3 drop-off points with counts."""
    return get_dropoff_report()


# ─────────────────────────────────────────────
# TC091 — Latency
# ─────────────────────────────────────────────

@router.post("/latency")
def post_latency(latency_ms: float, call_id: str = None, turn: int = None):
    """TC091: Record a single end-to-end turn latency (ms)."""
    record_latency(latency_ms, call_id, turn)
    return {"message": "Latency recorded", "latency_ms": latency_ms}


@router.get("/latency")
def latency_report():
    """TC091: P95 latency, KPI pass/fail, alert status."""
    return get_latency_report()


# ─────────────────────────────────────────────
# TC092 — Transcript Logging
# ─────────────────────────────────────────────

@router.post("/transcript/start")
def transcript_start(call_id: str, appointment_id: str = None, mask_pii: bool = True):
    """TC092: Initialise a transcript for a new call."""
    start_transcript(call_id, appointment_id, mask_pii)
    return {"message": "Transcript started", "call_id": call_id}


@router.post("/transcript/turn")
def transcript_turn(call_id: str, role: str, text: str):
    """TC092: Append a dialogue turn (role: 'alva'|'customer')."""
    log_turn(call_id, role, text)
    return {"message": "Turn logged"}


@router.post("/transcript/end")
def transcript_end(call_id: str):
    """TC092: Mark a transcript as complete."""
    end_transcript(call_id)
    return {"message": "Transcript ended", "call_id": call_id}


@router.get("/transcript/{call_id}")
def get_transcript_route(call_id: str):
    """TC092: Retrieve full transcript by call_id."""
    t = get_transcript(call_id)
    if not t:
        return {"error": "Transcript not found"}
    return t


@router.get("/transcripts")
def all_transcripts():
    """TC092: Return all stored transcripts."""
    return get_all_transcripts()


# ─────────────────────────────────────────────
# TC093 — Error Logging
# ─────────────────────────────────────────────

@router.post("/error")
def post_error(
    call_id: str,
    component: str,
    error_type: str,
    detail: str = "",
    recovery_action: str = "none",
):
    """TC093: Log a structured error event."""
    log_error(call_id, component, error_type, detail, recovery_action)
    return {"message": "Error logged"}


@router.get("/errors")
def error_log():
    """TC093: Full error log."""
    return get_error_log()


@router.get("/errors/summary")
def error_summary():
    """TC093: Error counts by component and type."""
    return get_error_summary()


# ─────────────────────────────────────────────
# TC094 — Appointment Pipeline
# ─────────────────────────────────────────────

@router.get("/pipeline")
def pipeline_snapshot():
    """TC094: Live appointment count per FSM state."""
    return get_pipeline_snapshot()


# ─────────────────────────────────────────────
# TC095 — Containment Rate KPI
# ─────────────────────────────────────────────

@router.get("/containment")
def containment_rate():
    """TC095: Containment rate KPI (target >= 80%)."""
    return get_containment_rate()


# ─────────────────────────────────────────────
# COMPOSITE SNAPSHOT (for dashboard polling)
# ─────────────────────────────────────────────

@router.get("/snapshot")
def full_snapshot():
    """Return all analytics metrics in one call (dashboard polling endpoint)."""
    return get_full_analytics_snapshot()