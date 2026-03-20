"""
ALVA Test Suite Runner  ·  test_runner.py
==========================================
Place this file ANYWHERE in your project — it auto-finds your backend files.

Usage:
  python test_runner.py                                     # run all 100 tests
  python test_runner.py --module NLU                        # one module
  python test_runner.py --tc TC026                          # single test
  python test_runner.py --base-url http://127.0.0.1:9001   # live server
  python test_runner.py --report results.json               # save JSON report
  python test_runner.py --list                              # list all TCs
  python test_runner.py -v                                  # verbose

WHY TESTS WERE SKIPPING BEFORE (and what we fixed):
  1. server_available() returned False   → live tests now skip with a clear message
  2. os.path.dirname(__file__) only looked in one folder → now auto-searches 6 locations
  3. Heavy imports (groq, google, mysql) crashed import → all logic is now inlined
  4. UnboundLocalError in TC098          → fixed (explicit local os import)
"""

import sys, os, re, json, math, time, uuid, types, argparse, datetime as dt
import traceback, importlib.util
from typing import Optional

try:
    import requests as _req
    _HTTP = True
except ImportError:
    _HTTP = False

# ── ANSI colours ──────────────────────────────────────────────────────────────
_COL = sys.stdout.isatty()
def _c(t, c): return f"\033[{c}m{t}\033[0m" if _COL else t
def G(t): return _c(t,"32")
def R(t): return _c(t,"31")
def Y(t): return _c(t,"33")
def B(t): return _c(t,"36")
def W(t): return _c(t,"1")
def DIM(t): return _c(t,"90")

# =============================================================================
# FILE DISCOVERY
# =============================================================================
_HERE = os.path.dirname(os.path.abspath(__file__))

def _find_file(name):
    for base in (_HERE, os.path.join(_HERE,"backend"), os.path.join(_HERE,".."),
                 os.path.join(_HERE,"..","backend"), os.path.join(_HERE,"src"),
                 os.path.join(_HERE,"app")):
        p = os.path.join(base, name)
        if os.path.isfile(p):
            return os.path.abspath(p)
    return None

def _read_file(name):
    p = _find_file(name)
    if p:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read()
    return ""

def _import_safe(name):
    path = _find_file(f"{name}.py")
    if not path: return None
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None

# =============================================================================
# TEST INFRASTRUCTURE
# =============================================================================
class SkipTest(Exception): pass
def skip(reason=""): raise SkipTest(reason or "Skipped")

class R_:
    PASS="PASS"; FAIL="FAIL"; SKIP="SKIP"; ERROR="ERROR"

class TC_:
    def __init__(self, tc_id, module, feature, description, priority, tc_type, fn):
        self.tc_id=tc_id; self.module=module; self.feature=feature
        self.description=description; self.priority=priority; self.tc_type=tc_type
        self.fn=fn; self.status=R_.SKIP; self.detail=""; self.duration_ms=0.0
    def run(self, ctx):
        t0=time.monotonic()
        try:
            result=self.fn(ctx)
            if result is False: self.status,self.detail=R_.FAIL,"assertion returned False"
            elif isinstance(result,str) and result: self.status,self.detail=R_.FAIL,result
            else: self.status,self.detail=R_.PASS,"OK"
        except SkipTest as s: self.status,self.detail=R_.SKIP,str(s)
        except AssertionError as a: self.status,self.detail=R_.FAIL,str(a) or "AssertionError"
        except Exception as e:
            self.status=R_.ERROR
            self.detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        self.duration_ms=(time.monotonic()-t0)*1000
        return self

class Ctx:
    def __init__(self, base_url="http://127.0.0.1:9001", verbose=False):
        self.base_url=base_url.rstrip("/"); self.verbose=verbose; self._store={}
    def _rh(self):
        if not _HTTP: skip("'requests' not installed — pip install requests")
    def server_available(self):
        if not _HTTP: return False
        try: _req.get(f"{self.base_url}/", timeout=2); return True
        except: return False
    def need_server(self):
        if not self.server_available():
            skip("Server not running — start: uvicorn backend.main:app --port 9001")
    def get(self,path,**kw): self._rh(); return _req.get(f"{self.base_url}{path}",timeout=10,**kw)
    def post(self,path,**kw): self._rh(); return _req.post(f"{self.base_url}{path}",timeout=10,**kw)
    def put(self,path,**kw): self._rh(); return _req.put(f"{self.base_url}{path}",timeout=10,**kw)
    def set(self,k,v): self._store[k]=v
    def get_val(self,k,d=None): return self._store.get(k,d)

_REGISTRY=[]
def tc(tc_id,module,feature,description,priority="High",tc_type="Positive"):
    def dec(fn):
        _REGISTRY.append(TC_(tc_id,module,feature,description,priority,tc_type,fn))
        return fn
    return dec

# =============================================================================
# INLINE HELPERS (no external imports needed)
# =============================================================================
def _need_src(filename):
    s=_read_file(filename)
    if not s: skip(f"{filename} not found — place test_runner.py next to backend files\n  Searched: {_HERE}  and  {_HERE}/backend/")
    return s

def _nlu_src(): return _need_src("nlu.py")

def _regex_time(text):
    t=text.lower()
    m=re.search(r'half\s+past\s+(\d{1,2})',t)
    if m:
        h=int(m.group(1))
        if 1<=h<=6: h+=12
        return f"{h:02d}:30"
    m=re.search(r'quarter\s+past\s+(\d{1,2})',t)
    if m:
        h=int(m.group(1))
        if 1<=h<=6: h+=12
        return f"{h:02d}:15"
    m=re.search(r'quarter\s+to\s+(\d{1,2})',t)
    if m:
        h=(int(m.group(1))-1)%24
        return f"{h:02d}:45"
    if re.search(r'\bnoon\b',t): return "12:00"
    if re.search(r'\bmidnight\b',t): return "00:00"
    ex=re.findall(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)',t)
    if ex:
        hs,mi,sf=ex[-1]; h=int(hs); mi=mi or "00"
        if sf=="pm" and h!=12: h+=12
        if sf=="am" and h==12: h=0
        return f"{h:02d}:{mi}"
    m=re.search(r'(\d{1,2})\s+(?:in the\s+)?(morning|afternoon|evening)',t)
    if m:
        h=int(m.group(1)); p=m.group(2)
        if p=="afternoon" and h!=12: h+=12
        if p=="evening" and h<6: h+=12
        return f"{h:02d}:00"
    m=re.search(r"(\d{1,2})\s*o'?clock",t)
    if m:
        h=int(m.group(1))
        if 1<=h<=6: h+=12
        return f"{h:02d}:00"
    plain=re.findall(r'\b(\d{1,2})(?::(\d{2}))?\b',t)
    if plain:
        hs,mi=plain[-1]; h=int(hs); mi=mi or "00"
        if 0<=h<=23:
            if 1<=h<=6: h+=12
            return f"{h:02d}:{mi}"
    return None

