from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from backend.socket_manager import connections, register_email, unregister_websocket
from backend.session_store import get_session, save_session, increment_total_calls, get_total_calls
from backend.nlu import extract_nlu
from backend.dialogue_manager import generate_reply, feedback, noshow_dialogue
from backend.db import get_last_appointment_by_email

from backend.db import (
    create_appointment,
    check_doctor_time_conflict,
    update_appointment_status,
    get_all_appointments,
    update_appointment_datetime,
    update_google_event_id,
    is_doctor_on_leave,
    save_feedback,
    save_noshow_reason,
    get_noshow_appointments,
    save_feedback_score,
)

from backend.fsm import AppointmentStateMachine
from backend.google_calendar import (
    create_event,
    delete_event,
    generate_available_slots
)

from backend.doctor_routes import router as doctor_router
from backend.escalation import (
    is_explicit_human_request,
    is_out_of_scope,
    track_asr_confidence,
    handle_escalation,
    get_escalation_kpi,
    get_escalation_log,
    get_human_queue,
    set_available_agents,
    REASON_EXPLICIT,
    REASON_LOW_CONFIDENCE,
    REASON_OUT_OF_SCOPE,
)
from backend.handoff_room import create_room, get_room, get_all_rooms
import uuid
import dateparser
from datetime import datetime, timedelta

# ── Analytics module ──────────────────────────────────
from backend.analytics_routes import router as analytics_router
from backend.analytics import (
    record_call_outcome,
    record_state_transition,
    record_dropoff,
    record_latency,
    start_transcript,
    log_turn,
    end_transcript,
    log_error,
)
import time as _analytics_time
# ───────────────────────────────────────────────────────────────────


app = FastAPI() 

# ── CORS: allow doctor dashboard on :9002 to call API on :9001 ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:9001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="frontend")
app.include_router(doctor_router)
app.include_router(analytics_router)  


# ──────────────────────────────────────────────────
# ESCALATION REST ENDPOINTS 
# ──────────────────────────────────────────────────

@app.get("/escalation/log")
def escalation_log_endpoint():
    """Return full escalation log with reason, timestamp, call_id."""
    return get_escalation_log()


@app.get("/escalation/kpi")
def escalation_kpi_endpoint(override_total_calls: int = None):
    """
    Return escalation rate KPI.
    Uses the live session counter automatically.
    Pass override_total_calls to supply a manual denominator (e.g. for testing).
    """
    denominator = override_total_calls if override_total_calls is not None else get_total_calls()
    return get_escalation_kpi(denominator)


@app.get("/escalation/queue")
def escalation_queue_endpoint():
    """Return human agent queue with full context packets."""
    return get_human_queue()


@app.post("/escalation/agents")
def set_agents_endpoint(count: int):
    """Set available human agent count (0 = simulate no-agent scenario)."""
    set_available_agents(count)
    return {"available_agents": count}


@app.get("/escalation/rooms")
def escalation_rooms_endpoint():
    """Return all active handoff rooms (for agent dashboard)."""
    return get_all_rooms()


# ──────────────────────────────────────────────────
# ASR CONFIDENCE TRACKING (TC076 — dashboard metrics)
# In-memory log of every ASR score received during
# the current server session.  Each entry stores:
#   session_id, score, timestamp, escalated (bool)
# ──────────────────────────────────────────────────

from collections import deque
import time as _time

_asr_log: deque = deque(maxlen=500)   # rolling window — last 500 turns
_asr_latency_log: deque = deque(maxlen=500)


def _record_asr(session_id: str, score: float, escalated: bool):
    _asr_log.append({
        "session_id": session_id,
        "score":      round(score, 3),
        "escalated":  escalated,
        "ts":         _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    })


def _record_asr_latency(session_id: str, latency_ms: float):
    """Store ASR-to-response latency measurement."""
    _asr_latency_log.append({
        "session_id": session_id,
        "latency_ms": round(latency_ms, 1),
        "ts":         _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime()),
    })


@app.get("/metrics/asr-confidence")
def asr_confidence_metrics():
    """
    Return ASR confidence history and aggregate stats for the doctor dashboard.

    Response shape:
      {
        "average_score":        float | null,
        "low_confidence_count": int,          # turns below 0.5 threshold
        "total_turns":          int,
        "escalated_count":      int,
        "low_confidence_rate_pct": float | null,
        "threshold":            float,        # the configured threshold (0.5)
        "history":              [ {session_id, score, escalated, ts}, … ]
      }
    """
    from backend.escalation import LOW_CONFIDENCE_THRESHOLD
    entries    = list(_asr_log)
    total      = len(entries)
    low_count  = sum(1 for e in entries if e["score"] < LOW_CONFIDENCE_THRESHOLD)
    esc_count  = sum(1 for e in entries if e["escalated"])
    avg        = round(sum(e["score"] for e in entries) / total, 3) if total else None
    low_rate   = round(low_count / total * 100, 1) if total else None

    # Latency statistics
    lat_entries = list(_asr_latency_log)
    lat_total   = len(lat_entries)
    avg_latency = round(sum(e["latency_ms"] for e in lat_entries) / lat_total, 1) if lat_total else None
    max_latency = round(max((e["latency_ms"] for e in lat_entries), default=0), 1) if lat_total else None
    under_500   = sum(1 for e in lat_entries if e["latency_ms"] < 500)

    return {
        "average_score":           avg,
        "low_confidence_count":    low_count,
        "total_turns":             total,
        "escalated_count":         esc_count,
        "low_confidence_rate_pct": low_rate,
        "threshold":               LOW_CONFIDENCE_THRESHOLD,
        "history":                 entries,
        "latency": {
            "average_ms":          avg_latency,
            "max_ms":              max_latency,
            "total_measurements":  lat_total,
            "under_500ms_count":   under_500,
            "under_500ms_pct":     round(under_500 / lat_total * 100, 1) if lat_total else None,
            "recent":              lat_entries[-20:],
        },
    }


# ──────────────────────────────────────────────────
# DASHBOARD WEBSOCKET  /ws/dashboard
# The doctor_dashboard connects here to receive
# real-time escalation pop-up alerts.
# ──────────────────────────────────────────────────

dashboard_connections: list = []

