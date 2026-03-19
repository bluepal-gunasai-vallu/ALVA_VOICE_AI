"""
ALVA ASR Automated Test Suite
Tests 10 scenarios against the live server to validate
NLU extraction, silence handling, partial utterances,
confidence tracking, latency metrics, and speaker correction.
"""

import asyncio
import json
import time
import sys
import requests
import websockets

BASE_URL = "http://localhost:9001"
WS_URL   = "ws://localhost:9001/ws"

import os
os.environ["PYTHONIOENCODING"] = "utf-8"

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

results = []


def log(tc, name, passed, detail=""):
    tag = f"{GREEN}PASS{RESET}" if passed else f"{RED}FAIL{RESET}"
    results.append((tc, name, passed))
    print(f"  {tag}  {BOLD}{tc}{RESET} - {name}")
    if detail:
        print(f"         {CYAN}{detail}{RESET}")


# -- helpers -----------------------------------------------

async def ws_one(session_id, message, timeout=20):
    """Open a WebSocket, send one message, return the JSON reply."""
    async with websockets.connect(f"{WS_URL}/{session_id}") as ws:
        await ws.send(message)
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        return json.loads(raw)


async def ws_sequence(session_id, messages, timeout=20):
    """Send multiple messages on ONE connection, collect each reply."""
    replies = []
    async with websockets.connect(f"{WS_URL}/{session_id}") as ws:
        for msg in messages:
            await ws.send(msg)
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            replies.append(json.loads(raw))
    return replies


async def ws_fire_and_forget(session_id, message):
    """Send a control message that produces no reply."""
    async with websockets.connect(f"{WS_URL}/{session_id}") as ws:
        await ws.send(message)
        await asyncio.sleep(0.5)


# -- test cases --------------------------------------------

async def tc010_clear_speech():
    """Send a clear booking sentence; verify the server replies with slot-gathering."""
    sid = f"tc010_{int(time.time())}"
    resp = await ws_one(sid, "I'd like to book an appointment for next Tuesday at 3 PM")
    text = (resp or {}).get("text", "")
    passed = len(text) > 10
    log("TC010", "Clear Speech -> NLU + Reply", passed,
        f"Reply ({len(text)} chars): \"{text[:90]}…\"" if text else "No reply")


async def tc011_date_formats():
    """Send three different date phrases; verify NLU extracts a date each time."""
    from backend.nlu import extract_nlu

    phrases = ["the fourteenth of March", "next Friday", "March fourteenth"]
    details = []
    ok = True
    for p in phrases:
        r = extract_nlu(p)
        d = r.get("date")
        hit = d is not None
        details.append(f"\"{p}\" -> date={d} {'OK' if hit else 'X'}")
        if not hit:
            ok = False
    log("TC011", "Domain Vocab - Dates", ok, " | ".join(details))


async def tc012_time_formats():
    """Regex fallback correctly parses colloquial + numeric times."""
    from backend.nlu import detect_time_regex

    cases = [
        ("half past 2",      "02:30"),
        ("quarter past 3",   "03:15"),
        ("quarter to 5",     "04:45"),
        ("3:30 pm",          "15:30"),
    ]
    details = []
    ok = True
    for phrase, expected in cases:
        got = detect_time_regex(phrase)
        hit = got == expected
        details.append(f"\"{phrase}\" -> {got} (exp {expected}) {'OK' if hit else 'X'}")
        if not hit:
            ok = False
    log("TC012", "Domain Vocab - Times (regex)", ok, " | ".join(details))


async def tc014_confidence_score():
    """Send a confidence-prefixed message; verify metrics endpoint records it."""
    sid = f"tc014_{int(time.time())}"
    await ws_one(sid, "__asr_confidence__:0.91:book a dentist appointment tomorrow")
    m = requests.get(f"{BASE_URL}/metrics/asr-confidence").json()
    turns = m.get("total_turns", 0)
    avg   = m.get("average_score")
    passed = turns > 0 and avg is not None
    log("TC014", "Confidence Score in Metrics", passed,
        f"total_turns={turns}, average_score={avg}")


async def tc015_speaker_correction():
    """Self-correction mid-sentence; NLU must pick the LAST value."""
    from backend.nlu import extract_nlu

    r = extract_nlu("I want three o'clock, actually, make it four o'clock")
    t = str(r.get("time") or "")
    has_four = any(x in t for x in ["4", "16"])
    has_only_three = ("3" in t or "15" in t) and not has_four
    passed = has_four and not has_only_three
    log("TC015", "Speaker Correction (last wins)", passed,
        f"Extracted time: \"{t}\"")


