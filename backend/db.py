import mysql.connector
from backend.config import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB


# ---------------- DATABASE CONNECTION ---------------- #

def get_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB
    )


# ---------------- APPOINTMENTS ---------------- #

def create_appointment(name, email, service, date_time, state, google_event_id=None, previous_appointment_id=None):

    conn = get_connection()
    cursor = conn.cursor()

    sql = """
    INSERT INTO appointments (name, email, service, date_time, state, google_event_id, previous_appointment_id)
    VALUES (%s,%s,%s,%s,%s,%s,%s)
    """

    cursor.execute(sql, (name, email, service, date_time, state, google_event_id, previous_appointment_id))

    conn.commit()

    appt_id = cursor.lastrowid

    cursor.close()
    conn.close()

    return appt_id


def get_all_appointments():

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM appointments ORDER BY date_time DESC")

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return data


def get_last_appointment_by_email(email):

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM appointments WHERE email=%s ORDER BY date_time DESC LIMIT 1",
        (email,)
    )

    data = cursor.fetchone()

    cursor.close()
    conn.close()

    return data


def update_appointment_status(appointment_id, status):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE appointments SET state=%s WHERE id=%s",
        (status, appointment_id)
    )

    conn.commit()

    cursor.close()
    conn.close()


def update_appointment_datetime(appointment_id, new_datetime):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE appointments SET date_time=%s WHERE id=%s",
        (new_datetime, appointment_id)
    )

    conn.commit()

    cursor.close()
    conn.close()


def update_google_event_id(appointment_id, event_id):

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE appointments SET google_event_id=%s WHERE id=%s",
        (event_id, appointment_id)
    )

    conn.commit()

    cursor.close()
    conn.close()


# ---------------- DOCTOR AVAILABILITY ---------------- #

def set_doctor_availability(date, start_time, end_time, status):

    conn = get_connection()
    cursor = conn.cursor()

    # check if record exists
    cursor.execute(
        "SELECT id FROM doctor_availability WHERE date=%s",
        (date,)
    )

    row = cursor.fetchone()

    if row:
        # update existing record
        cursor.execute("""
            UPDATE doctor_availability
            SET start_time=%s, end_time=%s, status=%s
            WHERE date=%s
        """, (start_time, end_time, status, date))

    else:
        # insert new record
        cursor.execute("""
            INSERT INTO doctor_availability (date,start_time,end_time,status)
            VALUES (%s,%s,%s,%s)
        """, (date, start_time, end_time, status))

    conn.commit()

    cursor.close()
    conn.close()


def get_doctor_availability():

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("SELECT * FROM doctor_availability ORDER BY date ASC")

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return data


def check_doctor_time_conflict(date, time):

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM doctor_availability WHERE date=%s",
        (date,)
    )

    rows = cursor.fetchall()

    cursor.close()
    conn.close()

    for r in rows:

        # 🚫 FULL DAY LEAVE
        if r["status"] == "LEAVE":
            return "LEAVE"

        # ⏰ BUSY TIME RANGE
        if r["status"] == "BUSY":

            if r["start_time"] and r["end_time"]:

                if str(r["start_time"]) <= time <= str(r["end_time"]):
                    return "BUSY"

    return None
# ---------------- FEEDBACK ---------------- #

def save_feedback(name, email, message):

    conn = get_connection()
    cursor = conn.cursor()

    sql = """
    INSERT INTO feedback (name, email, message)
    VALUES (%s, %s, %s)
    """

    cursor.execute(sql, (name, email, message))

    conn.commit()

    cursor.close()
    conn.close()


def save_feedback_score(appointment_id, score, channel='voice'):
    """
    TC084: Save a numeric feedback score (1–5) linked to an appointment_id.
    Stores feedback_score, feedback_timestamp, and feedback_channel.
    """
    conn = get_connection()
    cursor = conn.cursor()

    sql = """
    UPDATE appointments
    SET feedback_score = %s,
        feedback_timestamp = NOW(),
        feedback_channel = %s
    WHERE id = %s
    """

    cursor.execute(sql, (score, channel, appointment_id))

    conn.commit()

    cursor.close()
    conn.close()


def get_feedback_scores():
    """
    TC087: Return all appointments that have a feedback score for aggregate metric.
    """
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT id, name, service, date_time, feedback_score, feedback_timestamp, feedback_channel
        FROM appointments
        WHERE feedback_score IS NOT NULL
        ORDER BY feedback_timestamp ASC
    """)

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return data


def get_average_feedback_score():
    """
    TC087: Compute average satisfaction score across all completed appointments.
    """
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT AVG(feedback_score) as avg_score
        FROM appointments
        WHERE feedback_score IS NOT NULL
    """)

    row = cursor.fetchone()

    cursor.close()
    conn.close()

    if row and row[0] is not None:
        return round(float(row[0]), 1)

    return None


# ---------------- FOLLOW-UP TRACKING ---------------- #

def save_followup_attempt(appointment_id, attempt_number, status):
    """
    TC085: Log each follow-up call attempt (SENT, SKIPPED).
    """
    conn = get_connection()
    cursor = conn.cursor()

    sql = """
    INSERT INTO followup_attempts (appointment_id, attempt_number, status, attempted_at)
    VALUES (%s, %s, %s, NOW())
    ON DUPLICATE KEY UPDATE status = %s, attempted_at = NOW()
    """

    cursor.execute(sql, (appointment_id, attempt_number, status, status))

    conn.commit()

    cursor.close()
    conn.close()


def get_followup_attempts(appointment_id):
    """Return all follow-up attempts for a given appointment."""
    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT * FROM followup_attempts
        WHERE appointment_id = %s
        ORDER BY attempted_at ASC
    """, (appointment_id,))

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return data


def mark_followup_skipped(appointment_id):
    """TC085: Mark post-appointment follow-up as SKIPPED after max retries."""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        UPDATE appointments
        SET followup_status = 'SKIPPED'
        WHERE id = %s
    """, (appointment_id,))

    conn.commit()

    cursor.close()
    conn.close()
def is_doctor_on_leave(date):

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM doctor_availability WHERE date=%s AND status='LEAVE'",
        (date,)
    )

    row = cursor.fetchone()

    cursor.close()
    conn.close()

    return row is not None

def save_noshow_reason(name, email, reason):
    """Save the patient's no-show reason to the feedback table (reused for storage)."""

    conn = get_connection()
    cursor = conn.cursor()

    sql = """
    INSERT INTO feedback (name, email, message)
    VALUES (%s, %s, %s)
    """

    # Prefix message so it's distinguishable from regular feedback
    cursor.execute(sql, (name, email, f"[NO-SHOW REASON] {reason}"))

    conn.commit()

    cursor.close()
    conn.close()


def get_noshow_appointments():
    """Fetch all appointments with NO_SHOW status."""

    conn = get_connection()
    cursor = conn.cursor(dictionary=True)

    cursor.execute(
        "SELECT * FROM appointments WHERE state='NO_SHOW' ORDER BY date_time DESC"
    )

    data = cursor.fetchall()

    cursor.close()
    conn.close()

    return data