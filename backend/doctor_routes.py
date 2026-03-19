from fastapi import APIRouter, BackgroundTasks
from backend.db import (
    get_all_appointments,
    update_appointment_status,
    set_doctor_availability,
    get_doctor_availability,
    save_feedback_score,
    get_feedback_scores,
    get_average_feedback_score,
    save_followup_attempt,
    get_followup_attempts,
    mark_followup_skipped,
)

from backend.socket_manager import send_voice_message
from backend.google_calendar import create_doctor_block
from backend.db import get_last_appointment_by_email
from backend.google_calendar import delete_event
import asyncio

router = APIRouter()

# Max retry attempts before marking follow-up SKIPPED (TC085)
MAX_FOLLOWUP_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 30  # configurable retry delay


# ---------------- APPOINTMENTS ---------------- #

@router.get("/doctor/appointments")
def fetch_all():
    return get_all_appointments()


@router.put("/doctor/appointments/{appointment_id}")
def change_status(appointment_id: int, status: str):
    update_appointment_status(appointment_id, status)
    return {"message": "Status updated"}


# ---------------- DOCTOR SCHEDULE ---------------- #

@router.post("/doctor/availability")
def update_availability(date: str, start_time: str, end_time: str, status: str):
    set_doctor_availability(date, start_time, end_time, status)
    create_doctor_block(date, start_time, end_time, status)
    return {"message": "Schedule updated in DB and Google Calendar"}


@router.get("/doctor/availability")
def get_availability():
    return get_doctor_availability()


# ---------------- REMINDER ---------------- #

@router.post("/doctor/reminder")
async def send_reminder(id: int, email: str):

    appointments = get_all_appointments()

    for a in appointments:
        if a["id"] == id:
            date_time = a["date_time"]
            message = f"Reminder. Your appointment is scheduled on {date_time}. You can reschedule or cancel if needed."
            await send_voice_message(message, email, mode="reminder")
            break

    return {"message": "Reminder sent"}


# ---------------- COMPLETE APPOINTMENT + POST-APPOINTMENT FLOW (TC082, TC083, TC086) ---------------- #

async def _run_followup_flow(appointment_id: int, email: str, name: str, attempt: int = 1):
    """
    Internal helper: sends the post-appointment voice follow-up.
    Retries once (TC085) after RETRY_DELAY_SECONDS if unanswered.
    Does NOT trigger for CANCELLED appointments (TC086 — gated at call site).
    """
    opening_message = (
        f"Hello {name}, this is ALVA. We hope your appointment went well. "
        f"Could you please rate your experience on a scale of 1 to 5? "
        f"Also, would you like to book a follow-up appointment?"
    )

    await send_voice_message(
        opening_message,
        email,
        mode="post_appointment",
        appointment_id=appointment_id
    )

    save_followup_attempt(appointment_id, attempt, "SENT")


async def _followup_with_retry(appointment_id: int, email: str, name: str):
    """
    TC085: Attempt 1 → wait → Attempt 2 → if still no response mark SKIPPED.
    """
    # Attempt 1
    await _run_followup_flow(appointment_id, email, name, attempt=1)

    # Wait for configurable delay before retry
    await asyncio.sleep(RETRY_DELAY_SECONDS)

    # Check if feedback was already captured (patient responded to attempt 1)
    from backend.db import get_feedback_scores
    scores = get_feedback_scores()
    responded = any(str(s["id"]) == str(appointment_id) for s in scores)

    if responded:
        return  # Patient already responded — no retry needed

    # Attempt 2
    await _run_followup_flow(appointment_id, email, name, attempt=2)

    # Wait again
    await asyncio.sleep(RETRY_DELAY_SECONDS)

    # Final check — if still no response, mark SKIPPED (TC085)
    scores = get_feedback_scores()
    responded = any(str(s["id"]) == str(appointment_id) for s in scores)

    if not responded:
        mark_followup_skipped(appointment_id)
        save_followup_attempt(appointment_id, 3, "SKIPPED")


@router.post("/doctor/complete")
async def complete_appointment(
    id: int,
    email: str,
    name: str,
    background_tasks: BackgroundTasks
):
    """
    TC082, TC083, TC086:
    1. Mark appointment as COMPLETED.
    2. Trigger post-appointment follow-up voice call (feedback + rebooking offer).
    3. CANCELLED appointments are NEVER completed — the route returns an error.
    """
    appointments = get_all_appointments()
    appointment = next((a for a in appointments if a["id"] == id), None)

    if not appointment:
        return {"error": "Appointment not found"}

    # TC086: Do NOT trigger post-appointment flow for CANCELLED appointments
    if appointment["state"] == "CANCELLED":
        return {
            "error": "Cannot complete a cancelled appointment. Post-appointment flow skipped.",
            "state": "CANCELLED"
        }

    # Mark as COMPLETED
    update_appointment_status(id, "COMPLETED")

    # Schedule follow-up in background (non-blocking) with retry logic (TC085)
    background_tasks.add_task(_followup_with_retry, id, email, name)

    return {
        "message": f"Appointment {id} marked COMPLETED. Post-appointment follow-up initiated.",
        "appointment_id": id
    }


