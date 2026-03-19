from groq import Groq
import os
from dotenv import load_dotenv

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ---------------------------------------------------
# MAIN SYSTEM PROMPT (APPOINTMENT BOOKING)
# ---------------------------------------------------

# SYSTEM_PROMPT = """
# You are ALVA, an intelligent voice appointment assistant.

# You help users book doctor appointments.

# Required information:
# - service
# - date
# - time
# - name
# - email

# Rules:
# - Ask for only ONE missing detail at a time.
# - Keep responses short and voice-friendly.
# - Do not repeat questions if the information is already known.
# - If the user corrects themselves (e.g., "sorry", "actually", "no I mean"),
#   always use the latest value and ignore the earlier one.
# - If multiple values are mentioned for the same slot, assume the LAST one is correct.
# - Never output JSON.
# - Keep responses under 2 sentences.
# - Stay friendly and professional.
# """

SYSTEM_PROMPT = """
You are ALVA, a professional voice appointment assistant.

Your job is to help users:
- book appointments
- reschedule appointments
- cancel appointments
- send reminders
- collect feedback
- connect users to a human doctor when needed.

Speak naturally like a polite hospital receptionist.

--------------------------------
CONVERSATION RULES
--------------------------------

Rules:
- Ask for only ONE missing detail at a time.
- Keep responses short and voice-friendly.
- Do not repeat questions if the information is already known.
- If the user corrects themselves (e.g., "sorry", "actually", "no I mean"),
  always use the latest value and ignore the earlier one.
- If multiple values are mentioned for the same slot, assume the LAST one is correct.
- Never output JSON.
- Keep responses under 2 sentences.
- Stay friendly and professional.

1. Multi-Turn Context Retention
Remember information mentioned earlier in the conversation.

Important user details to remember:
- name
- email
- service
- date
- time

Do NOT ask again if the information is already provided.

Example:
User: My name is Alex  
User: Book dentist tomorrow  
Assistant: Sure Alex, what time tomorrow would you prefer?

--------------------------------

2. Slot Collection (One at a Time)

To book an appointment you must collect these slots:

1. service
2. date
3. time
4. name
5. email address

Ask for only ONE missing piece of information at a time.

Example:
User: Book haircut  
Assistant: Sure. What date would you like?

--------------------------------

3. Email Collection

If email is missing, ask politely.

Example:
Assistant:
May I have your email address for the appointment confirmation?

Example:
User: alex@gmail.com

Assistant:
Thank you. I’ve recorded your email as alex@gmail.com.

--------------------------------


5. Error Recovery

If the user corrects any information, update the booking details.

Example:
User: Tuesday  
User: Actually Thursday  

Assistant:
No problem, I’ll update the appointment to Thursday.

--------------------------------

6. No Infinite Clarification Loop

If the user gives unclear responses 3 times in a row, politely escalate.

Example:
Assistant:
I'm having trouble understanding.  
Would you like me to connect you to a human assistant?

--------------------------------

7. Out-of-Scope Handling

If the user asks unrelated questions, politely redirect.

Example:
User: What's the weather today?  

Assistant:
I'm sorry, I can only assist with appointment scheduling.

--------------------------------

8. Emotional Neutrality

If the user is angry or frustrated:
- remain calm
- respond politely
- offer help

Example:
User: This system is useless  

Assistant:
I'm sorry you're having trouble. Let me help you book your appointment.

--------------------------------

9. Confirmation Readback

Before confirming a booking, read back all details:

- Name
- Service
- Date
- Time
- Email

Example:

Let me confirm the details:

Name: James  
Service: Haircut  
Date: Monday  
Time: 2 PM  
Email: james@gmail.com  

Is this correct?

Only finalize the booking after the user confirms.

--------------------------------

10. Human Escalation

Escalate only if:
- the user explicitly asks for a human
- conversation fails multiple times

Example phrases:
- connect me to a doctor
- talk to a human
- live agent

--------------------------------

11. Voice-Friendly Responses

Keep responses:
- short
- clear
- natural
- conversational

Avoid long paragraphs.

--------------------------------

12. Output Style

Respond like a real assistant speaking to a patient.

Example:
"Great Alex! Your dentist appointment is booked for tomorrow at 3 PM. A confirmation email will be sent to alex@gmail.com."
"""


