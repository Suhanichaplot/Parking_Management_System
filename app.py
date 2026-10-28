import math
import re
import sqlite3
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, flash, g, redirect, render_template, request,
                   session, url_for)
from jinja2 import DictLoader
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = "parking-demo-secret"
DB_FILE = "parking.db"
OVERSTAY_HOURS = 12
TYPES = {"two": "Two-wheeler", "four": "Car / four-wheeler"}
TIME_FMT = "%Y-%m-%d %H:%M:%S"


# ---------- database ----------
def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_FILE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_FILE)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users(username TEXT PRIMARY KEY, password TEXT, role TEXT);
        CREATE TABLE IF NOT EXISTS rates(type TEXT PRIMARY KEY, per_hour INTEGER);
        CREATE TABLE IF NOT EXISTS slots(id TEXT PRIMARY KEY, type TEXT);
        CREATE TABLE IF NOT EXISTS vehicles(id INTEGER PRIMARY KEY AUTOINCREMENT,
            number TEXT, type TEXT, slot TEXT, time_in TEXT, time_out TEXT, fee INTEGER DEFAULT 0);
    """)
    if not conn.execute("SELECT 1 FROM users").fetchone():
        conn.execute("INSERT INTO users VALUES(?,?,?)", ("admin", generate_password_hash("admin123"), "Administrator"))
        conn.execute("INSERT INTO users VALUES(?,?,?)", ("operator", generate_password_hash("op123"), "Operator"))
        conn.executemany("INSERT INTO rates VALUES(?,?)", [("two", 10), ("four", 20)])
        conn.executemany("INSERT INTO slots VALUES(?,?)",
                         [("T%d" % i, "two") for i in range(1, 9)] + [("C%d" % i, "four") for i in range(1, 13)])
    conn.commit()
    conn.close()


# ---------- helper functions ----------
def now():
    return datetime.now().replace(microsecond=0)


def parse(text):
    return datetime.strptime(text, TIME_FMT)


def hours_charged(time_in):
    """Parking time is rounded up to full hours, minimum 1 hour."""
    seconds = (now() - parse(time_in)).total_seconds()
    return max(1, math.ceil(seconds / 3600))


def rate_of(vtype):
    return db().execute("SELECT per_hour FROM rates WHERE type=?", (vtype,)).fetchone()["per_hour"]


def find_parked(number):
    return db().execute("SELECT * FROM vehicles WHERE number=? AND time_out IS NULL", (number,)).fetchone()


def login_required(role=None):
    def wrap(fn):
        @wraps(fn)
        def inner(*a, **kw):
            if "user" not in session:
                return redirect(url_for("login"))
            if role and session["role"] != role:
                flash("Only the administrator can open this page.", "err")
                return redirect(url_for("slots"))
            return fn(*a, **kw)
        return inner
    return wrap


@app.template_filter("dt")
def dt_filter(text):
    return parse(text).strftime("%d-%m-%y %H:%M") if text else "-"


app.jinja_env.globals["TYPES"] = TYPES


# ---------- login ----------
@app.route("/", methods=["GET", "POST"])
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        row = db().execute("SELECT * FROM users WHERE username=?", (request.form["username"].strip(),)).fetchone()
        if row and check_password_hash(row["password"], request.form["password"]):
            session["user"], session["role"] = row["username"], row["role"]
            return redirect(url_for("slots"))
        flash("Wrong username or password.", "err")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- slots ----------
@app.route("/slots")
@login_required()
def slots():
    rows = db().execute("""SELECT s.id, s.type, v.number, v.time_in FROM slots s
        LEFT JOIN vehicles v ON v.slot = s.id AND v.time_out IS NULL ORDER BY s.rowid""").fetchall()
    bays, count = [], {t: [0, 0] for t in TYPES}      # [total, used]
    for r in rows:
        used = r["number"] is not None
        over = used and (now() - parse(r["time_in"])).total_seconds() > OVERSTAY_HOURS * 3600
        bays.append({"id": r["id"], "number": r["number"], "state": "over" if over else "busy" if used else "free"})
        count[r["type"]][0] += 1
        count[r["type"]][1] += used
    return render_template("slots.html", bays=bays, count=count, limit=OVERSTAY_HOURS)


# ---------- vehicle entry ----------
@app.route("/entry", methods=["GET", "POST"])
@login_required()
def entry():
    ticket = None
    if request.method == "POST":
        number = request.form["number"].strip().upper()
        vtype = request.form["type"]
        if not re.fullmatch(r"[A-Z0-9 -]{4,12}", number):
            flash("Enter a valid vehicle number (4 to 12 letters or digits).", "err")
        elif find_parked(number):
            flash(number + " is already parked.", "err")
        else:
            free = db().execute("""SELECT id FROM slots WHERE type=? AND id NOT IN
                (SELECT slot FROM vehicles WHERE time_out IS NULL) ORDER BY rowid LIMIT 1""", (vtype,)).fetchone()
            if not free:
                flash("Slot full for %s. Vehicle cannot enter now." % TYPES[vtype], "err")
            else:
                t = now().strftime(TIME_FMT)
                db().execute("INSERT INTO vehicles(number,type,slot,time_in) VALUES(?,?,?,?)",
                             (number, vtype, free["id"], t))
                db().commit()
                flash("Vehicle added. Give the ticket to the driver.", "ok")
                ticket = {"number": number, "type": TYPES[vtype], "slot": free["id"], "time_in": t,
                          "rate": rate_of(vtype)}
    return render_template("entry.html", ticket=ticket)


# ---------- vehicle exit ----------
@app.route("/exit")
@login_required()
def exit_page():
    number = request.args.get("number", "").strip().upper()
    found = None
    if number:
        v = find_parked(number)
        if v:
            h = hours_charged(v["time_in"])
            found = {"v": v, "hours": h, "rate": rate_of(v["type"]), "fee": h * rate_of(v["type"])}
        else:
            flash("No parked vehicle with number " + number + ".", "err")
    return render_template("exit.html", found=found, receipt=None, number=number)


@app.route("/exit/pay", methods=["POST"])
@login_required()
def exit_pay():
    v = find_parked(request.form["number"])
    if not v:
        return redirect(url_for("exit_page"))
    h = hours_charged(v["time_in"])
    fee = h * rate_of(v["type"])
    t = now().strftime(TIME_FMT)
    db().execute("UPDATE vehicles SET time_out=?, fee=? WHERE id=?", (t, fee, v["id"]))
    db().commit()
    flash("Fee taken and vehicle exited.", "ok")
    receipt = {"number": v["number"], "slot": v["slot"], "time_in": v["time_in"], "time_out": t, "hours": h, "fee": fee}
    return render_template("exit.html", found=None, receipt=receipt, number="")


@app.route("/exit/older", methods=["POST"])
@login_required()
def exit_older():
    """Demo helper: moves the entry time back by 1 hour so a fee can be shown in the video."""
    v = find_parked(request.form["number"])
    if v:
        new = (parse(v["time_in"]) - timedelta(hours=1)).strftime(TIME_FMT)
        db().execute("UPDATE vehicles SET time_in=? WHERE id=?", (new, v["id"]))
        db().commit()
    return redirect(url_for("exit_page", number=request.form["number"]))


# ---------- reports (admin) ----------
@app.route("/reports")
@login_required("Administrator")
def reports():
    rows = db().execute("SELECT * FROM vehicles ORDER BY id DESC").fetchall()
    done = [r for r in rows if r["time_out"]]
    stats = {"total": len(rows), "parked": len(rows) - len(done), "exits": len(done),
             "income": sum(r["fee"] for r in done)}
    return render_template("reports.html", rows=rows, stats=stats)


# ---------- settings (admin) ----------
@app.route("/settings")
@login_required("Administrator")
def settings():
    rates = {r["type"]: r["per_hour"] for r in db().execute("SELECT * FROM rates")}
    users = db().execute("SELECT username, role FROM users").fetchall()
    return render_template("settings.html", rates=rates, users=users)


@app.route("/settings/rates", methods=["POST"])
@login_required("Administrator")
def set_rates():
    try:
        two, four = int(request.form["two"]), int(request.form["four"])
        assert two > 0 and four > 0
    except (ValueError, AssertionError):
        flash("Rates must be numbers above 0.", "err")
        return redirect(url_for("settings"))
    db().execute("UPDATE rates SET per_hour=? WHERE type='two'", (two,))
    db().execute("UPDATE rates SET per_hour=? WHERE type='four'", (four,))
    db().commit()
    flash("Rates updated.", "ok")
    return redirect(url_for("settings"))


@app.route("/settings/slot", methods=["POST"])
@login_required("Administrator")
def add_slot():
    vtype = request.form["type"]
    n = db().execute("SELECT COUNT(*) FROM slots WHERE type=?", (vtype,)).fetchone()[0] + 1
    sid = ("T" if vtype == "two" else "C") + str(n)
    db().execute("INSERT INTO slots VALUES(?,?)", (sid, vtype))
    db().commit()
    flash("Slot %s added." % sid, "ok")
    return redirect(url_for("settings"))


@app.route("/settings/user", methods=["POST"])
@login_required("Administrator")
def add_user():
    name, pw = request.form["username"].strip(), request.form["password"]
    if len(name) < 3 or len(pw) < 4:
        flash("Username needs 3+ characters and password 4+.", "err")
    elif db().execute("SELECT 1 FROM users WHERE username=?", (name,)).fetchone():
        flash("That username is already used.", "err")
    else:
        db().execute("INSERT INTO users VALUES(?,?,?)", (name, generate_password_hash(pw), request.form["role"]))
        db().commit()
        flash("User %s added." % name, "ok")
    return redirect(url_for("settings"))


# ---------- templates ----------
CSS = """*{box-sizing:border-box}body{margin:0;font-family:"Trebuchet MS","Segoe UI",Arial,sans-serif;background:#dde1e4;color:#22272b;line-height:1.45}
header{background:#262b30;color:#fff;padding:12px 20px;display:flex;flex-wrap:wrap;gap:12px;align-items:center;justify-content:space-between;border-bottom:5px solid #f2b705}
header h1{font-size:19px;margin:0}header span{font-size:14px;color:#c9cfd4}header a{color:#f2b705;margin-left:10px}
nav{display:flex;flex-wrap:wrap;gap:6px;padding:12px 20px 0}nav a{background:#fff;border:1px solid #b9c0c6;padding:8px 16px;border-radius:3px 3px 0 0;color:#22272b;text-decoration:none;font-size:14px}
nav a.on{background:#f2b705;border-color:#f2b705;font-weight:700}main{background:#fff;margin:0 20px 20px;padding:20px;border:1px solid #b9c0c6;min-height:340px}
h2{font-size:18px;margin:18px 0 12px}h2:first-child{margin-top:0}
.btn{background:#262b30;color:#fff;border:0;padding:9px 16px;border-radius:3px;cursor:pointer;font:inherit;font-size:14px}.btn.alt{background:#fff;color:#262b30;border:1px solid #262b30}
input,select{font:inherit;font-size:14px;padding:8px;border:1px solid #98a1a8;border-radius:3px;margin:0 8px 8px 0;min-width:170px}label{display:block;font-size:13px;color:#4a5359;margin-bottom:2px}
form.row{display:flex;flex-wrap:wrap;align-items:flex-end}form.row>div{margin-right:4px}form.inline{display:inline}
.msg{padding:9px 12px;margin-bottom:14px;border-left:5px solid #2e7d4f;background:#e6f2ea;font-size:14px}.msg.err{border-color:#b4432f;background:#f7e4df}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(110px,1fr));gap:10px;margin:12px 0}
.bay{border:2px solid #f2b705;border-top:0;border-bottom:0;padding:12px 6px;text-align:center;font-size:13px;background:#f6f8f9;min-height:74px}.bay b{display:block;font-size:16px}
.bay.busy{background:#3a4148;color:#fff}.bay.over{background:#b4432f;color:#fff}
.stats{display:flex;flex-wrap:wrap;gap:10px}.stat{border:1px solid #b9c0c6;padding:8px 14px;font-size:14px}.stat b{font-size:20px;display:block}
table{border-collapse:collapse;width:100%;font-size:14px;margin-top:10px}th,td{border:1px solid #b9c0c6;padding:6px 8px;text-align:left}th{background:#eef0f2}
.ticket{border:2px dashed #262b30;padding:14px 18px;max-width:340px;margin-top:16px;background:#fffdf3}.ticket h3{margin:0 0 8px;font-size:16px}.ticket p{margin:3px 0;font-size:14px}
.login{max-width:340px;margin:50px auto;background:#fff;padding:24px;border-top:6px solid #f2b705}.hint{font-size:13px;color:#4a5359;margin-top:14px}
@media print{header,nav,.noprint{display:none}main{border:0;margin:0}}"""

BASE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Parking Management System</title><style>""" + CSS + """</style></head><body>
{% if session.user %}<header><h1>Parking Management System</h1><span>{{ session.user }} ({{ session.role }})<a href="{{ url_for('logout') }}">Log out</a></span></header>
<nav>{% for ep, name, adm in [('slots','Slots',0),('entry','Entry',0),('exit_page','Exit',0),('reports','Reports',1),('settings','Settings',1)] %}
{% if not adm or session.role == 'Administrator' %}<a href="{{ url_for(ep) }}" class="{{ 'on' if request.endpoint == ep }}">{{ name }}</a>{% endif %}{% endfor %}</nav>{% endif %}
{% block outer %}<main>{% for c, m in get_flashed_messages(with_categories=true) %}<div class="msg {{ c }}">{{ m }}</div>{% endfor %}{% block body %}{% endblock %}</main>{% endblock %}
</body></html>"""

TEMPLATES = {
    "base.html": BASE,
    "login.html": """{% extends 'base.html' %}{% block outer %}<div class="login"><h2>Parking Management System</h2>
{% for c, m in get_flashed_messages(with_categories=true) %}<div class="msg {{ c }}">{{ m }}</div>{% endfor %}
<form method="post"><label for="u">Username</label><input id="u" name="username" autofocus><label for="p">Password</label><input id="p" name="password" type="password"><br>
<button class="btn">Log in</button></form><p class="hint">Sample logins: admin / admin123 and operator / op123</p></div>{% endblock %}""",
    "slots.html": """{% extends 'base.html' %}{% block body %}<h2>Slot status</h2><div class="stats">
{% for t, c in count.items() %}<div class="stat"><b>{{ c[0] - c[1] }} free</b>{{ TYPES[t] }} ({{ c[1] }} of {{ c[0] }} filled)</div>{% endfor %}</div>
<div class="grid">{% for b in bays %}<div class="bay {{ b.state if b.state != 'free' }}"><b>{{ b.id }}</b>{{ b.number or 'Free' }}{% if b.state == 'over' %}<br>Overstay{% endif %}</div>{% endfor %}</div>
<p class="hint">Red slot means the vehicle is parked for more than {{ limit }} hours.</p>{% endblock %}""",
    "entry.html": """{% extends 'base.html' %}{% block body %}<h2>Vehicle entry</h2>
<form method="post" class="row"><div><label for="n">Vehicle number</label><input id="n" name="number" placeholder="GJ05AB1234" autofocus></div>
<div><label for="t">Vehicle type</label><select id="t" name="type">{% for k, v in TYPES.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select></div>
<div><button class="btn">Give slot and ticket</button></div></form>
{% if ticket %}<div class="ticket"><h3>Entry ticket</h3><p>Vehicle: {{ ticket.number }}</p><p>Type: {{ ticket.type }}</p><p>Slot: {{ ticket.slot }}</p>
<p>Time in: {{ ticket.time_in | dt }}</p><p>Rate: Rs. {{ ticket.rate }} per hour</p><p class="noprint"><button class="btn alt" onclick="window.print()">Print</button></p></div>{% endif %}{% endblock %}""",
    "exit.html": """{% extends 'base.html' %}{% block body %}<h2>Vehicle exit</h2>
<form method="get" class="row"><div><label for="n">Vehicle number</label><input id="n" name="number" value="{{ number }}" placeholder="GJ05AB1234" autofocus></div><div><button class="btn">Find vehicle</button></div></form>
{% if found %}<table><tr><th>Slot</th><th>Type</th><th>Time in</th><th>Hours</th><th>Rate per hour</th><th>Fee</th></tr>
<tr><td>{{ found.v.slot }}</td><td>{{ TYPES[found.v.type] }}</td><td>{{ found.v.time_in | dt }}</td><td>{{ found.hours }}</td><td>Rs. {{ found.rate }}</td><td><b>Rs. {{ found.fee }}</b></td></tr></table>
<p><form method="post" action="{{ url_for('exit_pay') }}" class="inline"><input type="hidden" name="number" value="{{ found.v.number }}"><button class="btn">Take fee and exit</button></form>
<form method="post" action="{{ url_for('exit_older') }}" class="inline"><input type="hidden" name="number" value="{{ found.v.number }}"><button class="btn alt">Make entry 1 hour older (for demo)</button></form></p>{% endif %}
{% if receipt %}<div class="ticket"><h3>Exit receipt</h3><p>Vehicle: {{ receipt.number }}</p><p>Slot: {{ receipt.slot }}</p><p>Time in: {{ receipt.time_in | dt }}</p>
<p>Time out: {{ receipt.time_out | dt }}</p><p>Hours charged: {{ receipt.hours }}</p><p>Amount paid: Rs. {{ receipt.fee }}</p><p class="noprint"><button class="btn alt" onclick="window.print()">Print</button></p></div>{% endif %}{% endblock %}""",
    "reports.html": """{% extends 'base.html' %}{% block body %}<h2>Reports</h2><div class="stats">
<div class="stat"><b>{{ stats.total }}</b>Total entries</div><div class="stat"><b>{{ stats.parked }}</b>Parked now</div>
<div class="stat"><b>{{ stats.exits }}</b>Exits</div><div class="stat"><b>Rs. {{ stats.income }}</b>Total income</div></div>
<h2>Daily usage and income</h2>{% if rows %}<table><tr><th>Vehicle</th><th>Type</th><th>Slot</th><th>Time in</th><th>Time out</th><th>Fee</th></tr>
{% for r in rows %}<tr><td>{{ r.number }}</td><td>{{ TYPES[r.type] }}</td><td>{{ r.slot }}</td><td>{{ r.time_in | dt }}</td><td>{{ r.time_out | dt if r.time_out else 'Parked' }}</td><td>{{ 'Rs. %d' % r.fee if r.time_out else '-' }}</td></tr>{% endfor %}</table>
{% else %}<p>No vehicles yet. Add one from the Entry tab.</p>{% endif %}{% endblock %}""",
    "settings.html": """{% extends 'base.html' %}{% block body %}<h2>Rates</h2>
<form method="post" action="{{ url_for('set_rates') }}" class="row"><div><label for="a">Two-wheeler (Rs. per hour)</label><input id="a" name="two" type="number" value="{{ rates.two }}"></div>
<div><label for="b">Car (Rs. per hour)</label><input id="b" name="four" type="number" value="{{ rates.four }}"></div><div><button class="btn">Save rates</button></div></form>
<h2>Add slot</h2><form method="post" action="{{ url_for('add_slot') }}" class="row"><div><select name="type">{% for k, v in TYPES.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select></div><div><button class="btn">Add slot</button></div></form>
<h2>Add user</h2><form method="post" action="{{ url_for('add_user') }}" class="row"><div><label for="u">Username</label><input id="u" name="username"></div><div><label for="p">Password</label><input id="p" name="password" type="password"></div>
<div><label for="r">Role</label><select id="r" name="role"><option>Operator</option><option>Administrator</option></select></div><div><button class="btn">Add user</button></div></form>
<p class="hint">Users now: {% for u in users %}{{ u.username }} ({{ u.role }}){{ ', ' if not loop.last }}{% endfor %}</p>{% endblock %}""",
}
app.jinja_loader = DictLoader(TEMPLATES)

if __name__ == "__main__":
    init_db()
    app.run(debug=False, port=5000)
