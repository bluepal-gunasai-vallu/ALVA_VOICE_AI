from groq import Groq
import json
import os
from dotenv import load_dotenv
import re
from datetime import datetime, timedelta

load_dotenv()

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SYSTEM_PROMPT = """
You are an advanced NLU engine for a voice appointment assistant.

Your task:
1. Detect the user's intent.
2. Extract relevant structured entities from spoken or typed input.
3. Handle partial, ambiguous, conversational, or corrected inputs robustly.
4. Never hallucinate missing data.
5. If uncertain, return null for that field.

INTENTS (choose exactly one):
- schedule
- reschedule
- cancel
- confirm
- check_availability
- greeting
- unknown
- feedback
- human_help

ENTITIES TO EXTRACT:

date:
  Accept ANY natural-language date expression the user might say or type.
  This includes relative references ("tomorrow", "in 3 days", "next week"),
  named weekdays ("next Tuesday", "this Friday"), ordinal phrasing
  ("the fourteenth of March", "June 3rd"), standard formats ("March 5",
  "2026-03-18"), and informal wording ("a week from now", "end of the month").
  Return the date as the user expressed it; downstream code handles normalization.

time:
  Accept ANY natural-language time expression.
  This includes clock format ("3 PM", "15:00"), spoken-word ("two thirty PM",
  "fourteen thirty", "eleven o'clock"), colloquial ("half past two",
  "quarter to five", "noon", "midnight"), and contextual ("3 in the afternoon",
  "8 in the morning").
  Return a normalized time string when possible (e.g. "3:00 PM", "14:30").
  If ambiguous between AM/PM, infer from context or return as-is.

time_period:
  One of: morning, afternoon, evening, night.
  Only extract when the user gives a general period instead of a specific time.

service:
  The type of appointment or service requested.

name:
  The user's name if mentioned.

email:
  The user's email address if mentioned.
  Normalize spacing (e.g. "user @ gmail.com" becomes "user@gmail.com").

SELF-CORRECTION HANDLING:
Users frequently correct themselves mid-sentence.
- ALWAYS extract the FINAL / CORRECTED value for any entity and DISCARD earlier mentions.
- Corrections may be signaled explicitly (words like "actually", "sorry", "I mean", "wait",
  "change that", "make it", "not that") or implicitly by simply stating a new value for the
  same entity after an earlier one.
- General principle: when the same entity appears more than once, the LAST occurrence wins.

RULES:
- Return ONLY valid JSON. No explanations, no markdown, no extra text.
- If a value is missing or unclear, return null for that field.
- If input is unclear but suggests booking context, choose the most logical intent.
- Never fabricate information.
- Always return all keys in the output.

CONFIRM INTENT:
Classify as "confirm" when the user expresses agreement, approval, or readiness to proceed
with a booking, even without the word "confirm". Any affirmative response that signals
approval, acceptance, or permission to continue counts.

This includes acknowledgements (short or long) when the user is responding to a confirmation
question like "Is that correct?" or "Should I book it?".

Examples that MUST be classified as "confirm" in booking context:
- proceed / go ahead / continue / do it / book it
- yep / yeah / yes / ok / okay / sure / alright
- that’s right / correct / sounds good / looks good

HUMAN_HELP INTENT:
Classify as "human_help" when the user wants to be connected to a real person, whether a
doctor, agent, operator, receptionist, or any human. This covers any phrasing that requests
live human assistance, asks to be transferred, or expresses a desire to stop talking to the
automated system.

JSON FORMAT (strict):

{
  "intent": "",
  "date": "",
  "time": "",
  "time_period": "",
  "service": "",
  "name": "",
  "email": ""
}
"""