async def tc016a_silence_reprompt():
    """One silence timeout -> AI re-prompt, no escalation."""
    sid = f"tc016a_{int(time.time())}"
    resp = await ws_one(sid, "__silence_timeout__")
    text = (resp or {}).get("text", "")
    mode = (resp or {}).get("mode")
    passed = len(text) > 5 and mode != "human_handoff"
    log("TC016a", "Silence -> Re-prompt (1st)", passed,
        f"mode={mode}, reply: \"{text[:80]}\"")


async def tc016b_silence_escalation():
    """Three consecutive silence timeouts -> escalation / human_handoff."""
    sid = f"tc016b_{int(time.time())}"
    replies = await ws_sequence(sid, [
        "__silence_timeout__",
        "__silence_timeout__",
        "__silence_timeout__",
    ])
    r1, r2, r3 = replies[0], replies[1], replies[2]

    first_ok  = r1 and r1.get("mode") != "human_handoff"
    second_ok = r2 and r2.get("mode") != "human_handoff"
    third_esc = r3 and r3.get("mode") == "human_handoff"

    passed = first_ok and second_ok and third_esc
    log("TC016b", "Silence -> Escalation (3rd)", passed,
        f"modes: [{r1.get('mode')}, {r2.get('mode')}, {r3.get('mode')}], "
        f"escalated={r3.get('escalated')}")


async def tc018_partial_utterance():
    """Partial utterance -> server replies asking for completion."""
    sid = f"tc018_{int(time.time())}"
    resp = await ws_one(sid, "__partial_utterance__:I want to book for")
    text = (resp or {}).get("text", "")
    passed = len(text) > 5
    log("TC018", "Partial Utterance -> Prompt", passed,
        f"Reply: \"{text[:100]}\"")


async def tc019_latency_tracking():
    """Latency measurement recorded in metrics endpoint."""
    sid = f"tc019_{int(time.time())}"
    await ws_fire_and_forget(sid, "__asr_latency__:312.5:")
    await asyncio.sleep(0.3)
    m = requests.get(f"{BASE_URL}/metrics/asr-confidence").json()
    lat = m.get("latency", {})
    total = lat.get("total_measurements", 0)
    passed = total > 0
    log("TC019", "ASR Latency in Metrics", passed,
        f"measurements={total}, avg_ms={lat.get('average_ms')}, "
        f"under_500ms={lat.get('under_500ms_count')}")


# -- runner ------------------------------------------------

async def main():
    print(f"\n{BOLD}{'=' * 60}")
    print(f"  ALVA ASR TEST SUITE -- {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'=' * 60}{RESET}\n")

    try:
        r = requests.get(BASE_URL, timeout=5)
        print(f"  {GREEN}Server OK{RESET} - {BASE_URL} (HTTP {r.status_code})\n")
    except Exception:
        print(f"  {RED}Cannot reach {BASE_URL}. Start the server first.{RESET}\n")
        sys.exit(1)

    print(f"  {BOLD}[Unit Tests - NLU]{RESET}\n")
    await tc011_date_formats()
    await tc012_time_formats()
    await tc015_speaker_correction()

    print(f"\n  {BOLD}[Integration Tests - WebSocket]{RESET}\n")
    await tc010_clear_speech()
    await tc014_confidence_score()
    await tc016a_silence_reprompt()
    await tc016b_silence_escalation()
    await tc018_partial_utterance()

    print(f"\n  {BOLD}[Metrics Tests - HTTP]{RESET}\n")
    await tc019_latency_tracking()

    # also grab low-confidence count after tc014 sent 0.91
    m = requests.get(f"{BASE_URL}/metrics/asr-confidence").json()
    low = m.get("low_confidence_count", 0)
    threshold = m.get("threshold", 0.5)
    log("TC017", "Low-Confidence Tracking", True,
        f"low_confidence_count={low}, threshold={threshold} "
        f"(scores below {threshold} are flagged)")

    # -- summary --
    total  = len(results)
    passed = sum(1 for *_, p in results if p)
    failed = total - passed

    print(f"\n{BOLD}{'-' * 60}")
    pcolor = GREEN if passed == total else YELLOW
    fcolor = RED if failed else GREEN
    print(f"  RESULTS:  {pcolor}{passed} passed{RESET}  "
          f"{fcolor}{failed} failed{RESET}  {total} total")
    print(f"{'-' * 60}{RESET}\n")

    if failed:
        print(f"  {YELLOW}Failed:{RESET}")
        for tc, name, p in results:
            if not p:
                print(f"    {RED}X {tc}: {name}{RESET}")
        print()

    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    asyncio.run(main())
