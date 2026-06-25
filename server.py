"""
Nyx Labs — backend для формы заявок и чата.

Что делает:
  1. /api/lead — принимает заявку с сайта (имя + телефон/почта/телеграм + задача)
     и пересылает её тебе в Telegram. В сообщении явно подписано, через какой
     канал клиент ждёт ответа, чтобы было сразу понятно — звонить, писать на
     почту или в Telegram.
  2. /api/chat/* — простой живой чат на сайте: посетитель пишет сообщение,
     ты получаешь уведомление в Telegram и отвечаешь через админ-страницу
     /api/chat/admin?key=ТВОЙ_КЛЮЧ — ответ сразу появляется у посетителя на сайте.

Локальный запуск (для проверки):
    pip install -r requirements.txt
    export BOT_TOKEN=полученный_токен_бота      (Windows: set BOT_TOKEN=...)
    export CHAT_ID=твой_chat_id
    export ADMIN_KEY=придумай_свой_пароль
    python server.py

Как получить BOT_TOKEN и CHAT_ID:
    1. В Telegram открой @BotFather -> /newbot -> следуй инструкциям -> получишь токен.
    2. Напиши своему новому боту любое сообщение (просто "привет").
    3. Открой в браузере:
       https://api.telegram.org/bot<ТВОЙ_ТОКЕН>/getUpdates
       В ответе найди "chat":{"id": ЦИФРЫ ...} — это и есть CHAT_ID.

ADMIN_KEY — любой свой пароль для доступа к /api/chat/admin. Без него
кто угодно, кто угадает адрес, увидит переписку — обязательно задай свой.

Деплой (бесплатно): Render.com — заливаешь все файлы, в настройках
сервиса прописываешь переменные окружения BOT_TOKEN, CHAT_ID, ADMIN_KEY
(НЕ хардкодь их в коде, особенно если код лежит в публичном репозитории
на GitHub). После деплоя вставь полученный адрес в index.html:
переменные LEAD_ENDPOINT и CHAT_ENDPOINT.

ВАЖНО про бесплатный тариф Render: сервис "засыпает" без запросов и
файлы на диске (включая базу chat.db ниже) стираются при каждом
перезапуске/редеплое. Для теста — нормально. Когда появятся реальные
клиенты и важно не терять историю чата — нужен платный тариф с
постоянным диском (от $7/мес) или внешняя бесплатная база данных.

ВАЖНО: точные шаги/бесплатные тарифы у хостингов могут поменяться —
сверься с актуальной документацией площадки на момент деплоя.
"""

