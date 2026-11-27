"""WhatsApp group moderation bot.

Watches groups with neonize, checks every text message with Jev (one cheap
typed judgment per message), and posts flagged ones to an admin group.
Admins reply there with "12 del", "12 warn" or "12 ok".
No model call is made for admin commands; plain code carries them out.
The bot never removes members. That stays a human job, on purpose.
"""
import base64, datetime, hashlib, json, os, queue, re, secrets, sqlite3, threading, time
from collections import defaultdict, deque
from pathlib import Path
from zoneinfo import ZoneInfo

import segno
import uvicorn
from fasthtml.common import *
from starlette.responses import HTMLResponse, Response
from neonize.client import NewClient
from neonize.events import ConnectedEv, MessageEv
from neonize.utils.jid import Jid2String, JIDToNonAD, build_jid
from typesafe_sdk import Choice, TypeSafeClient

# ---------- settings (plash.env) ----------
DATA = Path(__file__).parent / "data"
DATA.mkdir(exist_ok=True)
BOT_NAME = os.environ.get("BOT_NAME", "Moderation bot")
WATCH_GROUPS = {g.strip() for g in os.environ.get("WATCH_GROUPS", "").split(",") if g.strip()}
ADMIN_GROUP = os.environ.get("ADMIN_GROUP", "").strip()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
FLAG_THRESHOLD = float(os.environ.get("FLAG_THRESHOLD", "0.6"))
LABEL_THRESHOLDS = {"hype_forward": 0.85}  # stricter bar for labels that misfire on good posts
CONTEXT_MSGS = 3  # earlier messages sent along with each new one, so replies make sense
DISPLAY_TZ = ZoneInfo(os.environ.get("DISPLAY_TZ", "UTC"))  # Plash servers run on US time

# Replace with your group's norms. Plain words work best. data/rules.txt on the server overrides this.
DEFAULT_RULES = """\
This group is for people learning and building together.
- Share what you have built or learned, and ask for help when stuck.
- Help and encourage each other.
Avoid forwards, hype or fear posts, and posts you have not read yourself.
It's fine to share an article you have read and found useful.
Be kind. No sarcasm, mocking or put-downs.
Not allowed: scams, get-rich schemes, selling or ads, abuse, or adult content."""

def rules():
    f = DATA / "rules.txt"
    return f.read_text() if f.exists() else DEFAULT_RULES

# One label per kind of problem. Describe each in plain words; the wording matters more than the cutoffs.
CATEGORIES = {
    "ok": "Fits the group: normal conversation, questions, relevant sharing, or harmless off-topic chat.",
    "spam_scam": "Scam, phishing, earning/investment scheme, or mass-forwarded spam.",
    "promotion": "Selling or promoting a product, service, group or channel that the rules do not allow.",
    "abusive": "Insults, harassment, hate, threats, or sexual content aimed at people.",
    "disrespectful": "Rude, sarcastic, mocking or dismissive toward members or admins, without being outright abusive.",
    "hype_forward": ("A forwarded chain message, a hype or fear post, or excited news/launch announcement. "
                     "Sharing an article, post or link that the sender read and found useful is fine, even with little comment."),
    "rule_break": "Breaks a specific group rule in another way.",
}
SUGGEST = {"spam_scam": "del, then an admin removes the sender by hand", "promotion": "del", "abusive": "del + warn",
           "rule_break": "warn", "disrespectful": "warn", "hype_forward": "warn"}

# ---------- storage ----------
db = sqlite3.connect(DATA / "admin.db", check_same_thread=False)
db_lock = threading.Lock()
with db_lock:
    db.executescript("""
    create table if not exists msgs(id integer primary key, ts real, chat text, sender text,
        name text, wa_id text, text text, label text, prob real, scores text, rules_ver text);
    create table if not exists cases(id integer primary key, msg_id integer, label text, prob real,
        suggested text, status text default 'open', decided_by text, decided_at real);""")
    db.commit()

def q(sql, args=()):
    with db_lock:
        cur = db.execute(sql, args); db.commit(); return cur

# ---------- model ----------
ts_client = TypeSafeClient() if os.environ.get("TYPESAFE_API_KEY") else None