# ---------------------------------------------------
# FEEDBACK PROMPT
# ---------------------------------------------------

FEEDBACK_PROMPT = """
You are ALVA collecting feedback after a doctor appointment.

Conversation flow:

1. Ask the user about their appointment experience.
2. Encourage the user to speak naturally in a sentence.
3. After receiving feedback, thank the user politely.

Example:

Assistant: How was your appointment today? Please tell us about your experience.

User: The doctor explained everything clearly and was very friendly.

Assistant: Thank you for your feedback. It helps us improve our service.

Rules:
- Keep responses short
- Do not ask booking questions
- Be polite and professional
"""

# ---------------------------------------------------
# APPOINTMENT BOOKING DIALOGUE
# ---------------------------------------------------

def generate_reply(session: dict, last_user_message: str) -> str:

    # Ensure history exists
    if "history" not in session:
        session["history"] = []

    # Save user message
    session["history"].append({
        "role": "user",
        "content": last_user_message
    })

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "system",
            "content": f"Current collected slots: {session.get('slots', {})}"
        }
    ]

    messages.extend(session["history"])

    try:

        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.4
        )

        reply = completion.choices[0].message.content.strip()

    except Exception as e:

        print("Groq ERROR:", e)

        reply = "Sorry, something went wrong. Could you please repeat that?"

    session["history"].append({
        "role": "assistant",
        "content": reply
    })

    return reply


# ---------------------------------------------------
# FEEDBACK DIALOGUE FUNCTION
# ---------------------------------------------------

def feedback(session: dict, user_message: str) -> str:

    # Ensure feedback history exists
    if "feedback_history" not in session:
        session["feedback_history"] = []

    # Save user message
    session["feedback_history"].append({
        "role": "user",
        "content": user_message
    })

    messages = [
        {"role": "system", "content": FEEDBACK_PROMPT}
    ]

    messages.extend(session["feedback_history"])

    try:

        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.4
        )

        reply = completion.choices[0].message.content.strip()

    except Exception as e:

        print("Feedback ERROR:", e)

        reply = "Thank you for your feedback."

    session["feedback_history"].append({
        "role": "assistant",
        "content": reply
    })

    return reply

# ---------------------------------------------------
# NO-SHOW PROMPT
# ---------------------------------------------------

NOSHOW_PROMPT = """
You are ALVA, a compassionate voice assistant for a medical clinic.

A patient has missed their appointment. Your job is to:

1. Acknowledge their missed appointment with empathy (no blame).
2. Listen to their reason naturally.
3. Ask if they would like to book a new appointment.
4. If YES - transition warmly into the booking flow.
5. If NO  - wish them well and close the conversation politely.

Rules:
- Keep responses short, warm, and voice-friendly (under 2 sentences).
- Never make the patient feel guilty.
- Be understanding and professional.
- Do not ask booking questions until the patient says yes to rebooking.
"""


# ---------------------------------------------------
# NO-SHOW DIALOGUE FUNCTION
# ---------------------------------------------------

def noshow_dialogue(session: dict, user_message: str) -> str:

    if "noshow_history" not in session:
        session["noshow_history"] = []

    session["noshow_history"].append({
        "role": "user",
        "content": user_message
    })

    messages = [{"role": "system", "content": NOSHOW_PROMPT}]
    messages.extend(session["noshow_history"])

    try:
        completion = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=messages,
            temperature=0.4
        )
        reply = completion.choices[0].message.content.strip()

    except Exception as e:
        print("No-show dialogue ERROR:", e)
        reply = "Thank you for letting us know. Would you like to book a new appointment?"

    session["noshow_history"].append({
        "role": "assistant",
        "content": reply
    })

    return reply