@app.websocket("/ws/dashboard")
async def dashboard_ws(websocket: WebSocket):
    await websocket.accept()
    dashboard_connections.append(websocket)
    try:
        while True:
            await websocket.receive_text()   # keep-alive; dashboard sends nothing
    except Exception:
        pass
    finally:
        if websocket in dashboard_connections:
            dashboard_connections.remove(websocket)


async def notify_dashboard(payload: dict):
    """Broadcast a JSON payload to all connected doctor dashboards."""
    dead = []
    for ws in dashboard_connections:
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        dashboard_connections.remove(ws)


# ──────────────────────────────────────────────────
# HUMAN AGENT WEBSOCKET  /ws/agent/<room_id>
# Agent connects here; messages relay to/from patient
# ──────────────────────────────────────────────────

@app.websocket("/ws/agent/{room_id}")
async def agent_websocket(websocket: WebSocket, room_id: str):
    """
    Human agent connects to a handoff room.
    - On connect: room is found, agent_ws is registered, context packet sent.
    - Messages from agent → forwarded to patient.
    - Messages from patient (received in patient ws) → forwarded here.
    - Either side sends __end_handoff__ to close the room.
    """
    await websocket.accept()

    room = get_room(room_id)

    if not room:
        await websocket.send_json({
            "type":  "error",
            "text":  f"Room {room_id} not found or already closed.",
        })
        await websocket.close()
        return

    # Register agent in the room
    room.agent_ws = websocket

    # Send context packet immediately so agent has full background (TC078)
    await websocket.send_json({
        "type":           "handoff_context",
        "room_id":        room_id,
        "context_packet": room.context_packet,
        "text":           "You are now connected to the patient. Full context above.",
    })

    # Notify patient that agent has joined
    await room.send_to_patient(
        "A human agent has joined. Go ahead and speak.",
        sender="system"
    )

    try:
        while True:
            msg = await websocket.receive_text()

            if msg.strip() == "__end_handoff__":
                await room.send_to_patient(
                    "The agent has ended this session. Thank you for calling.",
                    sender="system"
                )
                room.end()
                break

            # Relay agent message to patient
            await room.send_to_patient(msg, sender="agent")

    except Exception as e:
        print(f"[agent_ws] Room {room_id} agent disconnected: {e}")
        if get_room(room_id):
            await room.send_to_patient(
                "The agent has disconnected. Please call back if you need further help.",
                sender="system"
            )
            room.end()


@app.get("/")
def home():
    return FileResponse("frontend/index.html")

# @app.get("/")
# def home():
#     return FileResponse("frontend/doctor_dashboard.html")    


# -----------------------------
# Normalize datetime
# -----------------------------
def normalize_datetime(date_str, time_str):

    if not date_str or not time_str:
        return None

    combined = f"{date_str} {time_str}"
    parsed = dateparser.parse(combined)

    if not parsed:
        return None

    # 🚫 block past time
    if parsed < datetime.now():
        return "PAST_TIME"

    return parsed.strftime("%Y-%m-%d %H:%M:%S")


# ──────────────────────────────────────────────────
# total_calls lives in session_store.py so
# both main.py and doctor_routes.py share the same
# counter without a circular import.
# ──────────────────────────────────────────────────

@app.get("/escalation/total-calls")
def get_total_calls_endpoint():
    """TC081: Return the total number of WebSocket sessions started."""
    return {"total_calls": get_total_calls()}