def judge(new, recent):
    """One Jev Choice per message. Returns (label, probability, all scores)."""
    if not ts_client: return "ok", 0.0, {}
    r = ts_client.system_one(
        state={"group_rules": rules(), "earlier_messages": recent, "new_message": new},
        questions={"verdict": Choice(
            instructions="Judge only `new_message.text` against `group_rules`. "
                         "`earlier_messages` are context; do not judge them.",
            criteria=CATEGORIES)})
    a = r.choices["verdict"]
    return a.choice, a.probabilities.get(a.choice, a.confidence), dict(a.probabilities)

# ---------- WhatsApp ----------
client = NewClient(str(DATA / "neonize.db"))  # session lives in data/, so redeploys keep the login
state = {"qr": None, "connected": False}
recent = defaultdict(lambda: deque(maxlen=CONTEXT_MSGS))
work = queue.Queue()

def jid_str(j): return Jid2String(JIDToNonAD(j))
def to_jid(s):
    user, server = s.split("@"); return build_jid(user, server)

@client.qr
def on_qr(_, data: bytes):
    state["qr"] = data.decode(); state["connected"] = False

@client.event(ConnectedEv)
def on_connected(_, __):
    state["connected"] = True; state["qr"] = None
    print("connected", flush=True)

@client.event(MessageEv)
def on_message(_, ev: MessageEv):
    src = ev.Info.MessageSource
    if src.IsFromMe or not src.IsGroup: return
    m = ev.Message
    text = (m.conversation or m.extendedTextMessage.text or "").strip()
    if not text: return
    chat = jid_str(src.Chat)
    item = dict(chat=chat, sender=jid_str(src.Sender), wa_id=ev.Info.ID, text=text,
                name=getattr(ev.Info, "Pushname", "") or "someone")
    if chat == ADMIN_GROUP: handle_admin(item)
    elif chat in WATCH_GROUPS: work.put(item)  # model call happens off the event thread

def worker():
    while True:
        it = work.get()
        try: check(it)
        except Exception as e: print("check failed:", repr(e), flush=True)

def check(it):
    new = {"from": it["name"], "text": it["text"]}
    label, prob, scores = judge(new, list(recent[it["chat"]]))
    ver = hashlib.sha1((rules() + json.dumps(CATEGORIES, sort_keys=True)).encode()).hexdigest()[:8]
    recent[it["chat"]].append(new)
    mid = q("insert into msgs(ts,chat,sender,name,wa_id,text,label,prob,scores,rules_ver) values(?,?,?,?,?,?,?,?,?,?)",
            (time.time(), it["chat"], it["sender"], it["name"], it["wa_id"], it["text"], label, prob,
             json.dumps({k: round(v, 4) for k, v in scores.items()}), ver)).lastrowid
    if label == "ok" or prob < LABEL_THRESHOLDS.get(label, FLAG_THRESHOLD) or not ADMIN_GROUP: return
    sug = SUGGEST.get(label, "review")
    cid = q("insert into cases(msg_id,label,prob,suggested) values(?,?,?,?)", (mid, label, prob, sug)).lastrowid
    snippet = it["text"] if len(it["text"]) < 300 else it["text"][:300] + "…"
    client.send_message(to_jid(ADMIN_GROUP),
        f"#{cid} {label} ({prob:.2f}) · {it['name']}:\n\"{snippet}\"\n"
        f"Suggest: {sug}\nReply: {cid} del | {cid} warn | {cid} ok")

# Admin commands. Deliberately no "kick": removing a member stays manual.
CMD = re.compile(r"^\s*#?(\d+)\s+(del|delete|warn|ok)\b", re.I)

def handle_admin(it):
    m = CMD.match(it["text"])
    if not m: return
    cid, cmd = int(m[1]), m[2].lower()
    row = q("""select c.status, g.chat, g.sender, g.wa_id, g.name from cases c
               join msgs g on g.id=c.msg_id where c.id=?""", (cid,)).fetchone()
    admin = to_jid(ADMIN_GROUP)
    if not row: return client.send_message(admin, f"#{cid}: no such case")
    status, chat, sender, wa_id, name = row
    if status != "open": return client.send_message(admin, f"#{cid} was already handled ({status})")
    try:
        if cmd in ("del", "delete"):
            client.revoke_message(to_jid(chat), to_jid(sender), wa_id)
        if cmd == "warn":
            client.send_message(to_jid(chat), f"{name}, please keep to the group rules. Thanks!")
    except Exception as e:
        return client.send_message(admin, f"#{cid} {cmd} failed: {e!r}. Is the bot an admin of that group?")
    q("update cases set status=?, decided_by=?, decided_at=? where id=?", (cmd, it["name"], time.time(), cid))
    client.send_message(admin, f"#{cid} done: {cmd} (by {it['name']})")