import os
import sqlite3
import time
from flask import Flask, request, jsonify, g
import requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)  # разрешаем запросы с домена твоего сайта

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHAT_ID = os.environ.get("CHAT_ID")
ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
DB_PATH = os.environ.get("DB_PATH", "chat.db")


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            sender TEXT NOT NULL,
            name TEXT,
            text TEXT NOT NULL,
            created_at REAL NOT NULL
        )"""
    )
    conn.commit()
    conn.close()


def notify_telegram(text):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=8,
        )
    except requests.RequestException:
        pass  # уведомление не критично — сообщение всё равно сохранится в базе


@app.route("/api/lead", methods=["POST"])
def lead():
    data = request.get_json(force=True, silent=True) or {}
    name = (data.get("name") or "—").strip()[:200]
    phone = (data.get("phone") or "").strip()[:100]
    email = (data.get("email") or "").strip()[:150]
    telegram = (data.get("telegram") or "").strip()[:100]
    message = (data.get("message") or "—").strip()[:1000]

    if not phone and not email and not telegram:
        return jsonify({"ok": False, "error": "Не указан ни один способ связи"}), 400

    if not BOT_TOKEN or not CHAT_ID:
        return jsonify({"ok": False, "error": "BOT_TOKEN/CHAT_ID не настроены на сервере"}), 500

    contact_lines = []
    if telegram:
        contact_lines.append(f"💬 Telegram: {telegram} — напиши там")
    if phone:
        contact_lines.append(f"📞 Телефон: {phone} — можно позвонить")
    if email:
        contact_lines.append(f"📧 Email: {email} — напиши на почту")

    text = (
        "🆕 Новая заявка с сайта Nyx Labs\n\n"
        f"Имя: {name}\n"
        + "\n".join(contact_lines)
        + f"\n\nЗадача: {message}"
    )

    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text},
            timeout=8,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": str(e)}), 502

    return jsonify({"ok": True})


@app.route("/api/chat/send", methods=["POST"])
def chat_send():
    data = request.get_json(force=True, silent=True) or {}
    session_id = (data.get("session_id") or "").strip()[:64]
    name = (data.get("name") or "Гость").strip()[:100]
    text = (data.get("text") or "").strip()[:2000]
    if not session_id or not text:
        return jsonify({"ok": False, "error": "session_id и text обязательны"}), 400

    db = get_db()
    db.execute(
        "INSERT INTO messages(session_id, sender, name, text, created_at) VALUES (?,?,?,?,?)",
        (session_id, "visitor", name, text, time.time()),
    )
    db.commit()

    admin_url = request.url_root.rstrip("/") + f"/api/chat/admin?key={ADMIN_KEY}&session={session_id}"
    notify_telegram(f"💬 Новое сообщение в чате от {name}:\n{text}\n\nОтветить: {admin_url}")

    return jsonify({"ok": True})


@app.route("/api/chat/poll", methods=["GET"])
def chat_poll():
    session_id = (request.args.get("session_id") or "").strip()[:64]
    after_id = int(request.args.get("after_id") or 0)
    if not session_id:
        return jsonify({"ok": False, "error": "session_id обязателен"}), 400

    db = get_db()
    rows = db.execute(
        "SELECT id, sender, name, text, created_at FROM messages "
        "WHERE session_id=? AND id>? ORDER BY id ASC",
        (session_id, after_id),
    ).fetchall()
    return jsonify({"ok": True, "messages": [dict(r) for r in rows]})


def check_admin(key):
    return ADMIN_KEY and key == ADMIN_KEY


@app.route("/api/chat/admin/sessions", methods=["GET"])
def chat_admin_sessions():
    if not check_admin(request.args.get("key")):
        return jsonify({"ok": False, "error": "Неверный key"}), 403
    db = get_db()
    rows = db.execute(
        """SELECT session_id, MAX(created_at) AS last_at,
                  (SELECT text FROM messages m2 WHERE m2.session_id = m1.session_id
                   ORDER BY id DESC LIMIT 1) AS last_text,
                  (SELECT name FROM messages m2 WHERE m2.session_id = m1.session_id
                   AND sender='visitor' ORDER BY id DESC LIMIT 1) AS name
           FROM messages m1 GROUP BY session_id ORDER BY last_at DESC"""
    ).fetchall()
    return jsonify({"ok": True, "sessions": [dict(r) for r in rows]})


@app.route("/api/chat/admin/messages", methods=["GET"])
def chat_admin_messages():
    if not check_admin(request.args.get("key")):
        return jsonify({"ok": False, "error": "Неверный key"}), 403
    session_id = (request.args.get("session_id") or "").strip()[:64]
    db = get_db()
    rows = db.execute(
        "SELECT id, sender, name, text, created_at FROM messages "
        "WHERE session_id=? ORDER BY id ASC",
        (session_id,),
    ).fetchall()
    return jsonify({"ok": True, "messages": [dict(r) for r in rows]})


@app.route("/api/chat/admin/reply", methods=["POST"])
def chat_admin_reply():
    data = request.get_json(force=True, silent=True) or {}
    if not check_admin(data.get("key")):
        return jsonify({"ok": False, "error": "Неверный key"}), 403
    session_id = (data.get("session_id") or "").strip()[:64]
    text = (data.get("text") or "").strip()[:2000]
    if not session_id or not text:
        return jsonify({"ok": False, "error": "session_id и text обязательны"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO messages(session_id, sender, name, text, created_at) VALUES (?,?,?,?,?)",
        (session_id, "admin", "Nyx Labs", text, time.time()),
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/api/chat/admin", methods=["GET"])
def chat_admin_page():
    key = request.args.get("key", "")
    if not check_admin(key):
        return "Неверный или отсутствующий ?key=", 403
    # простая однофайловая админка без отдельных шаблонов
    return f"""<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Nyx Labs — чат</title>