# ---------- RELATIVE DATE PARSER ----------
def normalize_relative_date(date_text):

    if not date_text:
        return None

    date_text = date_text.lower().strip()
    today = datetime.today()

    weekdays = {
        "monday":0,
        "tuesday":1,
        "wednesday":2,
        "thursday":3,
        "friday":4,
        "saturday":5,
        "sunday":6
    }

    # tomorrow / next day
    if date_text in ["tomorrow", "next day"]:
        return (today + timedelta(days=1)).strftime("%Y-%m-%d")

    # day after tomorrow
    if date_text == "day after tomorrow":
        return (today + timedelta(days=2)).strftime("%Y-%m-%d")

    # next 3 days
    if "next 3 days" in date_text:
        return (today + timedelta(days=3)).strftime("%Y-%m-%d")

    # next week
    if date_text == "next week":
        return (today + timedelta(days=7)).strftime("%Y-%m-%d")

    # next month
    if date_text == "next month":
        month = today.month + 1 if today.month < 12 else 1
        year = today.year if today.month < 12 else today.year + 1
        return datetime(year, month, today.day).strftime("%Y-%m-%d")

    # next weekend (Saturday)
    if "next weekend" in date_text:
        saturday = 5
        days_ahead = saturday - today.weekday()

        if days_ahead <= 0:
            days_ahead += 7

        return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # next weekday (next friday etc)
    for day in weekdays:
        if f"next {day}" in date_text:

            target = weekdays[day]
            days_ahead = target - today.weekday()

            if days_ahead <= 0:
                days_ahead += 7

            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # this weekday
    for day in weekdays:
        if f"this {day}" in date_text:

            target = weekdays[day]
            days_ahead = target - today.weekday()

            if days_ahead < 0:
                days_ahead += 7

            return (today + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    return date_text

def detect_time_regex(text: str):
    """Regex fallback for time extraction from raw text.
    Handles colloquial expressions and numeric patterns.
    When multiple numeric times appear, the last one wins (handles self-corrections)."""

    text = text.lower()

    # Colloquial: "half past <hour>", "quarter past <hour>", "quarter to <hour>"
    half_past = re.search(r'half\s+past\s+(\d{1,2})', text)
    if half_past:
        h = int(half_past.group(1))
        return f"{h:02d}:30"

    quarter_past = re.search(r'quarter\s+past\s+(\d{1,2})', text)
    if quarter_past:
        h = int(quarter_past.group(1))
        return f"{h:02d}:15"

    quarter_to = re.search(r'quarter\s+to\s+(\d{1,2})', text)
    if quarter_to:
        h = (int(quarter_to.group(1)) - 1) % 24
        return f"{h:02d}:45"

    # Numeric patterns: use findall and take the LAST match
    # so self-corrections resolve to the corrected value.
    pattern = r'(\d{1,2})(:\d{2})?\s*(am|pm|o\'?clock)?'
    matches = re.findall(pattern, text)

    if matches:
        hour_str, minute, suffix = matches[-1]
        hour = int(hour_str)
        minute = minute if minute else ":00"

        if suffix and "pm" in suffix and hour != 12:
            hour += 12

        if suffix and "am" in suffix and hour == 12:
            hour = 0

        return f"{hour:02d}{minute}"

    return None


def extract_nlu(text: str) -> dict:
    regex_time = detect_time_regex(text)
    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text}
            ],
            temperature=0,
            max_completion_tokens=300,
            top_p=1,
            stream=False
        )

        response_text = completion.choices[0].message.content.strip()

        # Parse JSON safely
        parsed = json.loads(response_text)

        # Ensure all required keys exist
        required_keys = [
            "intent",
            "date",
            "time",
            "time_period",
            "service",
            "name",
            "email"
        ]

        for key in required_keys:
            if key not in parsed:
                parsed[key] = None

        # ---------- FIX 1: REGEX TIME ----------
        if parsed["time"] is None and regex_time:
            parsed["time"] = regex_time

        # ---------- FIX 2: TIME PERIOD ----------
        if parsed["time"] is None and parsed.get("time_period"):

            period = parsed["time_period"].lower()

            if period == "morning":
                parsed["time"] = "09:00"
            elif period == "afternoon":
                parsed["time"] = "14:00"
            elif period == "evening":
                parsed["time"] = "17:00"
            elif period == "night":
                parsed["time"] = "19:00"

        # ---------- FIX 3: RELATIVE DATE ----------
        if parsed["date"]:
            parsed["date"] = normalize_relative_date(parsed["date"])

        return parsed
    


    except Exception as e:
        print("NLU ERROR:", e)

        # Safe fallback
        return {
            "intent": "unknown",
            "date": None,
            "time": None,
            "time_period": None,
            "service": None,
            "name": None,
            "email": None
        }
