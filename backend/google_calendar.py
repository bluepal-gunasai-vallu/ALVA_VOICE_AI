from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import datetime
import os
import pickle

SCOPES = ['https://www.googleapis.com/auth/calendar']


# ---------------- CALENDAR CONFIG ---------------- #

BUSINESS_START = 9
BUSINESS_END = 18
def is_within_business_hours(start_datetime):

    import datetime

    dt = datetime.datetime.fromisoformat(start_datetime)

    hour = dt.hour

    if hour < BUSINESS_START or hour >= BUSINESS_END:
        return False

    return True

SLOT_DURATION = 60  # minutes per slot

CLOSED_DAYS = ["Sunday"]

HOLIDAYS = [
    "2026-12-25"
]

SERVICE_DURATION = {
    "basic": 60,
    "specialist": 60,
    "full service": 120
}


# ---------------- GOOGLE AUTH ---------------- #

def get_calendar_service():

    creds = None

    if os.path.exists('token.pickle'):
        with open('token.pickle', 'rb') as token:
            creds = pickle.load(token)

    if not creds or not creds.valid:

        flow = InstalledAppFlow.from_client_secrets_file(
            'credentials.json', SCOPES)

        creds = flow.run_local_server(port=0)

        with open('token.pickle', 'wb') as token:
            pickle.dump(creds, token)

    service = build('calendar', 'v3', credentials=creds)

    return service


# ---------------- BUSINESS DAY VALIDATION ---------------- #

def get_next_open_date(from_date: str) -> str:
    """
    Return the nearest open date strictly after from_date,
    skipping CLOSED_DAYS and HOLIDAYS.
    Searches up to 14 days ahead to avoid infinite loops.
    """
    d = datetime.datetime.strptime(from_date, "%Y-%m-%d")

    for _ in range(14):
        d += datetime.timedelta(days=1)
        candidate = d.strftime("%Y-%m-%d")

        if d.strftime("%A") in CLOSED_DAYS:
            continue

        if candidate in HOLIDAYS:
            continue

        return candidate

    return None  # clinic closed for an extended period — very unlikely


def is_clinic_open(date):

    d = datetime.datetime.strptime(date, "%Y-%m-%d")

    day_name = d.strftime("%A")

    if day_name in CLOSED_DAYS:
        next_date = get_next_open_date(date)
        msg = f"We are not open on {day_name}s."
        if next_date:
            msg += f" The next available date is {next_date}."
        return False, msg

    if date in HOLIDAYS:
        next_date = get_next_open_date(date)
        msg = "The clinic is closed on this date."
        if next_date:
            msg += f" The next available date is {next_date}."
        return False, msg

    return True, "Open"


# ---------------- CREATE EVENT (WITH SERVICE DURATION) ---------------- #

# def create_event(start_datetime, service, summary, description, attendee_email):

#     service_api = get_calendar_service()

#     start_dt = datetime.datetime.fromisoformat(start_datetime)

#     service_name = service.lower()

#     if "full" in service_name:
#         duration = 120
#     else:
#         duration = 60

#     end_dt = start_dt + datetime.timedelta(minutes=duration)

#     end_datetime = end_dt.isoformat()

#     event = {
#         'summary': summary,
#         'description': description,
#         'start': {
#             'dateTime': start_datetime,
#             'timeZone': 'Asia/Kolkata',
#         },
#         'end': {
#             'dateTime': end_datetime,
#             'timeZone': 'Asia/Kolkata',
#         },
#         'attendees': [
#             {'email': attendee_email},
#         ],
#         'reminders': {
#             'useDefault': False,
#             'overrides': [
#                 {'method': 'email', 'minutes': 1440},
#                 {'method': 'popup', 'minutes': 30},
#             ],
#         },
#     }

#     event = service_api.events().insert(
#         calendarId='primary',
#         body=event
#     ).execute()

#     return event['id']
# def create_event(start_datetime, service, summary, description, attendee_email):

#     # BLOCK BOOKINGS OUTSIDE BUSINESS HOURS
#     if not is_within_business_hours(start_datetime):
#         raise Exception("Appointments allowed only between 9am and 5pm")

#     service_api = get_calendar_service()

#     start_dt = datetime.datetime.fromisoformat(start_datetime)

