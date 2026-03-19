"""
escalation.py — Human Handoff & Escalation Module
Covers TC075–TC081: explicit request, low-confidence ASR, out-of-scope detection,
context packet, no-agent fallback, escalation logging, and KPI reporting.
"""

import uuid
from datetime import datetime
from typing import Optional

# ──────────────────────────────────────────────────
# IN-MEMORY STORES
# ──────────────────────────────────────────────────

# Escalation log: list of dicts (TC080)
escalation_log: list[dict] = []

# Human agent queue: list of context-packet dicts (TC075, TC079)
human_queue: list[dict] = []

# Simulated agent pool — set to 0 to test TC079 (no agents available)
available_agents: int = 3

# ──────────────────────────────────────────────────
# ESCALATION REASONS
# ──────────────────────────────────────────────────

REASON_EXPLICIT       = "explicit_customer_request"   # TC075
REASON_LOW_CONFIDENCE = "low_confidence_asr"          # TC076
REASON_OUT_OF_SCOPE   = "out_of_scope_request"        # TC077
REASON_TIMEOUT        = "timeout"
REASON_EMOTION        = "emotion_detected"

# ──────────────────────────────────────────────────
# OUT-OF-SCOPE TOPICS (TC077)
# ──────────────────────────────────────────────────

OUT_OF_SCOPE_KEYWORDS = [
    "insurance",
    "pre-authorisation",
    "pre-authorization",
    "preauthorization",
    "preauthorisation",
    "billing",
    "refund",
    "prescription refill",
    "lab result",
    "test result",
    "second opinion",
    "referral",
    "legal",
    "complaint",
    "lawsuit",
]

# ──────────────────────────────────────────────────
# EXPLICIT HANDOFF PHRASES (TC075)
# ──────────────────────────────────────────────────

HUMAN_REQUEST_PHRASES = [
    "speak to a person",
    "speak to a human",
    "speak to someone",
    "talk to a person",
    "talk to a human",
    "talk to someone",
    "connect me to a doctor",
    "connect me to a human",
    "live agent",
    "real person",
    "human agent",
    "human assistant",
    "operator",
    "want a person",
    "want a human",
    "need a person",
    "need a human",
    "i want to speak",
    "connect me now",
    "transfer me",
    "human please",
]

# ──────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────

def _log_escalation(
    call_id: str,
    reason: str,
    appointment_state: str,
    session: dict,
    extra: dict = None
) -> dict:
    """
    TC080: Create and store a structured escalation log entry.
    Every entry contains: call_id, escalation_reason, timestamp,
    appointment_state_at_escalation, conversation_transcript.
    """
    entry = {
        "call_id":                        call_id,
        "escalation_reason":              reason,
        "timestamp":                      datetime.utcnow().isoformat() + "Z",
        "appointment_state_at_escalation": appointment_state,
        "customer_name":                  session.get("slots", {}).get("name"),
        "detected_intent":                session.get("last_intent"),
        "appointment_details":            session.get("slots", {}),
        "conversation_transcript":        session.get("history", []),
        **(extra or {}),
    }
    escalation_log.append(entry)
    print(f"[escalation] Logged: reason={reason} call_id={call_id}")
    return entry


def _build_context_packet(call_id: str, reason: str, session: dict, appointment_state: str) -> dict:
    """
    TC078: Build the complete context packet sent to the human agent.
    Must include: call_id, customer name, detected intent, appointment details,
    full conversation transcript, escalation_reason.
    """
    return {
        "call_id":               call_id,
        "escalation_reason":     reason,
        "customer_name":         session.get("slots", {}).get("name"),
        "detected_intent":       session.get("last_intent"),
        "appointment_details":   session.get("slots", {}),
        "conversation_transcript": session.get("history", []),
        "appointment_state":     appointment_state,
        "timestamp":             datetime.utcnow().isoformat() + "Z",
    }


# ──────────────────────────────────────────────────
# DETECTION FUNCTIONS
# ──────────────────────────────────────────────────

def is_explicit_human_request(text: str) -> bool:
    """TC075: Return True if the customer explicitly wants a human agent."""
    lower = text.lower()
    return any(phrase in lower for phrase in HUMAN_REQUEST_PHRASES)


