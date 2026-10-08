import os
import sqlite3
import uuid
from functools import wraps
from flask import (Flask, g, render_template, request, redirect,
                   url_for, session, flash, abort)
from seed import SEED
from werkzeug.security import generate_password_hash, check_password_hash

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "vyazanie.db")
UPLOADS = os.path.join(BASE, "static", "uploads")
ALLOWED = {"png", "jpg", "jpeg", "webp", "gif"}
LEVELS = ["Новичок", "Средний", "Опытный"]
CATEGORIES = ["Зайцы", "Мишки", "Коты и собаки", "Динозавры", "Другое"]

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-me-please")
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # фото до 5 МБ

SCHEMA = """
CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS patterns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    level TEXT NOT NULL DEFAULT 'Новичок',
    hours INTEGER NOT NULL DEFAULT 3,
    materials TEXT NOT NULL DEFAULT '',
    steps TEXT NOT NULL DEFAULT '',
    category_id INTEGER REFERENCES categories(id),
    image TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL, pattern_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, pattern_id));
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()

def init_db():
    os.makedirs(UPLOADS, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    # миграция со старой версии базы
    cols = [r[1] for r in db.execute("PRAGMA table_info(patterns)")]
    if "category_id" not in cols:
        db.execute("ALTER TABLE patterns ADD COLUMN category_id INTEGER")
    if "image" not in cols:
        db.execute("ALTER TABLE patterns ADD COLUMN image TEXT")
    if db.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        db.executemany("INSERT INTO categories (name) VALUES (?)", [(c,) for c in CATEGORIES])
    if db.execute("PRAGMA user_version").fetchone()[0] < 2:  # стартовые схемы обновляются один раз
        for t in SEED:
            row = db.execute("SELECT id FROM patterns WHERE title=?", (t[0],)).fetchone()
            if row:
                db.execute("UPDATE patterns SET summary=?, level=?, hours=?, materials=?, steps=?, "
                           "category_id=? WHERE id=?", t[1:] + (row[0],))
            else:
                db.execute("INSERT INTO patterns (title, summary, level, hours, materials, steps, category_id) "
                           "VALUES (?,?,?,?,?,?,?)", t)
        db.execute("PRAGMA user_version = 2")
    db.commit()
    db.close()

@app.before_request
def load_user():
    uid = session.get("uid")
    g.user = get_db().execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None

@app.context_processor
def inject():
    cats = get_db().execute("SELECT * FROM categories ORDER BY id").fetchall()
    return {"user": g.get("user"), "levels": LEVELS, "categories": cats}

def login_required(f):
    @wraps(f)
    def w(*a, **kw):
        if not g.user:
            flash("Сначала войдите в аккаунт")
            return redirect(url_for("login", next=request.path))
        return f(*a, **kw)
    return w

def admin_required(f):
    @wraps(f)
    @login_required
    def w(*a, **kw):
        if not g.user["is_admin"]:
            abort(403)
        return f(*a, **kw)
    return w

@app.template_filter("lines")
def lines(text):
    return [l.strip() for l in (text or "").splitlines() if l.strip()]

BASE_SELECT = ("SELECT p.*, c.name AS cat FROM patterns p "
               "LEFT JOIN categories c ON c.id = p.category_id ")

def get_pattern(pid):
    row = get_db().execute(BASE_SELECT + "WHERE p.id=?", (pid,)).fetchone()
    if not row:
        abort(404)
    return row

@app.route("/")
def index():
    q = request.args.get("q", "").strip()
    level = request.args.get("level", "")
    cat = request.args.get("cat", type=int)
    sql, args = BASE_SELECT + "WHERE 1=1", []
    if q:
        sql += " AND (p.title LIKE ? OR p.summary LIKE ? OR p.materials LIKE ?)"
        args += [f"%{q}%"] * 3
    if level in LEVELS:
        sql += " AND p.level = ?"
        args.append(level)
    if cat:
        sql += " AND p.category_id = ?"
        args.append(cat)
    sql += " ORDER BY p.created_at DESC, p.id DESC"
    items = get_db().execute(sql, args).fetchall()
    return render_template("index.html", items=items, q=q, level=level, cat=cat)

@app.route("/favorites")
@login_required
def favorites():
    items = get_db().execute(
        BASE_SELECT + "JOIN favorites f ON f.pattern_id = p.id WHERE f.user_id=? ORDER BY p.id DESC",
        (g.user["id"],)).fetchall()
    return render_template("index.html", items=items, title="Избранное", q="", level="", cat=None)

@app.route("/pattern/<int:pid>")
def pattern(pid):
    p = get_pattern(pid)
    db = get_db()
    comments = db.execute("SELECT c.*, u.username FROM comments c JOIN users u ON u.id=c.user_id "
                          "WHERE c.pattern_id=? ORDER BY c.id", (pid,)).fetchall()
    is_fav = bool(g.user and db.execute("SELECT 1 FROM favorites WHERE user_id=? AND pattern_id=?",
                                        (g.user["id"], pid)).fetchone())
    return render_template("pattern.html", p=p, comments=comments, is_fav=is_fav)

@app.route("/pattern/<int:pid>/fav", methods=["POST"])
@login_required
def fav(pid):
    get_pattern(pid)
    db = get_db()
    cur = db.execute("DELETE FROM favorites WHERE user_id=? AND pattern_id=?", (g.user["id"], pid))
    if cur.rowcount == 0:
        db.execute("INSERT INTO favorites (user_id, pattern_id) VALUES (?,?)", (g.user["id"], pid))
    db.commit()
    return redirect(url_for("pattern", pid=pid))

@app.route("/pattern/<int:pid>/comment", methods=["POST"])
@login_required
def comment(pid):
    get_pattern(pid)
    text = request.form.get("text", "").strip()[:1000]
    if text:
        db = get_db()
        db.execute("INSERT INTO comments (pattern_id, user_id, text) VALUES (?,?,?)",
                   (pid, g.user["id"], text))
        db.commit()
    return redirect(url_for("pattern", pid=pid) + "#comments")

@app.route("/comment/<int:cid>/delete", methods=["POST"])
@login_required
def delete_comment(cid):
    db = get_db()
    c = db.execute("SELECT * FROM comments WHERE id=?", (cid,)).fetchone()
    if not c:
        abort(404)
    if c["user_id"] != g.user["id"] and not g.user["is_admin"]:
        abort(403)
    db.execute("DELETE FROM comments WHERE id=?", (cid,))
    db.commit()
    return redirect(url_for("pattern", pid=c["pattern_id"]) + "#comments")

def save_image(f):
    if not f or not f.filename:
        return None
    ext = f.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED:
        flash("Фото не загружено: нужен файл jpg, png, webp или gif")
        return None
    name = uuid.uuid4().hex + "." + ext
    f.save(os.path.join(UPLOADS, name))
    return name

def read_form():
    f = request.form
    try:
        hours = max(1, int(f.get("hours", 3)))
    except ValueError:
        hours = 3
    level = f.get("level") if f.get("level") in LEVELS else LEVELS[0]
    return (f.get("title", "").strip(), f.get("summary", "").strip(), level, hours,
            f.get("materials", "").strip(), f.get("steps", "").strip(),
            f.get("category_id", type=int))

@app.route("/admin/new", methods=["GET", "POST"])
@admin_required
def new():
    if request.method == "POST":
        data = read_form()
        if not data[0]:
            flash("Укажите название схемы")
            return render_template("form.html", p=request.form, action="Добавить схему")
        db = get_db()
        cur = db.execute("INSERT INTO patterns (title, summary, level, hours, materials, steps, "
                         "category_id, image) VALUES (?,?,?,?,?,?,?,?)",
                         data + (save_image(request.files.get("image")),))
        db.commit()
        flash("Схема добавлена")
        return redirect(url_for("pattern", pid=cur.lastrowid))
    return render_template("form.html", p={}, action="Добавить схему")

@app.route("/admin/edit/<int:pid>", methods=["GET", "POST"])
@admin_required
def edit(pid):
    row = get_pattern(pid)
    if request.method == "POST":
        data = read_form()
        if not data[0]:
            flash("Укажите название схемы")
            return render_template("form.html", p=request.form, action="Сохранить")
        image = save_image(request.files.get("image")) or row["image"]
        db = get_db()
        db.execute("UPDATE patterns SET title=?, summary=?, level=?, hours=?, materials=?, steps=?, "
                   "category_id=?, image=? WHERE id=?", data + (image, pid))
        db.commit()
        flash("Изменения сохранены")
        return redirect(url_for("pattern", pid=pid))
    return render_template("form.html", p=row, action="Сохранить")

@app.route("/admin/delete/<int:pid>", methods=["POST"])
@admin_required
def delete(pid):
    get_pattern(pid)
    db = get_db()
    for t, col in (("comments", "pattern_id"), ("favorites", "pattern_id"), ("patterns", "id")):
        db.execute(f"DELETE FROM {t} WHERE {col}=?", (pid,))
    db.commit()
    flash("Схема удалена")
    return redirect(url_for("index"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("username", "").strip()
        pw = request.form.get("password", "")
        if len(name) < 3 or len(pw) < 4:
            flash("Имя: от 3 символов, пароль: от 4 символов")
        else:
            db = get_db()
            first = db.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0  # первый = админ
            try:
                cur = db.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?,?,?)",
                                 (name, generate_password_hash(pw), int(first)))
                db.commit()
            except sqlite3.IntegrityError:
                flash("Такое имя уже занято")
            else:
                session["uid"] = cur.lastrowid
                flash("Добро пожаловать!")
                return redirect(url_for("index"))
    return render_template("login.html", register=True)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = get_db().execute("SELECT * FROM users WHERE username=?",
                             (request.form.get("username", "").strip(),)).fetchone()
        if u and check_password_hash(u["password_hash"], request.form.get("password", "")):
            session["uid"] = u["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") else url_for("index"))
        flash("Неверное имя или пароль")
    return render_template("login.html", register=False)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))

@app.route("/basics")
def basics():
    return render_template("basics.html")

@app.route("/about")
def about():
    return render_template("about.html")

@app.route("/contacts")
def contacts():
    return render_template("contacts.html")

init_db()

if __name__ == "__main__":
    app.run(debug=True)