#     service_name = service.lower()

#     if "full" in service_name:
#         duration = 120
#     else:
#         duration = 60

def create_event(start_datetime, service, summary, description, attendee_email):

    # 🔴 BLOCK BOOKINGS OUTSIDE BUSINESS HOURS
    start_dt = datetime.datetime.fromisoformat(start_datetime)

    if start_dt.hour < BUSINESS_START or start_dt.hour >= BUSINESS_END:
        raise Exception("Appointments allowed only between 9am and 5pm")

    service_api = get_calendar_service()

    service_name = service.lower()

    if "full" in service_name:
        duration = 120
    else:
        duration = 60

    end_dt = start_dt + datetime.timedelta(minutes=duration)

    end_datetime = end_dt.isoformat()

    event = {
        'summary': summary,
        'description': description,
        'start': {
            'dateTime': start_datetime,
            'timeZone': 'Asia/Kolkata',
        },
        'end': {
            'dateTime': end_datetime,
            'timeZone': 'Asia/Kolkata',
        },
        'attendees': [
            {'email': attendee_email},
        ],
        'reminders': {
            'useDefault': False,
            'overrides': [
                {'method': 'email', 'minutes': 1440},
                {'method': 'popup', 'minutes': 30},
            ],
        },
    }

    event = service_api.events().insert(
        calendarId='primary',
        body=event
    ).execute()

    return event['id']

# ---------------- BUSY SLOTS ---------------- #

def get_busy_slots(date):

    service = get_calendar_service()

    start_of_day = f"{date}T00:00:00+05:30"
    end_of_day = f"{date}T23:59:59+05:30"

    body = {
        "timeMin": start_of_day,
        "timeMax": end_of_day,
        "timeZone": "Asia/Kolkata",
        "items": [{"id": "primary"}]
    }

    events_result = service.freebusy().query(body=body).execute()

    busy_times = events_result['calendars']['primary']['busy']

    return busy_times


# ---------------- SLOT GENERATOR ---------------- #

def generate_available_slots(date):

    open_status, message = is_clinic_open(date)

    if not open_status:
        next_date = get_next_open_date(date)
        return {
            "error": message,
            "next_open_date": next_date
        }

    busy_times = get_busy_slots(date)

    now = datetime.datetime.now()

    working_hours = range(BUSINESS_START, BUSINESS_END)

    available = []

    for hour in working_hours:

        slot_time = datetime.datetime.strptime(
            f"{date} {hour}:00",
            "%Y-%m-%d %H:%M"
        )

        slot_start = f"{date}T{hour:02d}:00:00+05:30"

        conflict = False

        for busy in busy_times:
            if busy['start'] <= slot_start < busy['end']:
                conflict = True
                break

        # Same-day booking rule (TC065)
        if slot_time.date() == now.date():

            diff = (slot_time - now).total_seconds() / 3600

            if diff < 1:
                conflict = True

        if not conflict:

            suffix = "am"
            display_hour = hour

            if hour >= 12:
                suffix = "pm"

            display_hour = hour % 12

            if display_hour == 0:
                display_hour = 12

            available.append(f"{display_hour}{suffix}")

    return available


# ---------------- DELETE EVENT ---------------- #

def delete_event(event_id):

    service = get_calendar_service()

    service.events().delete(
        calendarId="primary",
        eventId=event_id
    ).execute()

    return True




# ---------------- DOCTOR BLOCK ---------------- #

def create_doctor_block(date, start_time, end_time, status):

    service = get_calendar_service()

    if status == "LEAVE":

        start_datetime = f"{date}T00:00:00+05:30"
        end_datetime = f"{date}T23:59:00+05:30"

    else:

        start_datetime = f"{date}T{start_time}:00+05:30"
        end_datetime = f"{date}T{end_time}:00+05:30"

    event = {
        "summary": f"Doctor {status}",
        "description": "Blocked via ALVA Doctor Dashboard",
        "start": {
            "dateTime": start_datetime,
            "timeZone": "Asia/Kolkata"
        },
        "end": {
            "dateTime": end_datetime,
            "timeZone": "Asia/Kolkata"
        }
    }

    event = service.events().insert(
        calendarId="primary",
        body=event
    ).execute()

    return event["id"]