# ---------------- FEEDBACK ---------------- #

@router.post("/doctor/feedback")
async def ask_feedback(id: int, email: str, name: str = ""):
    """
    TC082/TC084: Post-appointment feedback trigger.
    Sends 1-5 rating prompt with mode=post_appointment.
    Fixed: was sending generic experience question with mode=feedback
    which collected free text only and triggered wrong flow.
    """
    display_name = name if name else email
    message = (
        f"Hello {display_name}, this is ALVA. We hope your appointment went well. "
        f"Could you please rate your experience on a scale of 1 to 5? "
        f"Also, would you like to book a follow-up appointment?"
    )
    await send_voice_message(
        message,
        email,
        mode="post_appointment",
        appointment_id=id
    )
    return {"message": "Post-appointment feedback request sent"}


@router.post("/doctor/feedback/score")
def submit_feedback_score(appointment_id: int, score: int, channel: str = "voice"):
    """
    TC084: Persist a 1–5 rating linked to an appointment_id.
    Stores feedback_score, feedback_timestamp, feedback_channel.
    """
    if score < 1 or score > 5:
        return {"error": "Score must be between 1 and 5"}

    save_feedback_score(appointment_id, score, channel)

    return {
        "message": "Feedback score saved",
        "appointment_id": appointment_id,
        "feedback_score": score,
        "feedback_channel": channel
    }


# ---------------- AGGREGATE SATISFACTION METRIC (TC087) ---------------- #

@router.get("/doctor/feedback/aggregate")
def get_aggregate_feedback():
    """
    TC087: Returns average satisfaction score and per-appointment scores
    for trend chart, filterable by service type.
    """
    scores = get_feedback_scores()
    avg = get_average_feedback_score()

    return {
        "average_score": avg,
        "total_responses": len(scores),
        "scores": scores
    }


@router.get("/doctor/feedback/aggregate/by-service")
def get_aggregate_by_service(service: str = None):
    """
    TC087: Filter aggregate by service type.
    """
    scores = get_feedback_scores()

    if service:
        scores = [s for s in scores if s.get("service", "").lower() == service.lower()]

    if not scores:
        return {"average_score": None, "total_responses": 0, "scores": []}

    avg = round(sum(s["feedback_score"] for s in scores) / len(scores), 1)

    return {
        "service": service,
        "average_score": avg,
        "total_responses": len(scores),
        "scores": scores
    }


# ---------------- NO-SHOW ---------------- #

@router.post("/doctor/noshow/mark")
async def mark_noshow(id: int, email: str, name: str):
    """
    Mark appointment as NO_SHOW (does not trigger voice — handled separately).
    TC086: NO_SHOW appointments also do not receive post-appointment follow-up.
    """
    appointment = get_last_appointment_by_email(email)

    if appointment and appointment["google_event_id"]:
        delete_event(appointment["google_event_id"])

    update_appointment_status(id, "NO_SHOW")
    return {"message": f"{name} marked as No-Show"}


@router.post("/doctor/noshow")
async def handle_noshow(id: int, email: str, name: str):
    """
    Send ALVA no-show voice follow-up from the No-Shows page.
    """
    opening_message = (
        f"Hello {name}, this is ALVA, your appointment assistant. "
        f"We noticed that you did not attend your scheduled appointment. "
        f"We hope everything is okay. "
        f"Could you please let us know the reason you missed your appointment?"
    )

    await send_voice_message(
        opening_message,
        email,
        mode="noshow",
        appointment_id=id
    )

    return {"message": f"No-show voice triggered for {email}"}


# ---------------- FOLLOW-UP STATUS (TC085) ---------------- #

@router.get("/doctor/followup/{appointment_id}")
def get_followup_status(appointment_id: int):
    """Check follow-up attempt history for a given appointment."""
    attempts = get_followup_attempts(appointment_id)
    return {
        "appointment_id": appointment_id,
        "attempts": attempts,
        "total": len(attempts)
    }


# ---------------- ESCALATION LOG & KPI (TC080, TC081) ---------------- #

@router.get("/doctor/escalations")
def get_escalation_log_route():
    """
    TC080: Return the full escalation log for the doctor dashboard.
    Each entry contains call_id, escalation_reason, timestamp,
    appointment_state_at_escalation, and conversation_transcript.
    """
    from backend.escalation import get_escalation_log
    return get_escalation_log()


@router.get("/doctor/escalation/kpi")
def get_escalation_kpi_route(override_total_calls: int = None):
    """
    TC081: Return escalation rate KPI for the doctor dashboard.
    - Reads total_calls from the live session counter in main.py automatically.
    - Pass override_total_calls as a query param to supply a manual denominator
      (useful for testing or when replaying historical data).
    - Returns: escalation_rate_pct, kpi_passed (True = rate <= 20%),
      breakdown_by_reason, total_escalations, total_calls.
    """
    from backend.escalation import get_escalation_kpi
    try:
        import backend.main as _main
        live_total = _main.total_calls
    except Exception:
        live_total = 0
    denominator = override_total_calls if override_total_calls is not None else live_total
    return get_escalation_kpi(denominator)