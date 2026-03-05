from flask import Flask, request, jsonify, send_from_directory
import sqlite3, time, hashlib, re

app = Flask(__name__)
DB = "confessions.db"
BAD_WORDS = ["sex","nude","kill","hate","fuck"]

# ---------------- DB ----------------

def get_db():
    con = sqlite3.connect(DB, timeout=5, check_same_thread=False)
    con.execute("PRAGMA journal_mode=WAL;")
    return con

def init_db():
    with get_db() as con:
        cur = con.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS confessions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT,
            timestamp INTEGER,
            reports INTEGER DEFAULT 0,
            ip_hash TEXT
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS reports(
            confession_id INTEGER,
            ip_hash TEXT,
            UNIQUE(confession_id, ip_hash)
        )
        """)

        cur.execute("""
        CREATE TABLE IF NOT EXISTS reactions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            confession_id INTEGER,
            ip_hash TEXT,
            emoji TEXT,
            UNIQUE(confession_id, ip_hash)
        )
        """)

        con.commit()

init_db()

# ---------------- HELPERS ----------------

def hash_ip(ip):
    return hashlib.sha256(ip.encode()).hexdigest()

def filter_text(text):
    if len(text) > 300:
        return False
    for w in BAD_WORDS:
        if w in text.lower():
            return False
    if re.search(r"\d{10}", text):
        return False
    return True

# ---------------- ROUTES ----------------

@app.route("/")
def index():
    return send_from_directory(".", "index.html")

# ---------------- CONFESS ----------------

@app.route("/confess", methods=["POST"])
def confess():
    data = request.json
    text = data.get("text","").strip()
    ip = hash_ip(request.remote_addr)
    now = int(time.time())

    if not filter_text(text):
        return jsonify({"error":"Rejected"}),400

    with get_db() as con:
        cur = con.cursor()

        # cooldown 5 min
        cur.execute("SELECT timestamp FROM confessions WHERE ip_hash=? ORDER BY timestamp DESC LIMIT 1",(ip,))
        row = cur.fetchone()
        if row and now-row[0] < 300:
            return jsonify({"error":"Cooldown active"}),429

        cur.execute(
            "INSERT INTO confessions(text,timestamp,ip_hash) VALUES(?,?,?)",
            (text,now,ip)
        )
        con.commit()

    return jsonify({"status":"posted"})

# ---------------- FEED ----------------

@app.route("/feed")
def feed():
    now = int(time.time())

    with get_db() as con:
        cur = con.cursor()

        # cleanup old (>24h)
        cur.execute("DELETE FROM confessions WHERE timestamp < ?",(now-86400,))
        cur.execute("DELETE FROM reactions WHERE confession_id NOT IN (SELECT id FROM confessions)")
        cur.execute("DELETE FROM reports WHERE confession_id NOT IN (SELECT id FROM confessions)")
        con.commit()

        cur.execute("""
        SELECT 
            c.id,
            c.text,
            c.timestamp,
            c.reports,
            SUM(CASE WHEN r.emoji='🥳' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.emoji='🔥' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.emoji='👍' THEN 1 ELSE 0 END),
            SUM(CASE WHEN r.emoji='👎' THEN 1 ELSE 0 END)
        FROM confessions c
        LEFT JOIN reactions r ON c.id = r.confession_id
        GROUP BY c.id
        ORDER BY c.id DESC
        """)

        rows = cur.fetchall()

    return jsonify(rows)

# ---------------- REPORT ----------------

@app.route("/report/<int:id>", methods=["POST"])
def report(id):
    ip = hash_ip(request.remote_addr)

    with get_db() as con:
        cur = con.cursor()
        try:
            cur.execute(
                "INSERT INTO reports(confession_id, ip_hash) VALUES(?,?)",
                (id,ip)
            )
        except:
            return jsonify({"error":"Already reported"}),400

        cur.execute("UPDATE confessions SET reports=reports+1 WHERE id=?",(id,))
        cur.execute("SELECT reports FROM confessions WHERE id=?",(id,))
        r = cur.fetchone()

        if r and r[0] >= 3:
            cur.execute("DELETE FROM confessions WHERE id=?",(id,))
            cur.execute("DELETE FROM reports WHERE confession_id=?",(id,))
            cur.execute("DELETE FROM reactions WHERE confession_id=?",(id,))

        con.commit()

    return jsonify({"status":"reported"})

# ---------------- REACT ----------------
@app.route("/react/<int:id>", methods=["POST"])
def react(id):
    ip = hash_ip(request.remote_addr)
    emoji = request.json.get("emoji")

    if emoji not in ["🥳","🔥","👍","👎"]:
        return jsonify({"error":"Invalid"}),400

    with get_db() as con:
        cur = con.cursor()

        cur.execute("SELECT emoji FROM reactions WHERE confession_id=? AND ip_hash=?",(id,ip))
        old = cur.fetchone()

        if old:
            if old[0]==emoji:
                cur.execute("DELETE FROM reactions WHERE confession_id=? AND ip_hash=?",(id,ip))
                user=None
            else:
                cur.execute("UPDATE reactions SET emoji=? WHERE confession_id=? AND ip_hash=?",(emoji,id,ip))
                user=emoji
        else:
            cur.execute("INSERT INTO reactions(confession_id,ip_hash,emoji) VALUES(?,?,?)",(id,ip,emoji))
            user=emoji

        con.commit()

        cur.execute("""
        SELECT 
          SUM(CASE WHEN emoji='🥳' THEN 1 ELSE 0 END),
          SUM(CASE WHEN emoji='🔥' THEN 1 ELSE 0 END),
          SUM(CASE WHEN emoji='👍' THEN 1 ELSE 0 END),
          SUM(CASE WHEN emoji='👎' THEN 1 ELSE 0 END)
        FROM reactions WHERE confession_id=?
        """,(id,))
        r=cur.fetchone()

    return jsonify({"🥳":r[0] or 0,"🔥":r[1] or 0,"👍":r[2] or 0,"👎":r[3] or 0,"user":user})

# ---------------- RUN ----------------

if __name__=="__main__":
    app.run(debug=True)