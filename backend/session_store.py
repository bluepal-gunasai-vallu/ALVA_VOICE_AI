# Simple in-memory session storage

sessions = {}

def get_session(session_id):
    if session_id not in sessions:
        sessions[session_id] = {
            "fsm_state": "INQUIRY",
            "slots": {}
        }
    return sessions[session_id]


def save_session(session_id, data):
    sessions[session_id] = data


def clear_session(session_id):
    if session_id in sessions:
        del sessions[session_id]

# ──────────────────────────────────────────────────
# TC081: Global call counter
# Stored here (not in main.py) so both main.py and
# doctor_routes.py can read/write it without a
# circular import.
# ──────────────────────────────────────────────────
total_calls: int = 0
 
def increment_total_calls():
    global total_calls
    total_calls += 1
 
def get_total_calls() -> int:
    return total_calls
 