# -----------------------------
# WebSocket Assistant
# -----------------------------
@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):

    increment_total_calls()    #  shared counter in session_store.py

    await websocket.accept()
    connections.append(websocket)

    appointment_id = str(uuid.uuid4())

    state_machine = AppointmentStateMachine(
        appointment_id=appointment_id,
        current_state="INQUIRY"
    )

    # initialise per-call analytics ──────────────────
    _call_id = session_id
    start_transcript(_call_id, appointment_id=appointment_id)
    _call_outcome = "success"      # updated to "failed"/"escalated" on error/escalation
    # ────────────────────────────────────────────────────────────────

    try:

        while True:

            text = await websocket.receive_text()

            # start per-turn latency timer ────────────────
            _turn_start_ms = _analytics_time.monotonic() * 1000
            # ───────────────────────────────────────────────────────

            session = get_session(session_id)

            # -------------------------------------------------
            # RECEIVE EMAIL FROM FRONTEND (FEEDBACK FIX)
            # Also registers email → websocket for targeted delivery
            # -------------------------------------------------
            if text.startswith("__feedback_email__:"):
                email = text.replace("__feedback_email__:", "").strip()
                session["feedback_email"] = email
                save_session(session_id, session)
                register_email(email, websocket)
                continue

            #  log each real customer turn ─────────────────
            if not text.startswith("__"):
                log_turn(_call_id, "customer", text)
            # ───────────────────────────────────────────────────────

            # ── Early exit for control-only messages (no NLU needed) ──

            # ASR latency metric — record and skip
            if text.startswith("__asr_latency__:"):
                lat_parts = text.split(":", 2)
                try:
                    _record_asr_latency(session_id, float(lat_parts[1]))
                except (ValueError, IndexError):
                    pass
                continue

            # Silence timeout — re-prompt via AI; escalate after repeated failures
            if text.strip() == "__silence_timeout__":
                silence_count = session.get("silence_timeout_count", 0) + 1
                session["silence_timeout_count"] = silence_count
                save_session(session_id, session)

                if silence_count >= 3:
                    session["silence_timeout_count"] = 0
                    result = handle_escalation(
                        session=session,
                        call_id=session_id,
                        reason=REASON_EXPLICIT,
                        appointment_state=state_machine.get_state(),
                        extra_log_data={"trigger": "silence_timeout"},
                    )
                    if result["escalated"]:
                        room = create_room(session_id, websocket, result["context_packet"])
                        session["handoff_room_id"] = room.room_id
                    save_session(session_id, session)
                    await websocket.send_json({
                        "type":      "assistant_reply",
                        "text":      result["reply"],
                        "state":     state_machine.get_state(),
                        "mode":      "human_handoff",
                        "escalated": result["escalated"],
                        "no_agent":  result["no_agent"],
                        "room_id":   session.get("handoff_room_id"),
                    })
                else:
                    reply = generate_reply(session, "[silence — no response from user]")
                    await websocket.send_json({
                        "type":  "assistant_reply",
                        "text":  reply,
                        "state": state_machine.get_state(),
                    })
                continue

            # Partial utterance — feed the incomplete text through the normal AI flow
            if text.startswith("__partial_utterance__:"):
                partial_text = text.replace("__partial_utterance__:", "").strip()
                reply = generate_reply(
                    session,
                    partial_text if partial_text else "[user started speaking but stopped]"
                )
                await websocket.send_json({
                    "type":  "assistant_reply",
                    "text":  reply,
                    "state": state_machine.get_state(),
                })
                continue

            # Strip ASR confidence prefix early so all downstream
            # code (handoff relay, NLU, dialogue) gets clean text
            asr_confidence = None
            if text.startswith("__asr_confidence__:"):
                parts = text.split(":", 2)
                try:
                    asr_confidence = float(parts[1])
                    text = parts[2] if len(parts) > 2 else ""
                except (ValueError, IndexError):
                    pass

            # Quick NLU pre-check for human_help intent so the escalation
            # block can use it (full NLU slot extraction happens later below)
            _pre_nlu = extract_nlu(text)
            if _pre_nlu.get("intent") == "human_help":
                session["_nlu_human_help"] = True

            # ── REPEAT REQUEST: replay last ALVA message ───────────────
            _REPEAT_PHRASES = [
                "repeat", "say that again", "say it again", "repeat that",
                "repeat again", "again please", "come again", "pardon",
                "what did you say", "could you repeat", "can you repeat",
                "i didn't hear", "didn't catch", "once more", "one more time",
            ]
            _text_lower = text.lower().strip()
            if any(p in _text_lower for p in _REPEAT_PHRASES):
                _history = session.get("history", [])
                _last_alva = next(
                    (m["content"] for m in reversed(_history) if m.get("role") == "assistant"),
                    None
                )
                if _last_alva:
                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": _last_alva,
                        "state": state_machine.get_state(),
                    })
                    continue
            # ───────────────────────────────────────────────────────────

            # ══════════════════════════════════════════════════
            # HANDOFF ROOM RELAY — patient is live with agent
            # If this session has an active room, relay patient
            # messages directly to the agent and skip ALVA entirely.
            # ══════════════════════════════════════════════════
            handoff_room_id = session.get("handoff_room_id")
            if handoff_room_id:
                room = get_room(handoff_room_id)
                if room:
                    if text.strip() == "__end_handoff__":
                        await room.send_to_agent(
                            "Patient has ended the session.", sender="system"
                        )
                        room.end()
                        session.pop("handoff_room_id", None)
                        save_session(session_id, session)
                        await websocket.send_json({
                            "type": "assistant_reply",
                            "text": "You have been disconnected from the agent. How else can I help you?",
                            "state": state_machine.get_state(),
                            "mode": "booking",
                        })
                    else:
                        # Relay patient text to agent
                        await room.send_to_agent(text, sender="patient")
                    continue
                else:
                    # Room ended (agent closed it) — clear and resume ALVA
                    session.pop("handoff_room_id", None)
                    save_session(session_id, session)

            # ══════════════════════════════════════════════════
            # HUMAN HANDOFF & ESCALATION CHECKS 
            # Run before any other NLU processing so that
            # escalation always takes priority.
            # ══════════════════════════════════════════════════

            # Explicit customer request for human agent
            # Also catches NLU human_help intent detected earlier in the turn
            _wants_human = (
                is_explicit_human_request(text)
                or session.get("_nlu_human_help")
            )
            session.pop("_nlu_human_help", None)
            if _wants_human:
                result = handle_escalation(
                    session=session,
                    call_id=session_id,
                    reason=REASON_EXPLICIT,
                    appointment_state=state_machine.get_state(),
                )
                if result["escalated"]:
                    room = create_room(session_id, websocket, result["context_packet"])
                    session["handoff_room_id"] = room.room_id
                    _call_outcome = "escalated"   
                    # ── Push real-time alert to doctor dashboard ──
                    await notify_dashboard({
                        "type":           "escalation_alert",
                        "room_id":        room.room_id,
                        "context_packet": result["context_packet"],
                    })
                await websocket.send_json({
                    "type":           "assistant_reply",
                    "text":           result["reply"],
                    "state":          state_machine.get_state(),
                    "mode":           "human_handoff",
                    "escalated":      result["escalated"],
                    "no_agent":       result["no_agent"],
                    "room_id":        session.get("handoff_room_id"),
                    "context_packet": result["context_packet"],  # TC078
                })
                save_session(session_id, session)
                continue

            if asr_confidence is not None:
                should_escalate = track_asr_confidence(session, asr_confidence)
                save_session(session_id, session)
                if should_escalate:
                    result = handle_escalation(
                        session=session,
                        call_id=session_id,
                        reason=REASON_LOW_CONFIDENCE,
                        appointment_state=state_machine.get_state(),
                        extra_log_data={"asr_confidence": asr_confidence},
                    )
                    if result["escalated"]:
                        room = create_room(session_id, websocket, result["context_packet"])
                        session["handoff_room_id"] = room.room_id
                        _call_outcome = "escalated"   # TC088/TC095
                    _record_asr(session_id, asr_confidence, escalated=True)
                    await websocket.send_json({
                        "type":           "assistant_reply",
                        "text":           result["reply"],
                        "state":          state_machine.get_state(),
                        "mode":           "human_handoff",
                        "escalated":      result["escalated"],
                        "no_agent":       result["no_agent"],
                        "room_id":        session.get("handoff_room_id"),
                        "context_packet": result["context_packet"],  # TC078
                    })
                    session["low_confidence_count"] = 0  # reset after escalation
                    save_session(session_id, session)
                    continue
                else:
                    _record_asr(session_id, asr_confidence, escalated=False)

            # Reset silence counter on any real user input
            if session.get("silence_timeout_count", 0) > 0:
                session["silence_timeout_count"] = 0
                save_session(session_id, session)

            # Out-of-scope request (insurance, billing, etc.)
            if is_out_of_scope(text):
                result = handle_escalation(
                    session=session,
                    call_id=session_id,
                    reason=REASON_OUT_OF_SCOPE,
                    appointment_state=state_machine.get_state(),
                    extra_log_data={"user_text": text},
                )
                if result["escalated"]:
                    room = create_room(session_id, websocket, result["context_packet"])
                    session["handoff_room_id"] = room.room_id
                    _call_outcome = "escalated"   # TC088/TC095
                await websocket.send_json({
                    "type":           "assistant_reply",
                    "text":           result["reply"],
                    "state":          state_machine.get_state(),
                    "mode":           "human_handoff",
                    "escalated":      result["escalated"],
                    "no_agent":       result["no_agent"],
                    "room_id":        session.get("handoff_room_id"),
                    "context_packet": result["context_packet"],  # TC078
                })
                save_session(session_id, session)
                continue

            # ---------------------------
            # ACTIVATE FEEDBACK MODE
            # ---------------------------
            if text == "__feedback_mode__":

                session["feedback_mode"] = True

                # email already sent from frontend
                save_session(session_id, session)

                # DO NOT call AI here
                continue


            # ---------------------------
            # FEEDBACK MODE HANDLING
            # ---------------------------

            if session.get("feedback_mode"):

                # check if user is trying to reschedule/cancel instead of feedback
                nlu = extract_nlu(text)

                if nlu.get("intent") in ["reschedule", "cancel", "confirm", "schedule"]:

                    # exit feedback mode and re-process through normal flow below
                    session["feedback_mode"] = False
                    save_session(session_id, session)
                    # fall through — do NOT continue, let normal intent handling run

                else:

                    user_feedback = text.strip()

                    email = session.get("feedback_email")

                    name = None

                    if email:
                        appointment = get_last_appointment_by_email(email)
                        if appointment:
                            name = appointment["name"]

                    save_feedback(name,email,user_feedback)

                    session["feedback_mode"] = False
                    save_session(session_id, session)

                    reply = "Thank you for sharing your experience. Your feedback helps us improve."

                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": reply,
                        "state": state_machine.get_state()
                    })

                    continue
            # ══════════════════════════════════════════════════
            # NO-SHOW MODE ACTIVATION
            # Triggered when the doctor clicks "No-Show" button.
            # The frontend sends  __noshow_mode__:<email>
            # ══════════════════════════════════════════════════

            if text.startswith("__noshow_mode__:"):
                email = text.replace("__noshow_mode__:", "").strip()
                session["noshow_mode"] = True
                session["noshow_email"] = email
                session["noshow_step"] = "reason"
                save_session(session_id, session)
                # Opening voice message already sent by /doctor/noshow route
                continue


            # ══════════════════════════════════════════════════
            # NO-SHOW CONVERSATION FLOW
            # ══════════════════════════════════════════════════

            if session.get("noshow_mode"):

                nlu = extract_nlu(text)
                step = session.get("noshow_step", "reason")

                # ── STEP 1: patient gives a reason ──────────────
                if step == "reason":

                    # Store the reason in session
                    session["noshow_reason"] = text
                    session["noshow_step"]   = "rebook"
                    save_session(session_id, session)

                    # Persist reason to DB
                    noshow_email = session.get("noshow_email")
                    if noshow_email:
                        appointment = get_last_appointment_by_email(noshow_email)
                        patient_name = appointment["name"] if appointment else None
                        try:
                            save_noshow_reason(patient_name, noshow_email, text)
                        except Exception as e:
                            print("Save noshow reason error:", e)

                    # Use AI to respond empathetically then ask to rebook
                    reply = noshow_dialogue(session, text)

                    # If AI reply doesn't naturally ask about rebooking, append it
                    if "appointment" not in reply.lower() and "book" not in reply.lower():
                        reply += " Would you like me to book a new appointment for you?"

                    await websocket.send_json({
                        "type":  "assistant_reply",
                        "text":  reply,
                        "state": state_machine.get_state(),
                        "mode":  "noshow"
                    })
                    continue

                # ── STEP 2: does the patient want to rebook? ─────
                if step == "rebook":

                    user_lower  = text.lower().strip()
                    positive_kw = {"yes", "yeah", "sure", "okay", "ok", "please",
                                   "yep", "yup", "go ahead", "book", "rebook",
                                   "new appointment", "of course", "alright"}
                    negative_kw = {"no", "nope", "not now", "later", "don't",
                                   "wont", "won't", "nah", "not really", "skip"}

                    wants_rebook = (
                        nlu.get("intent") in ["confirm", "schedule"]
                        or any(kw in user_lower for kw in positive_kw)
                    )
                    wants_exit = (
                        nlu.get("intent") == "cancel"
                        or any(kw in user_lower for kw in negative_kw)
                    )

                    # ── YES – start booking flow ──────────────────
                    if wants_rebook:

                        session["noshow_mode"] = False
                        session["noshow_step"] = None
                        
                        # FIX: stop post-appointment rating loop
                        session["post_appointment_mode"] = False
                        session["post_appointment_step"] = None

                        # Pre-fill ALL known slots from the original appointment
                        # so ALVA only asks for service, date, and time.
                        noshow_email = session.get("noshow_email")
                        if noshow_email:
                            session["slots"]["email"] = noshow_email
                            session["feedback_email"] = noshow_email
                            register_email(noshow_email, websocket)
                            try:
                                existing = get_last_appointment_by_email(noshow_email)
                                if existing:
                                    if existing.get("name"):
                                        session["slots"]["name"] = existing["name"]
                                    # Pre-fill service from previous appointment
                                    if existing.get("service"):
                                        session["slots"]["service"] = existing["service"]
                            except Exception as _e:
                                print("No-show rebook: could not pre-fill slots:", _e)

                        # Inject context into history so AI knows name/email are already known
                        _known_name  = session["slots"].get("name", "")
                        _known_email = session["slots"].get("email", "")
                        _known_svc   = session["slots"].get("service", "")
                        if _known_name or _known_email:
                            if "history" not in session:
                                session["history"] = []
                            session["history"].append({
                                "role": "assistant",
                                "content": (
                                    f"I have your details on file: "
                                    f"Name: {_known_name}, "
                                    f"Email: {_known_email}, "
                                    f"Previous service: {_known_svc}. "
                                    f"I will not ask for these again."
                                )
                            })

                        # Clear only scheduling slots — keep name, email, service
                        for k in ["date", "time"]:
                            session["slots"].pop(k, None)
                        session["appointment_saved"] = False

                        save_session(session_id, session)

                        _svc_hint = f" for {session['slots'].get('service', 'the same service')}" if session['slots'].get('service') else ""
                        reply = (
                            f"Great! Let's get you rebooked{_svc_hint}. "
                            "What date and time works best for you?"
                        )

                        await websocket.send_json({
                            "type":  "assistant_reply",
                            "text":  reply,
                            "state": state_machine.get_state(),
                            "mode":  "booking"
                        })
                        continue

                    # ── NO – close politely ───────────────────────
                    if wants_exit:

                        session["noshow_mode"] = False
                        session["noshow_step"] = None
                        save_session(session_id, session)

                        reply = (
                            "No problem at all. We hope to see you soon. "
                            "Take care and have a wonderful day. Goodbye!"
                        )

                        await websocket.send_json({
                            "type":  "assistant_reply",
                            "text":  reply,
                            "state": state_machine.get_state(),
                            "mode":  "noshow_end"
                        })
                        continue

                    # ── UNCLEAR – ask again ───────────────────────
                    reply = (
                        "I'm sorry, I didn't quite catch that. "
                        "Would you like to book a new appointment? "
                        "Please say yes or no."
                    )

                    await websocket.send_json({
                        "type":  "assistant_reply",
                        "text":  reply,
                        "state": state_machine.get_state(),
                        "mode":  "noshow"
                    })
                    continue


            # ══════════════════════════════════════════════════
            # POST-APPOINTMENT MODE ACTIVATION 
            # Triggered when ALVA sends post-appointment follow-up voice.
            # Frontend sends: __post_appointment_mode__:<email>:<appointment_id>
            # ══════════════════════════════════════════════════

            if text.startswith("__post_appointment_mode__:"):
                parts = text.replace("__post_appointment_mode__:", "").split(":")
                pa_email = parts[0].strip() if len(parts) > 0 else ""
                pa_id = parts[1].strip() if len(parts) > 1 else None
                session["post_appointment_mode"] = True
                session["post_appointment_email"] = pa_email
                session["post_appointment_id"] = int(pa_id) if pa_id else None
                session["post_appointment_step"] = "rating"   # rating → rebook
                save_session(session_id, session)
                continue


            # ══════════════════════════════════════════════════
            # POST-APPOINTMENT CONVERSATION FLOW 
            # Steps: rating → rebook
            # ══════════════════════════════════════════════════

            if session.get("post_appointment_mode"):

                nlu = extract_nlu(text)
                step = session.get("post_appointment_step", "rating")
                pa_id = session.get("post_appointment_id")
                pa_email = session.get("post_appointment_email")

                # ── STEP 1: Capture 1–5 rating ──────────
                if step == "rating":

                    import re as _re

                    def _extract_rating_1_to_5(raw: str):
                        """Extract a 1–5 rating from digits or spoken words."""
                        if not raw:
                            return None
                        s = raw.strip().lower()

                        # 1) Digits anywhere in the utterance
                        m = _re.search(r"\b([1-5])\b", s)
                        if m:
                            return int(m.group(1))

                        # 2) Common spoken forms (ASR often returns "for" for "four")
                        word_map = {
                            "one": 1,
                            "won": 1,
                            "two": 2,
                            "to": 2,
                            "too": 2,
                            "three": 3,
                            "four": 4,
                            "for": 4,
                            "five": 5,
                        }

                        tokens = _re.findall(r"[a-z']+", s)
                        for t in reversed(tokens):
                            if t in word_map:
                                return word_map[t]

                        return None

                    score = _extract_rating_1_to_5(text)

                    if score and pa_id:
                        # persist score with timestamp + channel
                        save_feedback_score(pa_id, score, channel="voice")
                        session["post_appointment_rating"] = score
                        session["post_appointment_step"] = "rebook"
                        save_session(session_id, session)

                        reply = (
                            f"Thank you for rating us {score} out of 5! "
                            f"Would you like to book a follow-up appointment?"
                        )
                    else:
                        reply = (
                            "I didn't catch your rating. "
                            "Could you please give a score from 1 to 5?"
                        )

                    await websocket.send_json({
                        "type":  "assistant_reply",
                        "text":  reply,
                        "state": state_machine.get_state(),
                        "mode":  "post_appointment"
                    })
                    continue

                # ── STEP 2: Rebooking offer  ─────────────────────
                if step == "rebook":

                    user_lower = text.lower().strip()
                    positive_kw = {"yes", "yeah", "sure", "okay", "ok", "please",
                                   "yep", "yup", "go ahead", "book", "rebook",
                                   "new appointment", "of course", "alright"}
                    negative_kw = {"no", "nope", "not now", "later", "don't",
                                   "won't", "nah", "not really", "skip"}

                    wants_rebook = (
                        nlu.get("intent") in ["confirm", "schedule"]
                        or any(kw in user_lower for kw in positive_kw)
                    )
                    wants_exit = (
                        nlu.get("intent") == "cancel"
                        or any(kw in user_lower for kw in negative_kw)
                    )

                    if wants_rebook:
                        # transition into booking flow from same call
                        session["post_appointment_mode"] = False
                        session["post_appointment_step"] = None
                        # Store previous appointment id for linking
                        if pa_id:
                            session["previous_appointment_id"] = pa_id
                        if pa_email:
                            session["slots"]["email"] = pa_email
                            session["feedback_email"] = pa_email
                            register_email(pa_email, websocket)
                            # Look up name and service from original appointment
                            try:
                                _orig = get_last_appointment_by_email(pa_email)
                                if _orig:
                                    if _orig.get("name"):
                                        session["slots"]["name"] = _orig["name"]
                                    if _orig.get("service"):
                                        session["slots"]["service"] = _orig["service"]
                            except Exception as _pe:
                                print("Post-appt rebook: could not pre-fill slots:", _pe)

                        # Inject history context so AI knows what's already known
                        _k_name  = session["slots"].get("name", "")
                        _k_email = session["slots"].get("email", "")
                        _k_svc   = session["slots"].get("service", "")
                        if _k_name or _k_email:
                            if "history" not in session:
                                session["history"] = []
                            session["history"].append({
                                "role": "assistant",
                                "content": (
                                    f"I have your details on file: "
                                    f"Name: {_k_name}, "
                                    f"Email: {_k_email}, "
                                    f"Previous service: {_k_svc}. "
                                    f"I will not ask for these again."
                                )
                            })

                        # Clear only scheduling slots — keep name, email, service
                        for k in ["date", "time"]:
                            session["slots"].pop(k, None)
                        session["appointment_saved"] = False
                        save_session(session_id, session)

                        # FSM COMPLETED → TENTATIVE for new booking
                        state_machine.transition(
                            "TENTATIVE",
                            metadata={"reason": "post_appointment_rebook"}
                        )
                        
                        record_state_transition(appointment_id, "COMPLETED", "TENTATIVE", _call_id)

                        _svc_h = f" for {session['slots'].get('service', 'your service')}" if session['slots'].get('service') else ""
                        reply = (
                            f"Great! Let's book your follow-up appointment{_svc_h}. "
                            "What date and time works best for you?"
                        )
                        await websocket.send_json({
                            "type":  "assistant_reply",
                            "text":  reply,
                            "state": state_machine.get_state(),
                            "mode":  "booking"
                        })
                        continue

                    if wants_exit:
                        session["post_appointment_mode"] = False
                        session["post_appointment_step"] = None
                        save_session(session_id, session)

                        reply = (
                            "No problem! Thank you for your time today. "
                            "We look forward to seeing you again. Goodbye!"
                        )
                        await websocket.send_json({
                            "type":  "assistant_reply",
                            "text":  reply,
                            "state": state_machine.get_state(),
                            "mode":  "post_appointment_end"
                        })
                        continue

                    # Unclear — ask again
                    reply = (
                        "I'm sorry, I didn't catch that. "
                        "Would you like to book a follow-up appointment? Please say yes or no."
                    )
                    await websocket.send_json({
                        "type":  "assistant_reply",
                        "text":  reply,
                        "state": state_machine.get_state(),
                        "mode":  "post_appointment"
                    })
                    continue



            # ---------------------------
            # NORMAL NLU PROCESSING
            # ---------------------------

            # wrap NLU with error logging
            try:
                nlu = extract_nlu(text)
            except Exception as _nlu_err:
                log_error(_call_id, "nlu", "failure",
                          detail=str(_nlu_err), recovery_action="fallback_unknown_intent")
                nlu = {"intent": "unknown", "date": None, "time": None,
                       "time_period": None, "service": None, "name": None, "email": None}

            # update slots
            for key, value in nlu.items():

                if key == "intent":
                    continue

                if not value:
                    continue

                if key == "time":
                    # In reschedule_mode, skip time if the NLU also returned a date
                    # in the same utterance. This prevents "22nd" → 22:00, "April 1st" → 01:00
                    # etc. — dateparser extracts the ordinal number as an hour.
                    # Only accept time when no date was in this same NLU response
                    # (i.e. the user is specifically answering the "what time?" question).
                    if session.get("reschedule_mode") and nlu.get("date"):
                        continue

                    parsed_time = dateparser.parse(str(value))

                    if parsed_time:
                        session["slots"]["time"] = parsed_time.strftime("%H:%M")

                else:
                    session["slots"][key] = value

                # Register email -> websocket mapping as soon as email is known
                if key == "email" and value:
                    register_email(value, websocket)

            # keep last detected intent in session for context packet
            if nlu.get("intent"):
                session["last_intent"] = nlu["intent"]

            print("CURRENT SESSION SLOTS:", session["slots"])
            save_session(session_id, session)

            required = ["service", "date", "time", "name", "email"]


            # ---------------------------
            # CHECK AVAILABILITY
            # ---------------------------

            if nlu.get("intent") == "check_availability" and not nlu.get("time"):


                date = session["slots"].get("date")

                parsed_date = dateparser.parse(date)
                formatted_date = parsed_date.strftime("%Y-%m-%d")

                if is_doctor_on_leave(formatted_date):

                    reply = "Doctor is on leave that day. Please choose another date."

                else:

                    slots = generate_available_slots(formatted_date)

                    if isinstance(slots, dict) and "error" in slots:
                        # TC064: closed day (e.g. Sunday) / TC066: holiday
                        reply = slots["error"]
                        next_open = slots.get("next_open_date")
                        if next_open:
                            reply += f" Would you like to book on {next_open} instead?"
                            # Remember the suggestion so a "yes" can adopt it
                            session["suggested_next_date"] = next_open
                            save_session(session_id, session)

                    elif not slots:

                        reply = "No slots available that day. Please choose another date."
                    else:
                        reply = f"Available slots are {', '.join(slots)} which time works for you?"

                await websocket.send_json({
                    "type": "assistant_reply",
                    "text": reply,
                    "state": state_machine.get_state()
                })

                continue


            # ---------------------------
            # MOVE TO TENTATIVE
            # ---------------------------

            if state_machine.get_state() == "INQUIRY" and not session.get("reschedule_mode"):

                if any(session["slots"].get(k) for k in required):

                    state_machine.transition(
                        "TENTATIVE",
                        metadata={"reason": "user_provided_details"}
                    )
                    record_state_transition(appointment_id, "INQUIRY", "TENTATIVE", _call_id)
            # ---------------------------

            # ---------------------------
            # CONFIRM ACTION
            # ---------------------------
            if nlu.get("intent") == "confirm":

                #  user said yes to the suggested next-open date ──
                if session.get("suggested_next_date"):
                    session["slots"]["date"] = session.pop("suggested_next_date")
                    save_session(session_id, session)
                    # Fall through so normal booking flow continues with new date

                # cancel confirmation
                if session.get("cancel_confirm"):

                    email = session["slots"].get("email")
                    appointments = get_all_appointments()

                    for a in appointments:

                        if a["email"] == email and a["state"] in ["CONFIRMED", "RESCHEDULED"]:

                            if a.get("google_event_id"):
                                try:
                                    delete_event(a["google_event_id"])
                                except Exception as e:
                                    print("Calendar delete skipped:", e)

                            update_appointment_status(a["id"], "CANCELLED")

                            session["cancel_confirm"] = False
                            save_session(session_id, session)

                            await websocket.send_json({
                                "type": "assistant_reply",
                                "text": "Your appointment has been cancelled.",
                                "state": state_machine.get_state()
                            })

                            continue

            # booking confirmation (only if not rescheduling)
            if nlu.get("intent") == "confirm" and not session.get("reschedule_mode"):

                if all(session["slots"].get(k) for k in required):

                    state_machine.transition(
                        "CONFIRMED",
                        metadata={"reason": "user_confirmed"}
                    )
                    
                    record_state_transition(appointment_id, "TENTATIVE", "CONFIRMED", _call_id)
            # ---------------------------

            if state_machine.get_state() == "CONFIRMED" and not session.get("appointment_saved"):

                name = session["slots"].get("name")
                email = session["slots"].get("email")
                service = session["slots"].get("service")
                date = session["slots"].get("date")
                time = session["slots"].get("time")

                parsed_date = dateparser.parse(date)
                formatted_date = parsed_date.strftime("%Y-%m-%d")

                parsed_time = dateparser.parse(time).strftime("%H:%M:%S")

                if is_doctor_on_leave(formatted_date):

                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": "Doctor is on leave that day. Please choose another date.",
                        "state": state_machine.get_state()
                    })

                    continue

                # TC064 / TC066: block Sundays and holidays before conflict check
                slots_check = generate_available_slots(formatted_date)
                if isinstance(slots_check, dict) and "error" in slots_check:
                    reply = slots_check["error"]
                    next_open = slots_check.get("next_open_date")
                    if next_open:
                        reply += f" Would you like to book on {next_open} instead?"
                        session["suggested_next_date"] = next_open
                        save_session(session_id, session)
                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": reply,
                        "state": state_machine.get_state()
                    })
                    continue

                conflict = check_doctor_time_conflict(
                    formatted_date,
                    parsed_time
                )

                if conflict == "BUSY":

                    slots = generate_available_slots(formatted_date)

                    reply = f"Doctor is busy at that time. Available slots are {', '.join(slots)}"

                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": reply,
                        "state": state_machine.get_state()
                    })

                    continue


                normalized_datetime = normalize_datetime(date, time)
               
                # 🚫 Prevent past booking
                if normalized_datetime == "PAST_TIME":

                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": "You cannot book past date or time. Please choose a future slot.",
                        "state": state_machine.get_state()
                    })

                    continue

                start_dt = datetime.strptime(
                    normalized_datetime,
                    "%Y-%m-%d %H:%M:%S"
                )

                # 🚫 Block bookings outside business hours (9am–6pm)
                from backend.google_calendar import BUSINESS_START, BUSINESS_END
                if start_dt.hour < BUSINESS_START or start_dt.hour >= BUSINESS_END:
                    slots = generate_available_slots(parsed_date.strftime("%Y-%m-%d"))
                    slots_hint = f" Available slots are: {', '.join(slots)}." if isinstance(slots, list) and slots else ""
                    session["slots"].pop("time", None)
                    save_session(session_id, session)
                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": f"Appointments are only available between {BUSINESS_START}am and {BUSINESS_END - 12}pm.{slots_hint} What time works for you?",
                        "state": state_machine.get_state()
                    })
                    continue

                end_dt = start_dt + timedelta(hours=1)

                # log calendar errors
                try:
                    event_id = create_event(
                        start_datetime=start_dt.isoformat(),
                        # end_datetime=end_dt.isoformat(),
                        service=service,
                        summary=f"{service}-{name}",
                        description="Booked via ALVA",
                        attendee_email=email
                    )
                except Exception as _cal_err:
                    log_error(_call_id, "calendar", "failure",
                              detail=str(_cal_err), recovery_action="appointment_saved_without_event")
                    event_id = None

                create_appointment(
                    name=name,
                    email=email,
                    service=service,
                    date_time=normalized_datetime,
                    state="CONFIRMED",
                    google_event_id=event_id,
                    previous_appointment_id=session.get("previous_appointment_id")
                )

                session["appointment_saved"] = True
                session.pop("previous_appointment_id", None)   # TC083: clear after use
                # session["slots"] = {}  # clear slots after booking
                save_session(session_id, session)

                reply = "Your appointment has been booked successfully."

                await websocket.send_json({
                    "type": "assistant_reply",
                    "text": reply,
                    "state": state_machine.get_state()
                })

                continue


            # ---------------------------
            # CANCEL APPOINTMENT
            # ---------------------------

            if nlu.get("intent") == "cancel":

                if not session.get("cancel_confirm"):

                    session["cancel_confirm"] = True
                    save_session(session_id, session)

                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": "Are you sure you want to cancel your appointment?",
                        "state": state_machine.get_state()
                    })

                    continue

                email = session.get("feedback_email") or session["slots"].get("email")

                appointment = get_last_appointment_by_email(email)

                if not appointment:

                    reply = "No active appointment found to cancel."

                else:

                    if appointment.get("google_event_id"):
                        try:
                            delete_event(appointment["google_event_id"])
                        except Exception as e:
                            print("Calendar delete skipped:", e)

                    update_appointment_status(appointment["id"], "CANCELLED")

                    session["cancel_confirm"] = False
                    save_session(session_id, session)

                    reply = "Your appointment has been cancelled."

                await websocket.send_json({
                    "type": "assistant_reply",
                    "text": reply,
                    "state": state_machine.get_state()
                })

                continue


            # ---------------------------
            # RESCHEDULE REQUEST
            # ---------------------------

            if nlu.get("intent") == "reschedule":

                email = session.get("feedback_email") or session["slots"].get("email")
                appointment = None

                if email:
                    appointment = get_last_appointment_by_email(email)

                # No email yet — ask for it
                if not email:
                    session["reschedule_mode"] = True
                    session["slots"].pop("date", None)
                    session["slots"].pop("time", None)
                    save_session(session_id, session)
                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": "Sure, I can help with that. Could you please provide your email address so I can locate your appointment?",
                        "state": state_machine.get_state()
                    })
                    continue

                # Email known but no appointment found
                if not appointment:
                    session["slots"].update({
                        "date": session["slots"].get("date"),
                        "time": session["slots"].get("time")
                    })
                    save_session(session_id, session)
                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": "Got it. Let me confirm your appointment details.",
                        "state": state_machine.get_state()
                    })
                    continue

                # ✅ Appointment found — enter reschedule flow
                session["reschedule_mode"] = True
                session["slots"].pop("date", None)
                session["slots"].pop("time", None)
                save_session(session_id, session)

                await websocket.send_json({
                    "type": "assistant_reply",
                    "text": "Sure. What new date would you like?",
                    "state": state_machine.get_state()
                })
                continue

            # ---------------------------
            # RESCHEDULE FLOW
            # ---------------------------

            if session.get("reschedule_mode"):

                # ── Step 0: Need email to look up appointment ─────────────────
                _rs_email = session.get("feedback_email") or session["slots"].get("email")
                if not _rs_email:
                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": "Could you please provide your email address so I can locate your appointment?",
                        "state": state_machine.get_state()
                    })
                    continue

                date = session["slots"].get("date")
                time = session["slots"].get("time")

                # ── Step 1: Need date ────────────────────────────────────────
                if not date:
                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": "What new date would you like?",
                        "state": state_machine.get_state()
                    })
                    continue

                # ── Step 2: Have date, need time ─────────────────────────────
                elif not time:
                    formatted_date = dateparser.parse(date).strftime("%Y-%m-%d")
                    slots = generate_available_slots(formatted_date)

                    if isinstance(slots, dict) and "error" in slots:
                        reply = slots["error"]
                        next_open = slots.get("next_open_date")
                        if next_open:
                            reply += f" Would you like to reschedule to {next_open} instead?"
                            session["suggested_next_date"] = next_open
                            save_session(session_id, session)
                    elif not slots:
                        reply = "No slots available that day. Please choose another date."
                    else:
                        reply = f"Available slots are {', '.join(slots)}. Which time works for you?"

                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": reply,
                        "state": state_machine.get_state()
                    })
                    continue

                # ── Step 3: Have date + time → save reschedule ───────────────
                else:
                    email = session.get("feedback_email") or session["slots"].get("email")
                    appointment = get_last_appointment_by_email(email) if email else None

                    if not appointment:
                        await websocket.send_json({
                            "type": "assistant_reply",
                            "text": "No active appointment found to reschedule. Please book a new appointment.",
                            "state": state_machine.get_state()
                        })
                        session["reschedule_mode"] = False
                        save_session(session_id, session)
                        continue

                    normalized_datetime = normalize_datetime(date, time)

                    if normalized_datetime == "PAST_TIME":
                        # Clear time so patient picks again; keep date
                        session["slots"].pop("time", None)
                        save_session(session_id, session)
                        await websocket.send_json({
                            "type": "assistant_reply",
                            "text": "That time has already passed. Please choose a future time slot.",
                            "state": state_machine.get_state()
                        })
                        continue

                    start_dt = datetime.strptime(normalized_datetime, "%Y-%m-%d %H:%M:%S")

                    # 🚫 Block reschedule outside business hours (9am–6pm)
                    from backend.google_calendar import BUSINESS_START, BUSINESS_END
                    if start_dt.hour < BUSINESS_START or start_dt.hour >= BUSINESS_END:
                        _rs_fmt_date = dateparser.parse(date).strftime("%Y-%m-%d")
                        _rs_slots = generate_available_slots(_rs_fmt_date)
                        _rs_hint = f" Available slots: {', '.join(_rs_slots)}." if isinstance(_rs_slots, list) and _rs_slots else ""
                        session["slots"].pop("time", None)
                        save_session(session_id, session)
                        await websocket.send_json({
                            "type": "assistant_reply",
                            "text": f"Appointments are only available between {BUSINESS_START}am and {BUSINESS_END - 12}pm.{_rs_hint} What time works for you?",
                            "state": state_machine.get_state()
                        })
                        continue

                    if appointment.get("google_event_id"):
                        try:
                            delete_event(appointment["google_event_id"])
                        except Exception as e:
                            print("Calendar delete skipped:", e)

                    # log calendar errors on reschedule
                    try:
                        event_id = create_event(
                            start_datetime=start_dt.isoformat(),
                            service=appointment["service"],
                            summary=f"{appointment['service']}-{appointment['name']}",
                            description="Rescheduled via ALVA",
                            attendee_email=email
                        )
                    except Exception as _cal_err:
                        log_error(_call_id, "calendar", "failure",
                                  detail=str(_cal_err), recovery_action="reschedule_saved_without_event")
                        event_id = None

                    update_appointment_datetime(appointment["id"], normalized_datetime)
                    update_google_event_id(appointment["id"], event_id)
                    update_appointment_status(appointment["id"], "RESCHEDULED")

                    state_machine.transition(
                        "RESCHEDULED",
                        metadata={"reason": "user_rescheduled"}
                    )
                    # TC089
                    record_state_transition(appointment_id, "CONFIRMED", "RESCHEDULED", _call_id)

                    session["reschedule_mode"] = False
                    save_session(session_id, session)

                    await websocket.send_json({
                        "type": "assistant_reply",
                        "text": f"Done! Your appointment has been rescheduled to {normalized_datetime[:16]}.",
                        "state": state_machine.get_state(),
                        "mode": "reschedule_done"
                    })
                    continue
            # ---------------------------

            # Guard: never let the generic AI handle reschedule turns.
            # The reschedule_mode block always does `continue` now, but
            # this is a safety net in case a new code path skips it.
            if session.get("reschedule_mode"):
                _rs_date = session["slots"].get("date")
                _rs_text = "What new date would you like?" if not _rs_date else "What time would you like?"
                await websocket.send_json({"type": "assistant_reply", "text": _rs_text, "state": state_machine.get_state()})
                continue

            reply = generate_reply(session, text)

            #  log ALVA reply ───────────────────────────────
            log_turn(_call_id, "alva", reply)
            #  record end-to-end turn latency ──────────────
            _turn_latency_ms = _analytics_time.monotonic() * 1000 - _turn_start_ms
            record_latency(_turn_latency_ms, call_id=_call_id)
            # ───────────────────────────────────────────────────────

            await websocket.send_json({
                "type": "assistant_reply",
                "text": reply,
                "state": state_machine.get_state(),
            })


    except WebSocketDisconnect:
        #  finalise analytics on disconnect ────
        unregister_websocket(websocket)
        end_transcript(_call_id)
        record_call_outcome(_call_id, _call_outcome)
        # if session ended mid-booking, record drop-off stage
        _session_final = get_session(session_id)
        _slots_final   = _session_final.get("slots", {})
        _fsm_final     = state_machine.get_state()
        if _fsm_final not in ("CONFIRMED", "COMPLETED", "CANCELLED", "RESCHEDULED"):
            _missing = [k for k in ["service","date","time","name","email"]
                        if not _slots_final.get(k)]
            _stage = f"collecting_{_missing[0]}" if _missing else "confirmation"
            record_dropoff(_call_id, _stage)
        # ──────────────────────────────────────────────────────────
        print("Client disconnected:", session_id)