def is_out_of_scope(text: str) -> bool:
    """TC077: Return True if the request is beyond ALVA's capability."""
    lower = text.lower()
    return any(keyword in lower for keyword in OUT_OF_SCOPE_KEYWORDS)


# ──────────────────────────────────────────────────
# CORE ESCALATION HANDLER
# ──────────────────────────────────────────────────

def handle_escalation(
    session: dict,
    call_id: str,
    reason: str,
    appointment_state: str,
    extra_log_data: dict = None,
) -> dict:
    """
    Central escalation handler used by all TC075–TC080 paths.

    Returns a result dict with:
      - reply        : text ALVA should speak to the customer
      - escalated    : bool
      - no_agent     : bool (TC079 — no human available)
      - context_packet: the packet sent to the human queue
    """
    context_packet = _build_context_packet(call_id, reason, session, appointment_state)

    # Log every escalation (TC080)
    _log_escalation(call_id, reason, appointment_state, session, extra_log_data)

    # TC079: check agent availability
    if available_agents <= 0:
        reply = (
            "I'm sorry, there are no agents available right now. "
            "I can schedule a callback for you — would you like that?"
        )
        return {
            "reply":          reply,
            "escalated":      False,
            "no_agent":       True,
            "context_packet": context_packet,
        }

    # Agents available — enqueue and hand off
    human_queue.append(context_packet)

    if reason == REASON_EXPLICIT:
        reply = "Let me connect you now. Please hold while I transfer you to a human agent."
    elif reason == REASON_LOW_CONFIDENCE:
        reply = (
            "I'm having trouble understanding you. "
            "Let me connect you to a human agent who can assist you better."
        )
    elif reason == REASON_OUT_OF_SCOPE:
        reply = (
            "That request is outside what I can help with directly. "
            "Let me connect you to a specialist who can assist you."
        )
    else:
        reply = "Let me connect you to a human agent now. Please hold."

    return {
        "reply":          reply,
        "escalated":      True,
        "no_agent":       False,
        "context_packet": context_packet,
    }


# ──────────────────────────────────────────────────
# LOW-CONFIDENCE TRACKER (TC076)
# ──────────────────────────────────────────────────

LOW_CONFIDENCE_THRESHOLD = 0.5
LOW_CONFIDENCE_LIMIT     = 3   # escalate after this many consecutive low-conf turns


def track_asr_confidence(session: dict, confidence: float) -> bool:
    """
    TC076: Track consecutive low-confidence ASR scores per session.
    Returns True when the threshold is reached and escalation should be triggered.
    """
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        session["low_confidence_count"] = session.get("low_confidence_count", 0) + 1
    else:
        session["low_confidence_count"] = 0   # reset on a good turn

    return session["low_confidence_count"] >= LOW_CONFIDENCE_LIMIT


# ──────────────────────────────────────────────────
# ESCALATION RATE KPI (TC081)
# ──────────────────────────────────────────────────

def get_escalation_kpi(total_calls: int = None) -> dict:
    """
    TC081: Return escalation rate, breakdown by reason, and pass/fail vs 20 % KPI.
    """
    total_escalations = len(escalation_log)

    by_reason: dict[str, int] = {}
    for entry in escalation_log:
        r = entry["escalation_reason"]
        by_reason[r] = by_reason.get(r, 0) + 1

    rate = None
    passed_kpi = None
    if total_calls and total_calls > 0:
        rate = round(total_escalations / total_calls * 100, 1)
        passed_kpi = rate <= 20.0

    return {
        "total_escalations":  total_escalations,
        "total_calls":        total_calls,
        "escalation_rate_pct": rate,
        "kpi_passed":         passed_kpi,       # True = rate <= 20 %
        "breakdown_by_reason": by_reason,
        "log":                escalation_log,
    }


# ──────────────────────────────────────────────────
# ACCESSORS (for doctor dashboard / tests)
# ──────────────────────────────────────────────────

def get_escalation_log() -> list[dict]:
    """TC080: Return full escalation log."""
    return escalation_log


def get_human_queue() -> list[dict]:
    """Return current human agent queue."""
    return human_queue


def set_available_agents(n: int):
    """Adjust agent pool at runtime (useful for TC079 testing)."""
    global available_agents
    available_agents = n