# ---------- web pages: public norms at /, admin pages behind a password ----------
PUBLIC = {"/", "/favicon.ico"}

def auth(req, sess):
    if req.url.path in PUBLIC: return
    expected = "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
    if not ADMIN_PASSWORD or not secrets.compare_digest(req.headers.get("authorization", ""), expected):
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="admin"'})

app, rt = fast_app(before=Beforeware(auth), live=False)

@rt("/")
def landing():
    return Titled(BOT_NAME,
        P("This bot reads group messages, checks them against the group's norms, and suggests an action to the admins. "
          "An admin approves every action. It cannot remove members."),
        H3("Group norms"), Pre(rules()),
        H3("What it looks for"), Ul(*[Li(Code(k), ": ", v) for k, v in CATEGORIES.items()]))

@rt("/favicon.ico")
def favicon(): return Response(status_code=204)

STATUS = {"open": "waiting", "del": "deleted", "delete": "deleted", "warn": "warned", "ok": "false alarm"}

def local(ts): return datetime.datetime.fromtimestamp(ts, DISPLAY_TZ).strftime("%d %b, %H:%M") if ts else ""

def alerts_table(limit=100):
    rows = q("""select c.id, m.ts, c.label, c.prob, m.name, m.text, c.suggested, c.status, c.decided_by, c.decided_at
                from cases c join msgs m on m.id = c.msg_id order by c.id desc limit ?""", (limit,)).fetchall()
    if not rows: return P("No alerts yet.")
    head = Tr(*[Th(h) for h in ("#", "Posted", "Label", "Score", "From", "Message", "Suggested", "Outcome", "Decided")])
    body = [Tr(Td(f"#{cid}"), Td(local(ts)), Td(Code(label)), Td(f"{prob:.2f}"), Td(name), Td(text),
               Td(sug), Td(Strong(STATUS.get(status, status))), Td(f"{by}, {local(at)}" if by else ""))
            for cid, ts, label, prob, name, text, sug, status, by, at in rows]
    return Div(Table(Thead(head), Tbody(*body)), style="overflow-x:auto")

@rt("/admin")
def admin():
    if state["qr"]:
        body = Div(Meta(http_equiv="refresh", content="10"),
                   H2("Scan with the bot's WhatsApp → Linked devices"),
                   NotStr(segno.make(state["qr"]).svg_inline(scale=6)),
                   P("This page refreshes every 10 seconds. Codes stop after about 2 minutes; "
                     "if the phone shows an error, restart the app and scan right away."))
    elif not state["connected"]:
        body = P("Connecting… refresh in a few seconds.")
    else:
        groups = [(g.GroupName.Name, jid_str(g.JID)) for g in client.get_joined_groups()]
        checked = q("select count(*) from msgs").fetchone()[0]
        n_alerts, n_open = q("select count(*), coalesce(sum(status='open'),0) from cases").fetchone()
        body = Div(P(f"Connected. Messages checked: {checked} · Alerts: {n_alerts} · Waiting for an admin: {n_open}"),
                   H3("Recent alerts"), alerts_table(),
                   Details(Summary("Settings and group IDs"),
                           P(f"Watching: {', '.join(WATCH_GROUPS) or 'nothing yet'} · Admin group: {ADMIN_GROUP or 'not set'}"),
                           Table(*[Tr(Td(n), Td(Code(j))) for n, j in groups])))
    return Titled(f"{BOT_NAME} · admin", body)

if __name__ == "__main__":
    # uvicorn directly, not FastHTML serve(): serve() re-imports the module and would start the bot twice.
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=client.connect, daemon=True).start()
    uvicorn.run(app, host="0.0.0.0", port=5001)
