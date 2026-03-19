"""
analytics_db.py — Persistent MySQL storage for Analytics (TC088–TC095)

All analytics data that was previously in-memory is now written to
four lightweight tables so history survives server restarts and tab closes.

Tables created automatically on first import:
  - analytics_call_outcomes   (TC088, TC095)
  - analytics_state_transitions (TC089)
  - analytics_dropoffs          (TC090)
  - analytics_latency           (TC091)
  - analytics_transcripts       (TC092)
  - analytics_transcript_turns  (TC092)
  - analytics_errors            (TC093)

Usage: import this module and call the functions exactly as you would
the in-memory functions in analytics.py — the signatures are identical
so analytics.py can simply delegate here.
"""

from backend.db import get_connection
from datetime import datetime


# ══════════════════════════════════════════════════════
# SCHEMA BOOTSTRAP — runs once on import
# ══════════════════════════════════════════════════════

def _bootstrap():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_call_outcomes (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            call_id     VARCHAR(128) NOT NULL,
            outcome     VARCHAR(32)  NOT NULL,
            recorded_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_call_id (call_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_state_transitions (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            appointment_id VARCHAR(128),
            from_state     VARCHAR(64),
            to_state       VARCHAR(64),
            transition_key VARCHAR(128),
            call_id        VARCHAR(128),
            recorded_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_appt (appointment_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_dropoffs (
            id             INT AUTO_INCREMENT PRIMARY KEY,
            call_id        VARCHAR(128),
            dialogue_stage VARCHAR(128),
            turn_number    INT,
            recorded_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_latency (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            call_id     VARCHAR(128),
            latency_ms  FLOAT NOT NULL,
            turn_num    INT,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_call (call_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_transcripts (
            call_id        VARCHAR(128) PRIMARY KEY,
            appointment_id VARCHAR(128),
            mask_pii       TINYINT(1) DEFAULT 1,
            started_at     DATETIME,
            ended_at       DATETIME
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_transcript_turns (
            id          INT AUTO_INCREMENT PRIMARY KEY,
            call_id     VARCHAR(128) NOT NULL,
            turn_index  INT,
            role        VARCHAR(32),
            text        TEXT,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_call (call_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analytics_errors (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            error_id             VARCHAR(32),
            call_id              VARCHAR(128),
            component            VARCHAR(64),
            error_type           VARCHAR(64),
            detail               TEXT,
            recovery_action      VARCHAR(128),
            recorded_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
            INDEX idx_call (call_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """)

    conn.commit()
    cursor.close()
    conn.close()


try:
    _bootstrap()
except Exception as _e:
    print(f"[analytics_db] Bootstrap warning: {_e}")


# ══════════════════════════════════════════════════════
# TC088 / TC095 — CALL OUTCOMES
# ══════════════════════════════════════════════════════

def db_record_call_outcome(call_id: str, outcome: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO analytics_call_outcomes (call_id, outcome) VALUES (%s, %s)",
        (call_id, outcome)
    )
    conn.commit()
    cursor.close()
    conn.close()


def db_get_call_success_rate() -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT outcome, COUNT(*) as cnt FROM analytics_call_outcomes GROUP BY outcome")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    counts = {r["outcome"]: r["cnt"] for r in rows}
    total     = sum(counts.values())
    successes = counts.get("success", 0)
    escalated = counts.get("escalated", 0)
    failed    = counts.get("failed", 0)
    rate      = round(successes / total * 100, 1) if total > 0 else 0.0

    return {
        "success_count":    successes,
        "escalated_count":  escalated,
        "failed_count":     failed,
        "total_calls":      total,
        "success_rate_pct": rate,
        "last_updated":     datetime.utcnow().isoformat() + "Z",
    }


def db_get_containment_rate() -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT outcome, COUNT(*) as cnt FROM analytics_call_outcomes GROUP BY outcome")
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    counts    = {r["outcome"]: r["cnt"] for r in rows}
    total     = sum(counts.values())
    contained = counts.get("success", 0)
    escalated = counts.get("escalated", 0) + counts.get("failed", 0)
    rate      = round(contained / total * 100, 1) if total > 0 else 0.0

    return {
        "contained_calls":      contained,
        "escalated_calls":      escalated,
        "total_calls":          total,
        "containment_rate_pct": rate,
        "kpi_passed":           rate >= 80.0 if total > 0 else None,
        "kpi_target_pct":       80.0,
    }


# ══════════════════════════════════════════════════════
# TC089 — STATE TRANSITIONS
# ══════════════════════════════════════════════════════

def db_record_state_transition(appointment_id, from_state: str, to_state: str, call_id: str = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO analytics_state_transitions
            (appointment_id, from_state, to_state, transition_key, call_id)
        VALUES (%s, %s, %s, %s, %s)
    """, (str(appointment_id), from_state, to_state, f"{from_state}→{to_state}", call_id))
    conn.commit()
    cursor.close()
    conn.close()


def db_get_state_transition_report() -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT transition_key, COUNT(*) as cnt
        FROM analytics_state_transitions
        GROUP BY transition_key
        ORDER BY cnt DESC
    """)
    rows = cursor.fetchall()
    cursor.execute("SELECT COUNT(*) as total FROM analytics_state_transitions")
    total = cursor.fetchone()["total"]
    cursor.close()
    conn.close()

    counts = {r["transition_key"]: r["cnt"] for r in rows}
    return {
        "transition_counts":  counts,
        "total_transitions":  total,
    }


# ══════════════════════════════════════════════════════
# TC090 — DROP-OFFS
# ══════════════════════════════════════════════════════

def db_record_dropoff(call_id: str, dialogue_stage: str, turn_number: int = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO analytics_dropoffs (call_id, dialogue_stage, turn_number) VALUES (%s, %s, %s)",
        (call_id, dialogue_stage, turn_number)
    )
    conn.commit()
    cursor.close()
    conn.close()


def db_get_dropoff_report() -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT dialogue_stage, COUNT(*) as cnt
        FROM analytics_dropoffs
        GROUP BY dialogue_stage
        ORDER BY cnt DESC
    """)
    rows = cursor.fetchall()
    total_q = conn.cursor()
    total_q.execute("SELECT COUNT(*) as total FROM analytics_dropoffs")
    total = total_q.fetchone()[0]
    total_q.close()
    cursor.close()
    conn.close()

    all_counts = {r["dialogue_stage"]: r["cnt"] for r in rows}
    top3 = [{"stage": r["dialogue_stage"], "count": r["cnt"]} for r in rows[:3]]

    return {
        "top_dropoff_points": top3,
        "all_stage_counts":   all_counts,
        "total_dropoffs":     total,
    }


# ══════════════════════════════════════════════════════
# TC091 — LATENCY
# ══════════════════════════════════════════════════════

def db_record_latency(latency_ms: float, call_id: str = None, turn: int = None):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO analytics_latency (call_id, latency_ms, turn_num) VALUES (%s, %s, %s)",
        (call_id, latency_ms, turn)
    )
    conn.commit()
    cursor.close()
    conn.close()


def db_get_latency_report() -> dict:
    import math
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT latency_ms FROM analytics_latency ORDER BY latency_ms ASC")
    rows   = [r["latency_ms"] for r in cursor.fetchall()]
    cursor.close()
    conn.close()

    if not rows:
        return {
            "p95_ms": None, "p50_ms": None, "avg_ms": None,
            "min_ms": None, "max_ms": None, "sample_count": 0,
            "kpi_passed": None, "alert_triggered": False,
            "alert_threshold_ms": 2500,
        }

    n = len(rows)
    def pct(p): return round(rows[max(0, math.ceil(p / 100 * n) - 1)], 2)

    p95 = pct(95)
    return {
        "p95_ms":            p95,
        "p50_ms":            pct(50),
        "avg_ms":            round(sum(rows) / n, 2),
        "min_ms":            round(rows[0], 2),
        "max_ms":            round(rows[-1], 2),
        "sample_count":      n,
        "kpi_passed":        p95 <= 2000,
        "alert_triggered":   p95 > 2500,
        "alert_threshold_ms": 2500,
    }


# ══════════════════════════════════════════════════════
# TC092 — TRANSCRIPTS
# ══════════════════════════════════════════════════════

import re as _re

def _mask_pii(text: str) -> str:
    return _re.sub(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        "[EMAIL_MASKED]", text
    )


def db_start_transcript(call_id: str, appointment_id=None, mask_pii: bool = True):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO analytics_transcripts (call_id, appointment_id, mask_pii, started_at)
        VALUES (%s, %s, %s, NOW())
        ON DUPLICATE KEY UPDATE started_at = NOW()
    """, (call_id, str(appointment_id) if appointment_id else None, int(mask_pii)))
    conn.commit()
    cursor.close()
    conn.close()


def db_log_turn(call_id: str, role: str, text: str):
    # check mask_pii preference
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT mask_pii FROM analytics_transcripts WHERE call_id=%s", (call_id,))
    row = cursor.fetchone()
    should_mask = row["mask_pii"] if row else True

    content = _mask_pii(text) if should_mask else text

    # count existing turns for index
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM analytics_transcript_turns WHERE call_id=%s", (call_id,)
    )
    idx = (cursor.fetchone()["cnt"] or 0) + 1

    cursor.execute("""
        INSERT INTO analytics_transcript_turns (call_id, turn_index, role, text)
        VALUES (%s, %s, %s, %s)
    """, (call_id, idx, role, content))
    conn.commit()
    cursor.close()
    conn.close()


def db_end_transcript(call_id: str):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE analytics_transcripts SET ended_at=NOW() WHERE call_id=%s", (call_id,)
    )
    conn.commit()
    cursor.close()
    conn.close()


def db_get_transcript(call_id: str):
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM analytics_transcripts WHERE call_id=%s", (call_id,))
    t = cursor.fetchone()
    if not t:
        cursor.close()
        conn.close()
        return None
    cursor.execute(
        "SELECT * FROM analytics_transcript_turns WHERE call_id=%s ORDER BY turn_index ASC",
        (call_id,)
    )
    turns = cursor.fetchall()
    cursor.close()
    conn.close()
    # Normalise datetime objects to strings
    for key in ("started_at", "ended_at"):
        if t.get(key) and hasattr(t[key], "isoformat"):
            t[key] = t[key].isoformat() + "Z"
    for turn in turns:
        if turn.get("recorded_at") and hasattr(turn["recorded_at"], "isoformat"):
            turn["recorded_at"] = turn["recorded_at"].isoformat() + "Z"
    t["turns"] = turns
    return t


def db_get_all_transcripts():
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT call_id FROM analytics_transcripts ORDER BY started_at DESC")
    call_ids = [r["call_id"] for r in cursor.fetchall()]
    cursor.close()
    conn.close()
    return [t for cid in call_ids if (t := db_get_transcript(cid))]


# ══════════════════════════════════════════════════════
# TC093 — ERROR LOG
# ══════════════════════════════════════════════════════

def db_log_error(call_id: str, component: str, error_type: str,
                 detail: str = "", recovery_action: str = "none"):
    import uuid
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO analytics_errors
            (error_id, call_id, component, error_type, detail, recovery_action)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (str(uuid.uuid4())[:8], call_id, component, error_type, detail, recovery_action))
    conn.commit()
    cursor.close()
    conn.close()


def db_get_error_summary() -> dict:
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM analytics_errors ORDER BY recorded_at DESC")
    errors = cursor.fetchall()
    cursor.close()
    conn.close()

    # Normalise datetimes
    for e in errors:
        if e.get("recorded_at") and hasattr(e["recorded_at"], "isoformat"):
            e["recorded_at"] = e["recorded_at"].isoformat() + "Z"
        # rename for frontend compatibility
        e["timestamp"] = e.pop("recorded_at", None)
        e["recovery_action_taken"] = e.pop("recovery_action", "")

    by_component: dict = {}
    by_type: dict = {}
    for e in errors:
        by_component[e["component"]] = by_component.get(e["component"], 0) + 1
        by_type[e["error_type"]]     = by_type.get(e["error_type"], 0) + 1

    return {
        "total_errors":  len(errors),
        "by_component":  by_component,
        "by_type":       by_type,
        "errors":        errors,
    }


# ══════════════════════════════════════════════════════
# COMPOSITE SNAPSHOT
# ══════════════════════════════════════════════════════

def db_get_full_analytics_snapshot() -> dict:
    from backend.analytics import get_pipeline_snapshot   # still reads live DB appointments
    return {
        "call_success_rate": db_get_call_success_rate(),
        "state_transitions": db_get_state_transition_report(),
        "dropoff_analysis":  db_get_dropoff_report(),
        "latency":           db_get_latency_report(),
        "errors":            db_get_error_summary(),
        "pipeline":          get_pipeline_snapshot(),
        "containment":       db_get_containment_rate(),
    }