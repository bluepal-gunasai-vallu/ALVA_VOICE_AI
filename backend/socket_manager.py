# socket_manager.py

# Raw list of all connected WebSocket objects (used for broadcast)
connections = []

# Email -> WebSocket mapping so we can target a specific patient
# Key: patient email (str)   Value: WebSocket object
email_connections = {}

# Pending message queue: messages queued when a patient is not yet connected.
# Flushed automatically when the patient registers their email.
# Key: patient email (str)   Value: list of payload dicts
pending_messages = {}

# Modes that must NEVER be broadcast to all connections.
# These are personal messages — if the patient is not connected they get
# queued, not leaked to every other open session.
PRIVATE_MODES = {"post_appointment", "feedback", "noshow"}


def register_email(email: str, websocket):
    """
    Called when a patient identifies their email over the WebSocket.
    Also flushes any pending messages queued while they were offline.
    """
    if not email:
        return

    key = email.strip().lower()
    email_connections[key] = websocket

    # Flush pending messages for this email
    queued = pending_messages.pop(key, [])
    if queued:
        import asyncio
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            pass

        async def _flush():
            for payload in queued:
                try:
                    await websocket.send_json(payload)
                except Exception as e:
                    print(f"[socket_manager] Failed to flush pending message: {e}")

        if loop and loop.is_running():
            asyncio.ensure_future(_flush())
        print(f"[socket_manager] Flushed {len(queued)} pending message(s) to {email}")


def unregister_websocket(websocket):
    """Clean up both registries when a client disconnects."""
    if websocket in connections:
        connections.remove(websocket)
    # Remove from email map
    to_remove = [k for k, v in email_connections.items() if v is websocket]
    for k in to_remove:
        del email_connections[k]


async def send_voice_message(
    message: str,
    email: str = None,
    mode: str = "feedback",
    appointment_id: int = None
):
    """
    Send a voice notification.

    Targeting rules:
    - If email provided AND patient connected → send immediately.
    - If email provided AND patient NOT connected:
        * Private modes (post_appointment, feedback, noshow) → QUEUE the message.
          It will be delivered automatically when the patient connects and registers.
        * Non-private modes → broadcast as before.
    - If no email → broadcast to all connections.
    """
    payload = {
        "type":           "voice_notification",
        "text":           message,
        "mode":           mode,
        "email":          email,
        "appointment_id": appointment_id,
    }

    if email:
        key = email.strip().lower()
        target = email_connections.get(key)

        if target:
            # Patient is connected — deliver immediately
            await target.send_json(payload)
            return

        # Patient not connected
        if mode in PRIVATE_MODES:
            # Queue for delivery when they connect
            if key not in pending_messages:
                pending_messages[key] = []
            pending_messages[key].append(payload)
            print(f"[socket_manager] Patient {email} not connected; "
                  f"queued mode='{mode}' message (will deliver on connect).")
            return

        # Non-private: fall through to broadcast

    # Broadcast to all connections (no email or non-private fallback)
    for conn in connections:
        await conn.send_json(payload)