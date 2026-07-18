#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
bot.py — ακούει τα πατήματα κουμπιών στο Telegram και εκτελεί.

Τρέχει ΣΥΝΕΧΩΣ ως systemd service (long polling — δεν χρειάζεται σταθερή IP).

  ✅ ok:<cid>  → status=confirmed, τρέχει merge_inbox.py, git push
  🗑 no:<cid>  → σβήνει την εγγραφή, μπαίνει στο seen.json

ΑΣΦΑΛΕΙΑ: δέχεται πατήματα ΜΟΝΟ από το δικό σου TELEGRAM_CHAT. Οποιοσδήποτε
άλλος βρει το bot, τα κουμπιά του δεν κάνουν τίποτα.

Μόνο stdlib.
"""

import json
import os
import subprocess
import time
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


E = env()
TOKEN, CHAT = E["TELEGRAM_TOKEN"], str(E["TELEGRAM_CHAT"])
API = f"https://api.telegram.org/bot{TOKEN}"


def call(method, payload=None, timeout=70):
    url = f"{API}/{method}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def answer(cbid, text):
    try:
        call("answerCallbackQuery", {"callback_query_id": cbid, "text": text})
    except Exception:
        pass


def edit(chat, mid, note):
    try:
        call("editMessageReplyMarkup", {"chat_id": chat, "message_id": mid,
                                        "reply_markup": {"inline_keyboard": [[{"text": note, "callback_data": "done"}]]}})
    except Exception:
        pass


def load_inbox():
    p = os.path.join(HERE, "inbox.json")
    return p, json.load(open(p, encoding="utf-8"))


def save_inbox(p, data):
    json.dump(data, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def find(inbox, cid):
    for it in inbox:
        if it.get("cid") == cid:
            return it
    return None


def run(cmd):
    return subprocess.run(cmd, cwd=HERE, capture_output=True, text=True)


def push(msg):
    """merge → commit → push. Επιστρέφει (ok, λεπτομέρεια)."""
    m = run(["python3", "merge_inbox.py"])
    if m.returncode != 0:
        return False, "merge σφάλμα: " + (m.stderr or m.stdout)[-200:]

    run(["git", "add", "data.json", "inbox.json", "seen.json"])
    diff = run(["git", "diff", "--cached", "--quiet"])
    if diff.returncode == 0:
        return True, "καμία αλλαγή στα δεδομένα"

    c = run(["git", "commit", "-m", msg])
    if c.returncode != 0:
        return False, "commit σφάλμα: " + (c.stderr or c.stdout)[-200:]
    p = run(["git", "push"])
    if p.returncode != 0:
        return False, "push σφάλμα: " + (p.stderr or p.stdout)[-200:]
    return True, "ναι"


def handle(cb):
    chat = str(cb["message"]["chat"]["id"])
    cbid = cb["id"]
    mid = cb["message"]["message_id"]
    data = cb.get("data", "")

    # --- ο φρουρός: μόνο εσύ
    if chat != CHAT:
        answer(cbid, "Δεν επιτρέπεται.")
        return

    if ":" not in data:
        answer(cbid, "ok")
        return

    action, cid = data.split(":", 1)
    p, inbox = load_inbox()
    it = find(inbox, cid)

    if it is None:
        answer(cbid, "Δεν βρέθηκε — ίσως το χειρίστηκες ήδη.")
        edit(chat, mid, "—")
        return

    if action == "no":
        seen_p = os.path.join(HERE, "seen.json")
        seen = set(json.load(open(seen_p)) if os.path.exists(seen_p) else [])
        seen.add(it["link"])
        json.dump(sorted(seen), open(seen_p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
        inbox = [x for x in inbox if x.get("cid") != cid]
        save_inbox(p, inbox)
        answer(cbid, "Απορρίφθηκε.")
        edit(chat, mid, "🗑 απορρίφθηκε")
        return

    if action == "ok":
        it["status"] = "confirmed"
        save_inbox(p, inbox)
        answer(cbid, "Εγκρίθηκε — ενημερώνω τη σελίδα…")
        ok, detail = push(f"telegram: έγκριση «{it['title'][:50]}»")
        edit(chat, mid, "✅ στη σελίδα" if ok else f"⚠️ {detail[:40]}")
        return


def main():
    print("bot ξεκίνησε — ακούω πατήματα…")
    offset = None
    while True:
        try:
            params = {"timeout": 60, "allowed_updates": ["callback_query"]}
            if offset is not None:
                params["offset"] = offset
            res = call("getUpdates", params, timeout=70)
        except Exception as e:
            print("polling σφάλμα:", e)
            time.sleep(5)
            continue

        for upd in res.get("result", []):
            offset = upd["update_id"] + 1
            if "callback_query" in upd:
                try:
                    handle(upd["callback_query"])
                except Exception as e:
                    print("handle σφάλμα:", e)


if __name__ == "__main__":
    main()