<style>
  body{{font-family:-apple-system,Segoe UI,Roboto,sans-serif;margin:0;display:flex;height:100vh;background:#f6f7f9}}
  .sidebar{{width:280px;background:#fff;border-right:1px solid #e6e8eb;overflow-y:auto;flex:none}}
  .sess{{padding:14px 16px;border-bottom:1px solid #eef0f2;cursor:pointer}}
  .sess:hover{{background:#f6f7f9}}
  .sess b{{display:block;font-size:13.5px}}
  .sess small{{color:#8a9097;font-size:12px}}
  .main{{flex:1;display:flex;flex-direction:column}}
  .log{{flex:1;overflow-y:auto;padding:20px}}
  .msg{{max-width:60%;margin-bottom:10px;padding:9px 13px;border-radius:12px;font-size:14px;line-height:1.4}}
  .msg.visitor{{background:#fff;border:1px solid #e6e8eb}}
  .msg.admin{{background:#15181d;color:#fff;margin-left:auto}}
  .bar{{display:flex;gap:8px;padding:14px;border-top:1px solid #e6e8eb;background:#fff}}
  .bar input{{flex:1;padding:10px 12px;border:1px solid #d8dbe0;border-radius:10px;font-size:14px}}
  .bar button{{padding:10px 18px;border:none;border-radius:10px;background:#15181d;color:#fff;font-weight:600;cursor:pointer}}
</style></head><body>
  <div class="sidebar" id="sidebar">Загрузка…</div>
  <div class="main">
    <div class="log" id="log">Выберите диалог слева</div>
    <div class="bar"><input id="inp" placeholder="Ответ клиенту…"><button onclick="send()">Отправить</button></div>
  </div>
<script>
  const KEY = {key!r};
  let current = null;
  async function loadSessions(){{
    const r = await fetch('/api/chat/admin/sessions?key='+encodeURIComponent(KEY));
    const d = await r.json();
    if(!d.ok) return;
    document.getElementById('sidebar').innerHTML = d.sessions.map(s=>
      `<div class="sess" onclick="openSession('${{s.session_id}}')"><b>${{s.name||'Гость'}}</b><small>${{(s.last_text||'').slice(0,40)}}</small></div>`
    ).join('') || '<div style="padding:16px;color:#8a9097">Пока пусто</div>';
  }}
  async function openSession(id){{
    current = id;
    const r = await fetch('/api/chat/admin/messages?key='+encodeURIComponent(KEY)+'&session_id='+encodeURIComponent(id));
    const d = await r.json();
    if(!d.ok) return;
    document.getElementById('log').innerHTML = d.messages.map(m=>
      `<div class="msg ${{m.sender}}">${{m.text}}</div>`
    ).join('');
  }}
  async function send(){{
    const inp = document.getElementById('inp');
    if(!current || !inp.value.trim()) return;
    await fetch('/api/chat/admin/reply',{{method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{key:KEY,session_id:current,text:inp.value.trim()}})}});
    inp.value='';
    openSession(current);
  }}
  document.getElementById('inp').addEventListener('keydown',e=>{{if(e.key==='Enter')send();}});
  loadSessions();
  setInterval(()=>{{loadSessions(); if(current) openSession(current);}}, 4000);
</script>
</body></html>"""


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "Nyx Labs backend работает"})


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))

