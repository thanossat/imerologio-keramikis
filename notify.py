#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify.py — στέλνει στο Telegram τα pending ευρήματα ως κάρτες με κουμπιά.

Τρέχει μετά τον σαρωτή. Στέλνει ΜΟΝΟ όσα δεν έχουν σταλεί ήδη
(σημαδεμένα με "notified": true στο inbox.json).

    ✅ Έγκριση  → callback  ok:<link>
    🗑 Απόρριψη → callback  no:<link>

Το bot.py ακούει αυτά τα callbacks. Μόνο stdlib.
"""

import hashlib
import json
import os
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def env(path=os.path.expanduser("~/.config/keramika/env")):
    e = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                e[k] = v
    return e


def api(token, method, payload):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def card(it):
    src = it.get("source", "—")
    kind = {"open_call": "open call", "event": "εκδήλωση"}.get(it.get("kind"), it.get("kind", ""))
    lines = [f"<b>{esc(it['title'])}</b>", f"<i>{kind} · {src}</i>"]
    if it.get("deadline"):
        lines.append(f"⏳ προθεσμία: {it['deadline']}")
    if it.get("eligibility_flags"):
        lines.append("⚠️ " + ", ".join(it["eligibility_flags"]))
    if it.get("link"):
        lines.append(esc(it["link"]))
    if it.get("excerpt"):
        lines.append("\n" + esc(it["excerpt"][:200]))
    return "\n".join(lines)


def main():
    e = env()
    token, chat = e["TELEGRAM_TOKEN"], e["TELEGRAM_CHAT"]

    p = os.path.join(HERE, "inbox.json")
    inbox = json.load(open(p, encoding="utf-8"))

    to_send = [i for i in inbox
               if i.get("status") == "pending" and not i.get("notified")]
    if not to_send:
        print("Τίποτα νέο για αποστολή.")
        return

    for it in to_send:
        # callback_data ≤ 64 bytes → σταθερό κοντό id, όχι το URL.
        it["cid"] = hashlib.sha1(it["link"].encode()).hexdigest()[:12]
        buttons = {"inline_keyboard": [[
            {"text": "✅ Έγκριση", "callback_data": f"ok:{it['cid']}"},
            {"text": "🗑 Απόρριψη", "callback_data": f"no:{it['cid']}"},
        ]]}
        api(token, "sendMessage", {
            "chat_id": chat, "text": card(it),
            "parse_mode": "HTML", "disable_web_page_preview": True,
            "reply_markup": buttons,
        })
        it["notified"] = True

    json.dump(inbox, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"Στάλθηκαν {len(to_send)} κάρτες στο Telegram.")


if __name__ == "__main__":
    main()