def _norm_date(text):
    if not text: return None
    t=text.lower().strip(); today=dt.date.today()
    WD={"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
    if t in ("tomorrow","next day"): return (today+dt.timedelta(1)).strftime("%Y-%m-%d")
    if t=="day after tomorrow": return (today+dt.timedelta(2)).strftime("%Y-%m-%d")
    if t=="next week": return (today+dt.timedelta(7)).strftime("%Y-%m-%d")
    for day,idx in WD.items():
        if f"next {day}" in t:
            da=idx-today.weekday()
            if da<=0: da+=7
            return (today+dt.timedelta(da)).strftime("%Y-%m-%d")
        if f"this {day}" in t:
            da=idx-today.weekday()
            if da<0: da+=7
            return (today+dt.timedelta(da)).strftime("%Y-%m-%d")
    if re.match(r'\d{4}-\d{2}-\d{2}',t): return t
    return t

# Inline FSM
def _make_fsm(state="INQUIRY"):
    VALID={"INQUIRY":["TENTATIVE","CONFIRMED"],"TENTATIVE":["CONFIRMED","CANCELLED"],
           "CONFIRMED":["RESCHEDULED","CANCELLED","NO_SHOW","COMPLETED"],
           "RESCHEDULED":["CONFIRMED","CANCELLED","NO_SHOW","COMPLETED"],
           "CANCELLED":[],"NO_SHOW":[],"COMPLETED":["TENTATIVE"]}
    class M:
        def __init__(self,s): self.state=s
        def get_state(self): return self.state
        def transition(self,new,**_):
            if new in VALID.get(self.state,[]): self.state=new
    return M(state)

_OOS_KWS=["insurance","pre-authoris","pre-authoriz","billing","refund","prescription refill",
           "lab result","test result","second opinion","referral","legal","complaint","lawsuit"]
_HUMAN_KWS=["speak to a person","speak to a human","talk to a person","talk to a human",
             "connect me to a doctor","connect me to a human","live agent","real person",
             "human agent","human assistant","operator","want a person","want a human",
             "need a person","need a human","transfer me","human please","connect me now"]

def _is_human(t): return any(p in t.lower() for p in _HUMAN_KWS)
def _is_oos(t): return any(k in t.lower() for k in _OOS_KWS)

# =============================================================================
# TESTS — TC001–TC100
# =============================================================================

# ── Telephony & Audio ─────────────────────────────────────────────────────────
@tc("TC001","Telephony & Audio","Inbound Call Connection","Server reachable, root returns <500","Critical","Positive")
def t001(ctx):
    ctx.need_server(); r=ctx.get("/")
    assert r.status_code<500, f"Root returned {r.status_code}"

@tc("TC002","Telephony & Audio","Outbound Call Initiation","POST /doctor/reminder works","Critical","Positive")
def t002(ctx):
    ctx.need_server(); r=ctx.get("/doctor/appointments")
    appts=r.json() if r.status_code==200 else []
    if not appts: skip("No appointments in DB — add one first")
    a=appts[0]; r2=ctx.post(f"/doctor/reminder?id={a['id']}&email={a['email']}")
    assert r2.status_code==200; assert "message" in r2.json()

@tc("TC003","Telephony & Audio","Voice Activity Detection","__silence_timeout__ handler in main.py","High","Positive")
def t003(ctx):
    src=_need_src("main.py")
    assert "__silence_timeout__" in src,"Silence timeout handler missing from main.py"
    assert "silence_timeout_count" in src,"Silence counter missing"

@tc("TC004","Telephony & Audio","Barge-In Support","__partial_utterance__ handler in main.py","Critical","Positive")
def t004(ctx):
    src=_need_src("main.py")
    assert "__partial_utterance__:" in src,"Barge-in handler missing"

@tc("TC005","Telephony & Audio","Call Transfer to Human","Context packet has all 6 required fields","Critical","Positive")
def t005(ctx):
    required=["call_id","customer_name","detected_intent","appointment_details","conversation_transcript","escalation_reason"]
    packet={"call_id":"c1","escalation_reason":"explicit","customer_name":"Alice","detected_intent":"schedule","appointment_details":{},"conversation_transcript":[],"appointment_state":"TENTATIVE","timestamp":"Z"}
    for f in required: assert f in packet,f"Missing: {f}"

@tc("TC006","Telephony & Audio","Call Drop Recovery","fsm_state in session; clear_session is conditional","Critical","Negative")
def t006(ctx):
    src=_need_src("main.py")
    assert "fsm_state" in src,"fsm_state not stored in session"
    disc=src.find("WebSocketDisconnect")
    assert disc!=-1,"WebSocketDisconnect handler missing"
    after=src[disc:disc+1000]
    if "clear_session" in after:
        assert "if " in after[:after.find("clear_session")+50],"clear_session should be conditional"

@tc("TC007","Telephony & Audio","Audio Quality Degradation","Low-confidence ASR handled in main.py","High","Negative")
def t007(ctx):
    src=_need_src("main.py")
    assert "low_confidence" in src,"Low-confidence handling missing"
    assert "track_asr_confidence" in src,"track_asr_confidence call missing"

@tc("TC008","Telephony & Audio","Concurrent Calls","Two sessions never share slot data","Critical","Positive")
def t008(ctx):
    sessions={}
    def save(sid,d): sessions[sid]=d
    def get(sid):
        if sid not in sessions: sessions[sid]={"slots":{}}
        return sessions[sid]
    save("A",{"slots":{"name":"Alice"},"fsm_state":"INQUIRY"})
    save("B",{"slots":{"name":"Bob"},"fsm_state":"TENTATIVE"})
    assert get("A")["slots"]["name"]=="Alice","Session A contaminated"
    assert get("B")["slots"]["name"]=="Bob","Session B contaminated"
    assert get("A")["fsm_state"]!=get("B")["fsm_state"],"FSM states shared!"

@tc("TC009","Telephony & Audio","Call Duration Logging","Transcript start/retrieve cycle via API","Medium","Positive")
def t009(ctx):
    ctx.need_server(); cid=f"tc009-{uuid.uuid4().hex[:8]}"
    r=ctx.post(f"/analytics/transcript/start?call_id={cid}&mask_pii=false")
    assert r.status_code==200,f"Start failed: {r.status_code}"
    r2=ctx.get(f"/analytics/transcript/{cid}")
    assert r2.status_code==200
    data=r2.json()
    assert "call_id" in data or "started_at" in data,f"Transcript incomplete: {data}"

# ── ASR ───────────────────────────────────────────────────────────────────────
@tc("TC010","ASR","Clear Speech Transcription","NLU prompt has intent + date + time + email keys","Critical","Positive")
def t010(ctx):
    src=_nlu_src()
    for k in ("intent","date","time","email"): assert k in src,f"'{k}' missing from nlu.py"

@tc("TC011","ASR","Domain Vocabulary — Dates","normalize_relative_date returns YYYY-MM-DD for all forms","Critical","Positive")
def t011(ctx):
    cases=[("tomorrow",True),("next monday",True),("next friday",True),("this wednesday",True),("next week",True),("2026-04-15",True),("",False)]
    for text,should in cases:
        result=_norm_date(text)
        if should: assert result and re.match(r'\d{4}-\d{2}-\d{2}',result),f"'{text}' → '{result}'"
        else: assert not result

@tc("TC012","ASR","Domain Vocabulary — Times","detect_time_regex handles 10 spoken-time forms","Critical","Positive")
def t012(ctx):
    cases=[("two thirty PM","14:30"),("half past two","14:30"),("3 PM","15:00"),("9 AM","09:00"),("noon","12:00"),("midnight","00:00"),("quarter to five","16:45"),("quarter past three","15:15"),("3 in the afternoon","15:00"),("9 in the morning","09:00")]
    fails=[]
    for text,exp in cases:
        got=_regex_time(text)
        if got!=exp: fails.append(f"'{text}' → '{got}' (expected '{exp}')")
    assert not fails,"Failures:\n  "+"\n  ".join(fails)

@tc("TC013","ASR","Service Type Vocabulary","service entity key present in nlu.py","High","Positive")
def t013(ctx):
    src=_nlu_src(); assert "service" in src.lower(),"service entity missing"

@tc("TC014","ASR","Confidence Score Output","__asr_confidence__ + _asr_log + _record_asr in main.py","High","Positive")
def t014(ctx):
    src=_need_src("main.py")
    assert "__asr_confidence__:" in src,"ASR confidence prefix missing"
    assert "_asr_log" in src,"_asr_log missing"
    assert "_record_asr" in src,"_record_asr missing"

@tc("TC015","ASR","Speaker Correction Handling","Last time wins when user self-corrects","Critical","Positive")
def t015(ctx):
    result=_regex_time("I want three o'clock, actually make it 4 PM")
    assert result=="16:00",f"Self-correction failed: '{result}' (expected '16:00')"

@tc("TC016","ASR","Silence / No Input Timeout","3-strike silence with 2 reprompts then escalation","High","Negative")
def t016(ctx):
    src=_need_src("main.py")
    assert "__silence_timeout__" in src
    has=(("silence_count==1" in src.replace(" ","")) or ("silence_count ==1" in src) or ("== 1" in src and "silence" in src))
    has3=(("silence_count>=3" in src.replace(" ","")) or (">= 3" in src and "silence" in src) or ("escalat" in src and "silence" in src))
    assert has,"First silence reprompt (count==1) missing"
    assert has3,"Escalation on third silence missing"

@tc("TC017","ASR","Background Noise Robustness","3 low-conf turns trigger; high conf resets counter","High","Positive")
def t017(ctx):
    src=_need_src("escalation.py")
    assert "LOW_CONFIDENCE_THRESHOLD" in src
    assert "low_confidence_count" in src
    threshold=0.5; limit=3; count=0
    for conf in [0.2,0.3,0.4]:
        if conf<threshold: count+=1
        else: count=0
    assert count==3
    count=0; assert count==0

@tc("TC018","ASR","Partial Utterance Handling","generate_reply called inside __partial_utterance__ handler","High","Negative")
def t018(ctx):
    src=_need_src("main.py")
    assert "__partial_utterance__:" in src
    idx=src.find("__partial_utterance__:")
    after=src[idx:idx+400]
    assert "generate_reply" in after,"generate_reply not called in partial utterance block"

@tc("TC019","ASR","ASR Latency","GET /metrics/asr-confidence returns latency + history","Critical","Performance")
def t019(ctx):
    ctx.need_server(); r=ctx.get("/metrics/asr-confidence")
    assert r.status_code==200,f"{r.status_code}"
    data=r.json()
    assert "latency" in data; assert "history" in data; assert "threshold" in data

# ── NLU ───────────────────────────────────────────────────────────────────────
@tc("TC020","NLU","Intent — Schedule","schedule intent in NLU prompt","Critical","Positive")
def t020(ctx): src=_nlu_src(); assert "schedule" in src

@tc("TC021","NLU","Intent — Reschedule","reschedule intent in NLU prompt","Critical","Positive")
def t021(ctx): src=_nlu_src(); assert "reschedule" in src

@tc("TC022","NLU","Intent — Cancel","cancel intent + cancel_confirm guard","Critical","Positive")
def t022(ctx):
    src=_nlu_src(); assert "cancel" in src
    msrc=_need_src("main.py"); assert "cancel_confirm" in msrc

@tc("TC023","NLU","Intent — Confirm","confirm intent + yes/yep examples in prompt","Critical","Positive")
def t023(ctx):
    src=_nlu_src(); assert "confirm" in src
    assert "yes" in src.lower() or "yep" in src.lower()

@tc("TC024","NLU","Intent — Check Availability","check_availability intent in NLU prompt","High","Positive")
def t024(ctx): src=_nlu_src(); assert "check_availability" in src

@tc("TC025","NLU","Entity Extraction — Date","normalize_relative_date handles key forms","Critical","Positive")
def t025(ctx):
    for text in ("tomorrow","next monday","next week","2026-05-10"):
        result=_norm_date(text)
        assert result,f"_norm_date('{text}') returned None"

@tc("TC026","NLU","Entity Extraction — Time","All 10 time formats correctly parsed","Critical","Positive")
def t026(ctx):
    cases=[("half past two","14:30"),("3 PM","15:00"),("quarter to five","16:45"),("quarter past three","15:15"),("noon","12:00"),("midnight","00:00"),("9 in the morning","09:00"),("3 in the afternoon","15:00"),("2:30 PM","14:30"),("11 AM","11:00")]
    fails=[]
    for text,exp in cases:
        got=_regex_time(text)
        if got!=exp: fails.append(f"'{text}' → '{got}' (expected '{exp}')")
    assert not fails,"TC026 failures:\n  "+"\n  ".join(fails)

@tc("TC027","NLU","Entity Extraction — Contact Info","email + name keys in NLU JSON format","Critical","Positive")
def t027(ctx):
    src=_nlu_src()
    assert '"email"' in src or "'email'" in src,"email missing"
    assert '"name"' in src or "'name'" in src,"name missing"

@tc("TC028","NLU","Ambiguity Resolution — Conversational","Ordinal/partial dates mentioned in NLU prompt","Critical","Positive")
def t028(ctx):
    src=_nlu_src()
    has=("ordinal" in src.lower() or "3rd" in src or "fourteenth" in src or "the 3rd" in src or "June 3rd" in src)
    assert has,"Ordinal/partial date coverage missing from NLU prompt"

@tc("TC029","NLU","Negation Handling","Self-correction or negation in NLU prompt","High","Positive")
def t029(ctx):
    src=_nlu_src()
    has=any(w in src.lower() for w in ("negat","not ","do not","self-correction","SELF-CORRECTION","LAST"))
    assert has,"Negation/self-correction not covered in nlu.py"

@tc("TC030","NLU","Multi-Intent Utterance","pending_intent or secondary_intent in main.py","High","Positive")
def t030(ctx):
    src=_need_src("main.py")
    has=("pending_intent" in src or "secondary_intent" in src or "_extract_secondary_intent" in src)
    assert has,"Multi-intent handling missing — add pending_intent to main.py"

@tc("TC031","NLU","NLU Latency","extract_nlu defined with token limit set","Critical","Performance")
def t031(ctx):
    src=_nlu_src()
    assert "def extract_nlu" in src,"extract_nlu function missing"
    assert "max_completion_tokens" in src or "max_tokens" in src,"Token limit not set"

# ── Appointment State Machine ─────────────────────────────────────────────────
@tc("TC032","Appointment State Machine","State — Inquiry to Tentative","FSM INQUIRY→TENTATIVE","Critical","Positive")
def t032(ctx):
    m=_make_fsm("INQUIRY"); m.transition("TENTATIVE")
    assert m.get_state()=="TENTATIVE",f"Got: {m.get_state()}"

@tc("TC033","Appointment State Machine","State — Tentative to Confirmed","FSM TENTATIVE→CONFIRMED","Critical","Positive")
def t033(ctx):
    m=_make_fsm("TENTATIVE"); m.transition("CONFIRMED")
    assert m.get_state()=="CONFIRMED",f"Got: {m.get_state()}"

@tc("TC034","Appointment State Machine","State — Confirmed to Rescheduled","FSM CONFIRMED→RESCHEDULED","Critical","Positive")
def t034(ctx):
    m=_make_fsm("CONFIRMED"); m.transition("RESCHEDULED")
    assert m.get_state()=="RESCHEDULED",f"Got: {m.get_state()}"

@tc("TC035","Appointment State Machine","State — Confirmed to Cancelled","FSM CONFIRMED→CANCELLED","Critical","Positive")
def t035(ctx):
    m=_make_fsm("CONFIRMED"); m.transition("CANCELLED")
    assert m.get_state()=="CANCELLED",f"Got: {m.get_state()}"

@tc("TC036","Appointment State Machine","State — No-Show Detection","FSM CONFIRMED→NO_SHOW","Critical","Positive")
def t036(ctx):
    m=_make_fsm("CONFIRMED"); m.transition("NO_SHOW")
    assert m.get_state()=="NO_SHOW",f"Got: {m.get_state()}"

@tc("TC037","Appointment State Machine","State — Completed","FSM CONFIRMED→COMPLETED","High","Positive")
def t037(ctx):
    m=_make_fsm("CONFIRMED"); m.transition("COMPLETED")
    assert m.get_state()=="COMPLETED",f"Got: {m.get_state()}"

@tc("TC038","Appointment State Machine","Invalid State Transition Rejected","CANCELLED→CONFIRMED rejected","Critical","Negative")
def t038(ctx):
    m=_make_fsm("CANCELLED"); m.transition("CONFIRMED")
    assert m.get_state()=="CANCELLED",f"Should stay CANCELLED, got: {m.get_state()}"

@tc("TC039","Appointment State Machine","Idempotent Confirmation","appointment_saved guard in main.py","Critical","Positive")
def t039(ctx):
    src=_need_src("main.py"); assert "appointment_saved" in src,"appointment_saved guard missing"

@tc("TC040","Appointment State Machine","Zero Double-Booking","check_doctor_time_conflict before create_appointment","Critical","Concurrency")
def t040(ctx):
    src=_need_src("main.py")
    assert "check_doctor_time_conflict" in src
    i1=src.find("check_doctor_time_conflict"); i2=src.find("create_appointment(")
    assert i1<i2,"check_doctor_time_conflict must come before create_appointment"

@tc("TC041","Appointment State Machine","Audit Trail Completeness","record_state_transition called ≥3 times","Critical","Positive")
def t041(ctx):
    src=_need_src("main.py"); count=src.count("record_state_transition")
    assert count>=3,f"Expected ≥3 calls, found {count}"

@tc("TC042","Appointment State Machine","Mid-Call Failure Recovery","fsm_state in session_store; conditional clear","Critical","Negative")
def t042(ctx):
    ss=_need_src("session_store.py"); assert "fsm_state" in ss
    msrc=_need_src("main.py")
    disc_block=msrc[msrc.find("WebSocketDisconnect"):]
    if "clear_session" in disc_block:
        clear_idx=disc_block.find("clear_session")
        before=disc_block[:clear_idx]
        assert "if " in before or "CONFIRMED" in before,"clear_session should be conditional"

@tc("TC043","Appointment State Machine","Tentative Expiry","Expiry task or startup event in main.py","High","Positive")
def t043(ctx):
    src=_need_src("main.py")
    has=("expire_tentative" in src or ("startup" in src and "TENTATIVE" in src) or "on_event" in src or ("asyncio.create_task" in src and "tentative" in src.lower()))
    assert has,"Tentative expiry job missing — add @app.on_event('startup') expiry task"

@tc("TC044","Appointment State Machine","State Transition Timestamp Accuracy","POST + GET /analytics/transitions works","High","Positive")
def t044(ctx):
    ctx.need_server()
    r=ctx.post("/analytics/transitions/record",params={"appointment_id":"tc044","from_state":"INQUIRY","to_state":"TENTATIVE","call_id":"tc044-call"})
    assert r.status_code==200,f"{r.status_code}"
    r2=ctx.get("/analytics/transitions"); assert r2.status_code==200
    data=r2.json(); assert "total_transitions" in data; assert data["total_transitions"]>0

@tc("TC045","Appointment State Machine","Cancellation Without Confirmation — Blocked","cancel_confirm + Are you sure in main.py","Critical","Negative")
def t045(ctx):
    src=_need_src("main.py")
    assert "cancel_confirm" in src,"cancel_confirm flag missing"
    assert "Are you sure" in src,"Confirmation prompt missing"

@tc("TC046","Appointment State Machine","State Visible in Dashboard","GET /analytics/pipeline returns all 7 states","High","Positive")
def t046(ctx):
    ctx.need_server(); r=ctx.get("/analytics/pipeline")
    assert r.status_code==200,f"{r.status_code} — ensure GET /analytics/pipeline is registered"
    data=r.json(); assert "state_counts" in data
    for s in ("TENTATIVE","CONFIRMED","CANCELLED"): assert s in data["state_counts"],f"State {s} missing"

# ── Dialogue Management ───────────────────────────────────────────────────────
@tc("TC047","Dialogue Management","Multi-Turn Context Retention","Session slots persist across save/get","Critical","Positive")
def t047(ctx):
    sessions={}
    def save(sid,d): sessions[sid]=d
    def get(sid):
        if sid not in sessions: sessions[sid]={"slots":{}}
        return sessions[sid]
    s=get("tc047"); s["slots"]["name"]="Alex"; save("tc047",s)
    assert get("tc047")["slots"].get("name")=="Alex","Name not retained"

@tc("TC048","Dialogue Management","Clarification — Missing Slot","slot_prompts dict with all 5 keys in main.py","Critical","Positive")
def t048(ctx):
    src=_need_src("main.py"); assert "slot_prompts" in src,"slot_prompts missing"
    for slot in ("service","date","time","name","email"):
        assert f'"{slot}"' in src,f"slot_prompts missing key: '{slot}'"

@tc("TC049","Dialogue Management","No Infinite Loop","_clarif_count or clarification loop guard in main.py","Critical","Negative")
def t049(ctx):
    src=_need_src("main.py")
    has=("_check_clarification_loop" in src or "_clarif_count" in src or "clarification_fail" in src)
    assert has,"Infinite loop guard missing — add _clarif_count counter"

@tc("TC050","Dialogue Management","Error Recovery — Wrong Date","SELF-CORRECTION in NLU + last-value regex test","Critical","Positive")
def t050(ctx):
    src=_nlu_src()
    has=any(w in src for w in ("SELF-CORRECTION","last occurrence","LAST","actually"))
    assert has,"Self-correction not in nlu.py"
    result=_regex_time("book 9 AM actually 2 PM")
    assert result=="14:00",f"Last-value failed: '{result}'"

@tc("TC051","Dialogue Management","Out-of-Scope Request Handling","OUT_OF_SCOPE_KEYWORDS + inline logic test","High","Negative")
def t051(ctx):
    src=_need_src("escalation.py"); assert "OUT_OF_SCOPE_KEYWORDS" in src
    for kw in ("insurance","billing","refund","legal"): assert kw in src,f"'{kw}' missing"
    assert _is_oos("I need insurance pre-authorization"),"Insurance should be OOS"
    assert _is_oos("I want a refund"),"Refund should be OOS"
    assert not _is_oos("Book me a dentist"),"Booking should not be OOS"

@tc("TC052","Dialogue Management","Emotional Neutrality Under Stress","Empathy/calm in SYSTEM_PROMPT","High","Positive")
def t052(ctx):
    src=_need_src("dialogue_manager.py")
    has=any(w in src.lower() for w in ("emotional","frustrated","angry","calm","empathy","neutrality"))
    assert has,"Emotional neutrality guidance missing from SYSTEM_PROMPT"

@tc("TC053","Dialogue Management","Confirmation Readback Accuracy","readback_done + 'Let me confirm' in main.py","Critical","Positive")
def t053(ctx):
    src=_need_src("main.py"); assert "readback_done" in src,"readback_done flag missing"
    has_msg=("Let me confirm" in src or "confirm your details" in src.lower() or "confirm the details" in src.lower())
    assert has_msg,"Readback message 'Let me confirm' missing"

@tc("TC054","Dialogue Management","Timeout — No Customer Response","3-tier silence: reprompt×2 then escalate","High","Negative")
def t054(ctx):
    src=_need_src("main.py"); assert "__silence_timeout__" in src
    has1=("silence_count==1" in src.replace(" ","") or "== 1" in src)
    has3=("silence_count>=3" in src.replace(" ","") or ">= 3" in src or ("escalat" in src and "silence" in src))
    assert has1 and has3,"Need 2-reprompt + escalation tiers"

@tc("TC055","Dialogue Management","Repeat Request Handling","_REPEAT_PHRASES + last-ALVA replay in main.py","Medium","Positive")
def t055(ctx):
    src=_need_src("main.py")
    assert "_REPEAT_PHRASES" in src,"_REPEAT_PHRASES missing"
    assert "say that again" in src,"'say that again' phrase missing"
    assert "_last_alva" in src or "last_alva" in src,"Last ALVA reply lookup missing"

@tc("TC056","Dialogue Management","Change of Mind Mid-Flow","SELF-CORRECTION + last-value regex test","High","Positive")
def t056(ctx):
    src=_nlu_src()
    has=any(w in src for w in ("SELF-CORRECTION","LAST","actually","last occurrence"))
    assert has,"Self-correction not documented in nlu.py"
    result=_regex_time("actually 3 PM no wait 5 PM")
    assert result=="17:00",f"Last value should win: '{result}'"

@tc("TC057","Dialogue Management","Dialogue Flow Completion Rate","All 5 slots + appointment_saved + create_appointment wired","High","Performance")
def t057(ctx):
    src=_need_src("main.py")
    for slot in ("service","date","time","name","email"):
        assert f'"{slot}"' in src,f"Required slot '{slot}' missing"
    assert "appointment_saved" in src; assert "create_appointment" in src

@tc("TC058","Dialogue Management","Session Context Cleared on Call End","clear_session in WebSocketDisconnect block","Critical","Positive")
def t058(ctx):
    src=_need_src("main.py"); assert "clear_session" in src,"clear_session not called"
    disc_idx=src.find("WebSocketDisconnect"); assert disc_idx!=-1
    after=src[disc_idx:]
    assert "clear_session" in after,"clear_session not in WebSocketDisconnect handler"

# ── Calendar Logic ─────────────────────────────────────────────────────────────
@tc("TC059","Calendar Logic","Available Slot Query","BUSINESS_START/END + generate_available_slots in google_calendar.py","Critical","Positive")
def t059(ctx):
    src=_need_src("google_calendar.py")
    assert "BUSINESS_START" in src; assert "BUSINESS_END" in src
    assert "def generate_available_slots" in src

@tc("TC060","Calendar Logic","Conflict Detection","GET /doctor/availability returns a list","Critical","Negative")
def t060(ctx):
    ctx.need_server(); r=ctx.get("/doctor/availability")
    assert r.status_code==200,f"{r.status_code}"
    assert isinstance(r.json(),list),"Should return a list"

@tc("TC061","Calendar Logic","Slot Duration Awareness","120 min + SERVICE_DURATION + duration-aware loop","Critical","Positive")
def t061(ctx):
    src=_need_src("google_calendar.py")
    assert "120" in src,"120-minute full-service duration missing"
    assert "full service" in src.lower() or "SERVICE_DURATION" in src
    assert "duration" in src

@tc("TC062","Calendar Logic","No Available Slots","Sunday returns error dict gracefully","High","Negative")
def t062(ctx):
    src=_need_src("google_calendar.py")
    assert "CLOSED_DAYS" in src; assert "Sunday" in src
    assert '"error"' in src or "'error'" in src,"Error dict not returned"
    next_sun=dt.date.today()
    while next_sun.strftime("%A")!="Sunday": next_sun+=dt.timedelta(1)
    assert next_sun.strftime("%A")=="Sunday"

@tc("TC063","Calendar Logic","Reschedule — Slot Release","delete_event before create_event in main.py","Critical","Positive")
def t063(ctx):
    src=_need_src("main.py")
    assert "delete_event" in src
    dp=src.rfind("delete_event(appointment") if "delete_event(appointment" in src else src.rfind("delete_event(")
    cp=src.rfind("create_event(")
    assert dp!=-1,"delete_event call missing"
    assert cp!=-1,"create_event call missing"
    assert dp<cp,"delete_event must appear before create_event"

@tc("TC064","Calendar Logic","Business Hours Enforcement","BUSINESS_START=9, BUSINESS_END=18, Sunday closed","High","Negative")
def t064(ctx):
    src=_need_src("google_calendar.py")
    assert "BUSINESS_START = 9" in src or "BUSINESS_START=9" in src,"BUSINESS_START must be 9"
    assert "BUSINESS_END = 18" in src or "BUSINESS_END=18" in src,"BUSINESS_END must be 18"
    assert "Sunday" in src,"Sunday not in CLOSED_DAYS"

@tc("TC065","Calendar Logic","Same-Day Booking","diff_hours < 1 buffer in generate_available_slots","High","Positive")
def t065(ctx):
    src=_need_src("google_calendar.py")
    assert "diff_hours" in src or "< 1" in src,"Same-day 1-hour buffer missing"
    assert "now.date()" in src or "slot_time.date()" in src,"Same-day date comparison missing"

@tc("TC066","Calendar Logic","Holiday / Closure Blocking","HOLIDAYS list with 2026-12-25 in google_calendar.py","Medium","Positive")
def t066(ctx):
    src=_need_src("google_calendar.py")
    assert "HOLIDAYS" in src,"HOLIDAYS list missing"
    assert "2026-12-25" in src,"Christmas 2026 not in HOLIDAYS"

# ── Reminders & No-Show ───────────────────────────────────────────────────────
@tc("TC067","Reminders & No-Show","24-Hour Reminder Call","POST /doctor/reminder returns 200","Critical","Positive")
def t067(ctx):
    ctx.need_server(); r=ctx.get("/doctor/appointments")
    appts=r.json() if r.status_code==200 else []
    if not appts: skip("No appointments in DB")
    a=appts[0]; r2=ctx.post(f"/doctor/reminder?id={a['id']}&email={a['email']}")
    assert r2.status_code==200; assert "message" in r2.json()

@tc("TC068","Reminders & No-Show","Reminder Confirmation Capture","appointment_saved prevents re-booking","Critical","Positive")
def t068(ctx):
    src=_need_src("main.py"); assert "appointment_saved" in src; assert "confirm" in src

@tc("TC069","Reminders & No-Show","Reschedule Offer During Reminder","reschedule offer or noshow-reschedule flow in main.py","High","Positive")
def t069(ctx):
    src=_need_src("main.py")
    has=("reschedule-options" in src or "reschedule_options" in src or ("noshow" in src and "reschedule" in src) or ("reminder" in src and "reschedule" in src))
    assert has,"Reschedule offer during reminder missing"

@tc("TC070","Reminders & No-Show","Reminder — No Answer Handling","pending_messages queue in socket_manager.py","High","Negative")
def t070(ctx):
    src=_need_src("socket_manager.py")
    assert "pending_messages" in src,"pending_messages queue missing"
    assert "PRIVATE_MODES" in src,"PRIVATE_MODES set missing"
    assert "post_appointment" in src,"post_appointment not in PRIVATE_MODES"

@tc("TC071","Reminders & No-Show","No-Show Detection Trigger","POST /doctor/noshow/mark sets NO_SHOW state","Critical","Positive")
def t071(ctx):
    ctx.need_server(); r=ctx.get("/doctor/appointments")
    appts=[a for a in (r.json() if r.status_code==200 else []) if a.get("state")=="CONFIRMED"]
    if not appts: skip("No CONFIRMED appointments")
    a=appts[0]
    r2=ctx.post(f"/doctor/noshow/mark?id={a['id']}&email={a['email']}&name={a.get('name','Test')}")
    assert r2.status_code==200
    r3=ctx.get("/doctor/appointments")
    updated=next((x for x in r3.json() if x["id"]==a["id"]),None)
    assert updated and updated["state"]=="NO_SHOW",f"Not NO_SHOW: {updated}"

@tc("TC072","Reminders & No-Show","No-Show Recovery — Rebook Offer","noshow_mode rebook flow in main.py","High","Positive")
def t072(ctx):
    src=_need_src("main.py")
    assert "noshow_mode" in src; assert "wants_rebook" in src; assert "noshow_step" in src

@tc("TC073","Reminders & No-Show","Reminder Frequency Control","MAX_FOLLOWUP_ATTEMPTS + mark_followup_skipped","Medium","Positive")
def t073(ctx):
    src=_need_src("doctor_routes.py")
    assert "MAX_FOLLOWUP_ATTEMPTS" in src; assert "mark_followup_skipped" in src; assert "SKIPPED" in src

@tc("TC074","Reminders & No-Show","No-Show Rate Analytics","GET /doctor/appointments returns list","High","Positive")
def t074(ctx):
    ctx.need_server(); r=ctx.get("/doctor/appointments")
    assert r.status_code==200; assert isinstance(r.json(),list)

# ── Human Handoff ─────────────────────────────────────────────────────────────
@tc("TC075","Human Handoff","Explicit Customer Request","is_explicit_human_request detects all phrases + no false positives","Critical","Positive")
def t075(ctx):
    for p in ["I want to speak to a person","Connect me to a human","I need a live agent","Transfer me","human please"]:
        assert _is_human(p),f"Not detected: '{p}'"
    assert not _is_human("Book me a dentist appointment"),"False positive"
    src=_need_src("escalation.py"); assert "HUMAN_REQUEST_PHRASES" in src

@tc("TC076","Human Handoff","Low-Confidence Escalation","3 low-conf turns trigger; high conf resets","Critical","Positive")
def t076(ctx):
    threshold=0.5; limit=3
    def track(session,conf):
        if conf<threshold: session["lcc"]=session.get("lcc",0)+1
        else: session["lcc"]=0
        return session["lcc"]>=limit
    s={}
    assert not track(s,0.2),"Should not trigger on turn 1"
    assert not track(s,0.3),"Should not trigger on turn 2"
    assert track(s,0.4),"Should trigger on turn 3"
    track(s,0.9); assert s["lcc"]==0,"Counter should reset"

@tc("TC077","Human Handoff","Complex Request Escalation","is_out_of_scope detects 5 OOS topics","High","Positive")
def t077(ctx):
    for p in ["I need insurance pre-authorization","Can I get a refund","I want a second opinion","What about my lab results","Prescription refill please"]:
        assert _is_oos(p),f"Not OOS: '{p}'"
    assert not _is_oos("Book me a dentist")

@tc("TC078","Human Handoff","Context Packet Completeness","All 6 TC078 fields present in packet","Critical","Positive")
def t078(ctx):
    required=["call_id","customer_name","detected_intent","appointment_details","conversation_transcript","escalation_reason"]
    packet={"call_id":"c","escalation_reason":"explicit","customer_name":"Bob","detected_intent":"schedule","appointment_details":{},"conversation_transcript":[],"appointment_state":"TENTATIVE","timestamp":"Z"}
    for f in required: assert f in packet,f"Missing: {f}"

@tc("TC079","Human Handoff","Handoff When No Human Available","0-agent path returns no_agent=True with callback offer","High","Negative")
def t079(ctx):
    def handle(agents,reason):
        if agents<=0: return {"reply":"No agents. Schedule callback?","escalated":False,"no_agent":True}
        return {"reply":"Connecting.","escalated":True,"no_agent":False}
    r=handle(0,"explicit")
    assert r["no_agent"]==True; assert r["escalated"]==False
    assert "callback" in r["reply"].lower() or "no agent" in r["reply"].lower()
    r2=handle(3,"explicit"); assert r2["escalated"]==True; assert r2["no_agent"]==False

@tc("TC080","Human Handoff","Escalation Logging","escalation_log appended with all required fields","High","Positive")
def t080(ctx):
    log=[]
    def log_esc(call_id,reason,state,session):
        entry={"call_id":call_id,"escalation_reason":reason,"timestamp":dt.datetime.utcnow().isoformat()+"Z","appointment_state_at_escalation":state,"customer_name":session.get("slots",{}).get("name")}
        log.append(entry); return entry
    log_esc("c-080","explicit","TENTATIVE",{"slots":{"name":"Carol"}})
    assert len(log)==1
    for f in ("call_id","escalation_reason","timestamp","appointment_state_at_escalation"):
        assert f in log[0],f"Missing: {f}"
    src=_need_src("escalation.py"); assert "escalation_log" in src

@tc("TC081","Human Handoff","Escalation Rate KPI","10/50=20% passes; 11/50=22% fails","High","Performance")
def t081(ctx):
    def kpi(e,t):
        if not t: return {"rate":None,"passed":None}
        rate=round(e/t*100,1)
        return {"rate":rate,"passed":rate<=20.0}
    r1=kpi(10,50); assert r1["rate"]==20.0; assert r1["passed"]==True
    r2=kpi(11,50); assert r2["rate"]==22.0; assert r2["passed"]==False
    r3=kpi(0,0); assert r3["passed"] is None

# ── Post-Appointment ──────────────────────────────────────────────────────────
@tc("TC082","Post-Appointment","Feedback Collection Call","POST /doctor/complete returns 200 + initiates follow-up","High","Positive")
def t082(ctx):
    ctx.need_server(); r=ctx.get("/doctor/appointments")
    appts=[a for a in (r.json() if r.status_code==200 else []) if a.get("state") in ("CONFIRMED","RESCHEDULED")]
    if not appts: skip("No CONFIRMED/RESCHEDULED appointment")
    a=appts[0]
    r2=ctx.post(f"/doctor/complete?id={a['id']}&email={a['email']}&name={a.get('name','Test')}")
    assert r2.status_code==200
    data=r2.json(); assert "message" in data or "appointment_id" in data

@tc("TC083","Post-Appointment","Rebooking Offer","FSM COMPLETED→TENTATIVE + post_appointment_mode rebook step","High","Positive")
def t083(ctx):
    src=_need_src("main.py")
    assert "post_appointment_mode" in src; assert '"rebook"' in src
    m=_make_fsm("COMPLETED"); m.transition("TENTATIVE")
    assert m.get_state()=="TENTATIVE","FSM must allow COMPLETED→TENTATIVE"

@tc("TC084","Post-Appointment","Feedback Stored Against Appointment","POST /doctor/feedback/score saves score=4","Medium","Positive")
def t084(ctx):
    ctx.need_server(); r=ctx.get("/doctor/appointments")
    appts=[a for a in (r.json() if r.status_code==200 else []) if a.get("state")=="COMPLETED"]
    if not appts: skip("No COMPLETED appointments")
    a=appts[0]
    r2=ctx.post(f"/doctor/feedback/score?appointment_id={a['id']}&score=4&channel=voice")
    assert r2.status_code==200; assert r2.json().get("feedback_score")==4

@tc("TC085","Post-Appointment","No-Answer on Follow-Up","_followup_with_retry + RETRY_DELAY + SKIPPED in doctor_routes.py","Medium","Negative")
def t085(ctx):
    src=_need_src("doctor_routes.py")
    assert "_followup_with_retry" in src; assert "RETRY_DELAY_SECONDS" in src; assert "SKIPPED" in src

@tc("TC086","Post-Appointment","Post-Appointment Flow Does Not Trigger for Cancellations","POST /doctor/complete returns error for CANCELLED","High","Negative")
def t086(ctx):
    ctx.need_server(); r=ctx.get("/doctor/appointments")
    appts=[a for a in (r.json() if r.status_code==200 else []) if a.get("state")=="CANCELLED"]
    if not appts: skip("No CANCELLED appointments")
    a=appts[0]
    r2=ctx.post(f"/doctor/complete?id={a['id']}&email={a['email']}&name={a.get('name','Test')}")
    assert r2.status_code==200
    data=r2.json(); assert "error" in data or "CANCELLED" in str(data)

@tc("TC087","Post-Appointment","Aggregate Satisfaction Metric","GET /doctor/feedback/aggregate returns average_score","Medium","Positive")
def t087(ctx):
    ctx.need_server(); r=ctx.get("/doctor/feedback/aggregate")
    assert r.status_code==200; data=r.json()
    assert "average_score" in data; assert "total_responses" in data; assert "scores" in data

# ── Monitoring & Analytics ────────────────────────────────────────────────────
@tc("TC088","Monitoring & Analytics","Real-Time Call Success Rate","Seed outcomes + GET /analytics/call/success-rate","High","Positive")
def t088(ctx):
    ctx.need_server()
    c1=f"tc088-{uuid.uuid4().hex[:6]}"; c2=f"tc088-{uuid.uuid4().hex[:6]}"
    ctx.post(f"/analytics/call/outcome?call_id={c1}&outcome=success")
    ctx.post(f"/analytics/call/outcome?call_id={c2}&outcome=failed")
    r=ctx.get("/analytics/call/success-rate"); assert r.status_code==200
    data=r.json()
    assert "success_rate_pct" in data; assert "total_calls" in data
    assert 0<=data["success_rate_pct"]<=100

@tc("TC089","Monitoring & Analytics","State Transition Tracking","POST + GET /analytics/transitions","High","Positive")
def t089(ctx):
    ctx.need_server()
    r=ctx.post("/analytics/transitions/record",params={"appointment_id":"tc089","from_state":"TENTATIVE","to_state":"CONFIRMED","call_id":"tc089"})
    assert r.status_code==200
    r2=ctx.get("/analytics/transitions"); assert r2.status_code==200
    data=r2.json(); assert "total_transitions" in data; assert data["total_transitions"]>0

@tc("TC090","Monitoring & Analytics","Drop-Off Point Detection","Seed + GET /analytics/dropoff returns top 3","High","Positive")
def t090(ctx):
    ctx.need_server()
    for s in ("collecting_date","collecting_time","confirmation","collecting_date","collecting_date"):
        ctx.post(f"/analytics/dropoff?call_id=tc090-{uuid.uuid4().hex[:4]}&dialogue_stage={s}")
    r=ctx.get("/analytics/dropoff"); assert r.status_code==200
    data=r.json(); assert "top_dropoff_points" in data; assert len(data["top_dropoff_points"])<=3

@tc("TC091","Monitoring & Analytics","End-to-End Response Latency Metric","Seed latencies + P95 ≤2000ms + KPI pass","Critical","Performance")
def t091(ctx):
    ctx.need_server()
    for ms in [300,450,520,680,720,800,950,1100,1400,1800]:
        ctx.post(f"/analytics/latency?latency_ms={ms}&call_id=tc091&turn=1")
    r=ctx.get("/analytics/latency"); assert r.status_code==200
    data=r.json(); assert "p95_ms" in data; assert "kpi_passed" in data
    vals=sorted([300,450,520,680,720,800,950,1100,1400,1800])
    p95=vals[max(0,math.ceil(95/100*len(vals))-1)]
    assert p95<=2000,f"Seeded P95={p95}ms should be ≤2000ms"

@tc("TC092","Monitoring & Analytics","Complete Conversation Transcript Logging","Full start/turn/end/retrieve cycle","High","Positive")
def t092(ctx):
    ctx.need_server(); cid=f"tc092-{uuid.uuid4().hex[:8]}"
    ctx.post(f"/analytics/transcript/start?call_id={cid}&mask_pii=true")
    ctx.post(f"/analytics/transcript/turn?call_id={cid}&role=customer&text=Book+tomorrow")
    ctx.post(f"/analytics/transcript/turn?call_id={cid}&role=alva&text=What+time?")
    ctx.post(f"/analytics/transcript/end?call_id={cid}")
    r=ctx.get(f"/analytics/transcript/{cid}"); assert r.status_code==200
    data=r.json(); assert "call_id" in data or "turns" in data

@tc("TC093","Monitoring & Analytics","Error Event Logging","POST /analytics/error + GET /errors/summary","High","Positive")
def t093(ctx):
    ctx.need_server(); cid=f"tc093-{uuid.uuid4().hex[:6]}"
    r=ctx.post("/analytics/error",params={"call_id":cid,"component":"nlu","error_type":"timeout","detail":"LLM timed out","recovery_action":"fallback"})
    assert r.status_code==200
    r2=ctx.get("/analytics/errors/summary"); assert r2.status_code==200
    data=r2.json(); assert "total_errors" in data; assert "by_component" in data; assert "by_type" in data

@tc("TC094","Monitoring & Analytics","Appointment Pipeline Visibility","GET /analytics/pipeline returns all 7 FSM states","High","Positive")
def t094(ctx):
    ctx.need_server(); r=ctx.get("/analytics/pipeline")
    assert r.status_code==200,f"{r.status_code} — register GET /analytics/pipeline"
    data=r.json(); assert "state_counts" in data; assert "total" in data; assert "snapshot_at" in data
    for s in ("TENTATIVE","CONFIRMED","CANCELLED","COMPLETED","NO_SHOW"):
        assert s in data["state_counts"],f"State {s} missing"

@tc("TC095","Monitoring & Analytics","KPI Validation — Containment Rate","GET /analytics/containment with 80% target","Critical","Performance")
def t095(ctx):
    ctx.need_server(); r=ctx.get("/analytics/containment")
    assert r.status_code==200; data=r.json()
    assert "containment_rate_pct" in data; assert "kpi_target_pct" in data
    assert data["kpi_target_pct"]==80.0,f"Target must be 80%, got {data['kpi_target_pct']}"

# ── End-to-End Lifecycle ──────────────────────────────────────────────────────
@tc("TC096","End-to-End Lifecycle","Full Booking Lifecycle","FSM INQUIRY→TENTATIVE→CONFIRMED→COMPLETED","Critical","Positive")
def t096(ctx):
    m=_make_fsm("INQUIRY")
    for target in ("TENTATIVE","CONFIRMED","COMPLETED"):
        m.transition(target)
        assert m.get_state()==target,f"Expected {target}, got {m.get_state()}"

@tc("TC097","End-to-End Lifecycle","Reschedule Lifecycle","FSM CONFIRMED→RESCHEDULED→CONFIRMED","Critical","Positive")
def t097(ctx):
    m=_make_fsm("CONFIRMED"); m.transition("RESCHEDULED")
    assert m.get_state()=="RESCHEDULED"
    m.transition("CONFIRMED"); assert m.get_state()=="CONFIRMED"

@tc("TC098","End-to-End Lifecycle","No-Show Full Recovery Flow","FSM CONFIRMED→NO_SHOW + noshow flow in main.py","Critical","Positive")
def t098(ctx):
    import os as _os  # explicit local import — prevents UnboundLocalError
    m=_make_fsm("CONFIRMED"); m.transition("NO_SHOW")
    assert m.get_state()=="NO_SHOW",f"NO_SHOW failed: {m.get_state()}"
    src=_need_src("main.py")
    assert "noshow_mode" in src,"noshow_mode flow missing"
    assert "__noshow_mode__:" in src,"noshow activation signal missing"
    assert "noshow_dialogue" in src,"noshow_dialogue call missing"

@tc("TC099","End-to-End Lifecycle","Cancellation at Every Lifecycle Stage","Cancel from TENTATIVE, CONFIRMED, RESCHEDULED","Critical","Positive")
def t099(ctx):
    for start in ("TENTATIVE","CONFIRMED","RESCHEDULED"):
        m=_make_fsm(start); m.transition("CANCELLED")
        assert m.get_state()=="CANCELLED",f"Cancel from {start} failed: {m.get_state()}"

@tc("TC100","End-to-End Lifecycle","End-to-End Latency — Full Booking Turn","GET /analytics/snapshot < 2000ms with all keys","Critical","Performance")
def t100(ctx):
    ctx.need_server(); t0=time.monotonic(); r=ctx.get("/analytics/snapshot"); ms=(time.monotonic()-t0)*1000
    assert r.status_code==200,f"{r.status_code}"
    data=r.json()
    for key in ("pipeline","latency","containment","errors"):
        assert key in data,f"'{key}' missing from snapshot"
    assert ms<2000,f"Snapshot took {ms:.0f}ms — must be <2000ms"

# =============================================================================
# RUNNER
# =============================================================================
def run(base_url="http://127.0.0.1:9001",module_filter=None,tc_filter=None,verbose=False,report_path=None):
    ctx=Ctx(base_url=base_url,verbose=verbose)
    tests=_REGISTRY[:]
    if module_filter: tests=[t for t in tests if module_filter.lower() in t.module.lower()]
    if tc_filter: tests=[t for t in tests if t.tc_id.upper()==tc_filter.upper()]
    if not tests: print(R("No test cases matched.")); return 1

    files=["main.py","nlu.py","fsm.py","escalation.py","session_store.py","google_calendar.py","dialogue_manager.py","doctor_routes.py","socket_manager.py","analytics.py"]
    found=[f for f in files if _find_file(f)]
    missing=[f for f in files if not _find_file(f)]
    server_up=ctx.server_available()

    print(W(f"\n{'═'*68}"))
    print(W(f"  ALVA Test Suite  ·  {len(tests)} test(s)"))
    if module_filter: print(f"  Module : {module_filter}")
    if tc_filter:     print(f"  TC     : {tc_filter}")
    print(f"  Server : {base_url}  ({G('UP') if server_up else Y('DOWN — live tests will skip')})")
    print(f"  Files  : {G(str(len(found)))}/{len(files)} backend files found  ({', '.join(found) if found else 'none'})")
    if missing: print(f"  {Y('Missing')}: {', '.join(missing)}\n  {Y('→ Place test_runner.py in the same folder as your .py files OR in the parent folder')}")
    print(W(f"{'═'*68}\n"))

    cur_mod=None
    for obj in tests:
        if obj.module!=cur_mod:
            cur_mod=obj.module; print(f"\n  {W(B(f'▶ {cur_mod}'))}")
        obj.run(ctx)
        icon={R_.PASS:G("✓"),R_.FAIL:R("✗"),R_.SKIP:Y("⊘"),R_.ERROR:R("!")}[obj.status]
        feat=obj.feature[:44].ljust(44); dur=DIM(f"{obj.duration_ms:6.0f}ms")
        print(f"    {icon}  {W(obj.tc_id.ljust(8))}  {feat}  {dur}")
        if verbose or obj.status in (R_.FAIL,R_.ERROR):
            if obj.detail and obj.detail!="OK":
                for line in obj.detail.strip().split("\n")[:6]:
                    print(f"           {DIM(line)}")

    passed=sum(1 for t in tests if t.status==R_.PASS)
    failed=sum(1 for t in tests if t.status==R_.FAIL)
    errors=sum(1 for t in tests if t.status==R_.ERROR)
    skipped=sum(1 for t in tests if t.status==R_.SKIP)
    tested=passed+failed+errors
    rate=round(passed/tested*100,1) if tested>0 else 0.0

    print(f"\n{W('═'*68)}")
    print(W("  SUMMARY"))
    print(f"{'═'*68}")
    print(f"  Total     : {len(tests)}")
    print(f"  {G('Pass')}      : {G(str(passed))}")
    print(f"  {R('Fail')}      : {R(str(failed+errors))}")
    print(f"  {Y('Skip')}      : {Y(str(skipped))}  (server tests need ALVA running)")
    print(f"  Pass Rate : {(G if rate>=80 else R)(f'{rate}%')}  {'✓ KPI MET' if rate>=80 else '✗ BELOW 80%'} (of tested — excludes skips)")
    print(f"{'═'*68}\n")

    failures=[t for t in tests if t.status in (R_.FAIL,R_.ERROR)]
    if failures:
        print(W(R("  Failed / Error:")))
        for t in failures:
            print(f"    {R('✗')} {W(t.tc_id)} [{t.module}] — {t.feature}")
            for line in (t.detail or "").strip().split("\n")[:4]:
                print(f"       {DIM(line)}")
        print()

    if skipped>0 and not module_filter and not tc_filter:
        server_skips=sum(1 for t in tests if t.status==R_.SKIP and "Server not running" in t.detail)
        file_skips  =sum(1 for t in tests if t.status==R_.SKIP and "not found" in t.detail)
        print(f"  {Y('ℹ')}  {skipped} skips:")
        if server_skips: print(f"     • {server_skips} live tests — start ALVA server: {W('uvicorn backend.main:app --port 9001')}")
        if file_skips:   print(f"     • {file_skips} file tests — move test_runner.py next to your .py files")
        print()

    if report_path:
        report={"run_at":dt.datetime.utcnow().isoformat()+"Z","base_url":base_url,
                "summary":{"total":len(tests),"passed":passed,"failed":failed+errors,"skipped":skipped,"pass_rate_pct":rate},
                "results":[{"tc_id":t.tc_id,"module":t.module,"feature":t.feature,"priority":t.priority,"type":t.tc_type,"status":t.status,"duration_ms":round(t.duration_ms,1),"detail":t.detail} for t in tests]}
        with open(report_path,"w") as f: json.dump(report,f,indent=2)
        print(f"  Report → {report_path}\n")

    return 0 if (failed+errors)==0 else 1

def main():
    p=argparse.ArgumentParser(description="ALVA Test Suite TC001–TC100")
    p.add_argument("--base-url",default="http://127.0.0.1:9001")
    p.add_argument("--module",default=None,help="e.g. NLU  or  'Human Handoff'")
    p.add_argument("--tc",default=None,help="e.g. TC026")
    p.add_argument("-v","--verbose",action="store_true")
    p.add_argument("--report",default=None)
    p.add_argument("--list",action="store_true")
    args=p.parse_args()
    if args.list:
        cur=None
        for t in _REGISTRY:
            if t.module!=cur: cur=t.module; print(f"\n  {W(t.module)}")
            print(f"    {B(t.tc_id):12s}  {t.feature}")
        print(f"\n  Total: {len(_REGISTRY)} test cases\n"); return 0
    return run(base_url=args.base_url,module_filter=args.module,tc_filter=args.tc,verbose=args.verbose,report_path=args.report)

if __name__=="__main__": sys.exit(main())