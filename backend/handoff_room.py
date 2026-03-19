"""
handoff_room.py — Real-time Patient ↔ Human Agent relay

When ALVA escalates, the patient stays on their existing WebSocket.
A human agent connects to  /ws/agent/<room_id>  and messages flow
bidirectionally between both parties.

Room lifecycle:
  1. escalation.py calls  create_room(session_id, patient_ws, context_packet)
  2. Agent opens  /ws/agent/<room_id>  → joins room
  3. Every message from patient is forwarded to agent (and vice-versa)
  4. Either side can close the room with  __end_handoff__
"""

from datetime import datetime
from typing import Optional

# room_id -> HandoffRoom
_rooms: dict[str, "HandoffRoom"] = {}


class HandoffRoom:
    def __init__(self, room_id: str, patient_ws, context_packet: dict):
        self.room_id        = room_id
        self.patient_ws     = patient_ws      # existing patient WebSocket
        self.agent_ws       = None            # set when agent joins
        self.context_packet = context_packet  # TC078 context
        self.created_at     = datetime.utcnow().isoformat() + "Z"
        self.ended_at: Optional[str] = None
        self.transcript: list[dict] = []      # live relay transcript

    # ── send helpers ──────────────────────────────────────────────

    async def send_to_patient(self, text: str, sender: str = "agent"):
        payload = {
            "type":    "handoff_message",
            "sender":  sender,
            "text":    text,
            "room_id": self.room_id,
        }
        self.transcript.append({**payload, "ts": datetime.utcnow().isoformat()})
        try:
            await self.patient_ws.send_json(payload)
        except Exception as e:
            print(f"[handoff_room] send_to_patient failed: {e}")

    async def send_to_agent(self, text: str, sender: str = "patient"):
        if not self.agent_ws:
            return
        payload = {
            "type":    "handoff_message",
            "sender":  sender,
            "text":    text,
            "room_id": self.room_id,
        }
        self.transcript.append({**payload, "ts": datetime.utcnow().isoformat()})
        try:
            await self.agent_ws.send_json(payload)
        except Exception as e:
            print(f"[handoff_room] send_to_agent failed: {e}")

    # ── lifecycle ─────────────────────────────────────────────────

    def end(self):
        self.ended_at = datetime.utcnow().isoformat() + "Z"
        _rooms.pop(self.room_id, None)
        print(f"[handoff_room] Room {self.room_id} closed.")


# ── public API ────────────────────────────────────────────────────

def create_room(room_id: str, patient_ws, context_packet: dict) -> "HandoffRoom":
    room = HandoffRoom(room_id, patient_ws, context_packet)
    _rooms[room_id] = room
    print(f"[handoff_room] Room created: {room_id}")
    return room


def get_room(room_id: str) -> Optional["HandoffRoom"]:
    return _rooms.get(room_id)


def get_all_rooms() -> list[dict]:
    return [
        {
            "room_id":        r.room_id,
            "created_at":     r.created_at,
            "ended_at":       r.ended_at,
            "agent_joined":   r.agent_ws is not None,
            "context_packet": r.context_packet,
        }
        for r in _rooms.values()
    ]