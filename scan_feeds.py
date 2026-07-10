#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scan_feeds.py — Δόση 1: ΑΝΙΧΝΕΥΤΗΣ ΜΟΝΟ.

Διαβάζει RSS/Atom, φιλτράρει, και γράφει ΤΡΙΑ αρχεία:

    inbox.json     πέρασαν και τα τρία σκέλη  → περιμένουν έγκριση
    rejected.json  κόπηκαν, με τον λόγο       → παράθυρο επιθεώρησης (τελευταία 150)
    seen.json      αποτυπώματα ήδη ιδωμένων   → δεν επανέρχονται

ΔΕΝ γράφει στο data.json. ΔΕΝ κάνει commit. ΔΕΝ στέλνει email.

Μόνο stdlib. Καμία εγκατάσταση.

    python3 scan_feeds.py            # κανονικό τρέξιμο
    python3 scan_feeds.py --stats    # μόνο σύνοψη, χωρίς εγγραφή αρχείων
"""

import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (X11; Linux aarch64) Gecko/20100101 Firefox/128.0"
TIMEOUT = 25
REJECTED_WINDOW = 150

# Χρονικό παράθυρο: τρέχον έτος + επόμενο. Ό,τι παλαιότερο πάει κατευθείαν στο seen.
MAX_AGE_DAYS = 400
THIS_YEAR = datetime.now().year

# Τον πρώτο μήνα τα αρνητικά ΔΕΝ σβήνουν οριστικά: πάνε στο rejected.json
# ώστε να ελέγξεις ότι κόβουν σωστά. Γύρισέ το σε True όταν τα εμπιστευτείς.
NEGATIVES_ARE_FINAL = False

FEEDS = {
    "cnw":     "https://ceramicsnow.substack.com/feed",
    "artaxis": "https://artaxis.org/category/news-call-for-entries/feed/",
    "katlas":  "https://keramik-atlas.de/feed/",
    "otm":     "https://on-the-move.org/feed",
    "peka":    "https://enosi-keramiston-aggeioplaston.blogspot.com/feeds/posts/default?alt=rss&max-results=50",
}


# ---------------------------------------------------------------- κανονικοποίηση

def strip_accents(s: str) -> str:
    """Αφαιρεί τόνους και ενοποιεί το τελικό σίγμα. Χωρίς αυτό,
    το 'ΑΙΤΗΣΗ' δεν ταιριάζει ποτέ με το 'αίτηση'."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.replace("ς", "σ")


def norm(s: str) -> str:
    return strip_accents((s or "").lower())


def despace(s: str) -> str:
    """«Δ Ε Λ Τ Ι Ο  Τ Υ Π Ο Υ» → «δελτιοτυπου». Νικάει τα αραιωμένα κεφαλαία."""
    return re.sub(r"\s+", "", norm(s))


TAG = re.compile(r"<[^>]+>")


def untag(s: str) -> str:
    """HTML → σκέτο κείμενο. Τα feeds βάζουν HTML μέσα σε <description>."""
    return re.sub(r"\s+", " ", TAG.sub(" ", html.unescape(s or ""))).strip()


# ---------------------------------------------------------------- ανάγνωση feed

def text_of(el) -> str:
    """ΠΡΟΣΟΧΗ: το On the Move βάζει <a> μέσα στο <title>.
    Το .text θα επέστρεφε μόνο '[News] '. Το itertext() τα μαζεύει όλα."""
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip()


def child(item, name):
    for c in item:
        if c.tag.split("}")[-1] == name:
            return c
    return None


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    raw = urllib.request.urlopen(req, timeout=TIMEOUT).read()
    # Αμυντικά: BOM ή κενά πριν το <?xml σπάνε τον parser (keramik-atlas).
    raw = raw.lstrip(b"\xef\xbb\xbf").lstrip()
    if b"<?xml" in raw[:200]:
        raw = raw[raw.index(b"<?xml"):]
    return ET.fromstring(raw)


def items_of(root):
    for el in root.iter():
        if el.tag.split("}")[-1] in ("item", "entry"):
            yield el


def parse_item(el, source):
    t = child(el, "title")
    title = text_of(t) if t is not None else ""
    title = re.sub(r"^\[[^\]]{1,12}\]\s*", "", title)   # On the Move: «[News] …»

    body = raw = ""
    for tag in ("description", "content", "summary", "encoded"):
        c = child(el, tag)
        if c is not None and (c.text or len(c)):
            raw = html.unescape(text_of(c))   # HTML ακέραιο: κρατά τα datetime="…"
            body = untag(text_of(c))          # καθαρό κείμενο: για ανάγνωση
            break

    link = ""
    l = child(el, "link")
    if l is not None:
        link = (l.text or l.get("href") or "").strip()

    d = None
    for tag in ("pubDate", "published", "updated"):
        d = child(el, tag)
        if d is not None:          # ΠΟΤΕ σκέτο `or`: Element χωρίς παιδιά είναι falsy
            break
    pub = (d.text if d is not None and d.text else "").strip()

    # Μερικές αναρτήσεις της ΠΕΚΑ δεν έχουν καθόλου τίτλο.
    if not title and body:
        title = body[:70].rstrip() + "…"

    return {"source": source, "title": title, "link": link,
            "published": pub, "body": body[:4000], "raw": raw[:6000]}


# ---------------------------------------------------------------- προθεσμίες

ISO = re.compile(r'datetime="(\d{4}-\d{2}-\d{2})')          # On the Move: μηχανικά αναγνώσιμο
DEADLINE = re.compile(r"deadline:?\s*([^.<|;]{4,40})", re.I)  # Ceramics Now: μέσα στο κείμενο


def find_deadline(raw_body: str, clean_body: str):
    m = ISO.search(raw_body)
    if m:
        return m.group(1), "iso"
    m = DEADLINE.search(clean_body)
    if m:
        return m.group(1).strip(), "text"
    return None, None


# ---------------------------------------------------------------- ηλικία

YEAR = re.compile(r"\b(20\d{2})\b")


def too_old(item) -> str:
    """Επιστρέφει λόγο αν είναι παρελθόν, αλλιώς "". Δύο ανεξάρτητα μανταλάκια."""
    if item["published"]:
        try:
            d = parsedate_to_datetime(item["published"])
            if d.tzinfo is None:
                d = d.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - d).days
            if age > MAX_AGE_DAYS:
                return f"δημοσιεύτηκε πριν {age} μέρες"
        except Exception:
            pass

    # Έτος ΜΟΝΟ από τον τίτλο. Στο σώμα υπάρχουν αρχεία παλιών ετών.
    years = [int(y) for y in YEAR.findall(item["title"])]
    if years and max(years) < THIS_YEAR:
        return f"τίτλος αναφέρει μόνο {max(years)}"
    return ""


# ---------------------------------------------------------------- φίλτρο

def hits(text: str, terms) -> list:
    return [t for t in terms if norm(t) in text]


def classify(item, kw):
    """Καλάθια: pass / reject / noise / old. Επιστρέφει (bucket, reason, matches)."""
    t = norm(item["title"])
    t_ds = despace(item["title"])
    full = norm(item["title"] + " " + item["body"])

    # 1. Σκληρά αρνητικά — βιομηχανική/επιστημονική κεραμική. Σε ΟΛΟ το κείμενο.
    bad = hits(full, kw["negative_hard"])
    if bad:
        return "noise", f"σκληρό αρνητικό: {bad[0]}", {}

    # 2. Συντακτικά αρνητικά — ΜΟΝΟ στον τίτλο, και στην αραιωμένη εκδοχή του.
    bad = [x for x in kw["negative_title"] if norm(x) in t or despace(x) in t_ds]
    if bad:
        return "noise", f"αρνητικό στον τίτλο: {bad[0]}", {}

    # 3. Υλικό: οπουδήποτε.
    mat = hits(full, kw["material"])
    if not mat:
        return "reject", "λείπει όρος υλικού", {}

    # 4. Ευκαιρία ή εκδήλωση: ΣΤΟΝ ΤΙΤΛΟ.
    opp = hits(t, kw["opportunity"])
    evt = hits(t, kw["event"])

    # Εξαίρεση: το Ceramics Now κρύβει τις προθεσμίες στο σώμα.
    if not opp and BODY_DEADLINE.search(item["raw"]):
        opp = ["deadline (στο σώμα)"]

    if not opp and not evt:
        return "reject", "λείπει όρος ευκαιρίας/εκδήλωσης στον τίτλο", {"material": mat[:4]}

    kind = "open_call" if opp else "event"
    return "pass", kind, {"material": mat[:4], "opportunity": opp[:4], "event": evt[:4]}


BODY_DEADLINE = re.compile(r'deadline\s*:|datetime="\d{4}-', re.I)


# ---------------------------------------------------------------- αποθήκευση

def load(name, default):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    p = os.path.join(HERE, name)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def key_of(item):
    """Ταυτότητα = link. ΠΟΤΕ guid: του On the Move είναι κατεστραμμένο."""
    return item["link"] or (item["source"] + "::" + item["title"])


# ---------------------------------------------------------------- κυρίως

def main():
    stats_only = "--stats" in sys.argv
    kw = load("keywords.json", None)
    if kw is None:
        sys.exit("Λείπει το keywords.json δίπλα στο script.")
    kw = {k: v for k, v in kw.items() if not k.startswith("_")}

    seen = set(load("seen.json", []))
    inbox = load("inbox.json", [])
    known = {i["link"] for i in inbox}

    rejected, tally = [], {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    for src, url in FEEDS.items():
        try:
            root = fetch(url)
        except Exception as e:
            print(f"  !! {src}: {e}", file=sys.stderr)
            tally[src] = "ΣΦΑΛΜΑ"
            continue

        c = {"pass": 0, "reject": 0, "noise": 0, "old": 0, "seen": 0}

        for el in items_of(root):
            item = parse_item(el, src)
            k = key_of(item)

            if k in seen or k in known:
                c["seen"] += 1
                continue

            reason = too_old(item)
            if reason:
                c["old"] += 1
                seen.add(k)          # γεγονός, όχι κρίση — δεν χρειάζεται επιθεώρηση
                continue

            bucket, reason, matches = classify(item, kw)
            c[bucket] += 1

            if bucket == "pass":
                dl, dl_kind = find_deadline(item["raw"], item["body"])
                flags = hits(norm(item["body"]), kw["eligibility_flags"])
                inbox.append({
                    "status": "pending", "kind": reason, "found": now,
                    "source": src, "title": item["title"], "link": item["link"],
                    "published": item["published"],
                    "deadline": dl, "deadline_kind": dl_kind,
                    "eligibility_flags": flags,
                    "matched": matches,
                    "excerpt": item["body"][:280],
                })
                known.add(item["link"])

            elif bucket == "noise" and NEGATIVES_ARE_FINAL:
                seen.add(k)

            else:  # reject, ή noise όσο NEGATIVES_ARE_FINAL == False
                rejected.append({
                    "source": src, "title": item["title"], "link": item["link"],
                    "cut_by": reason, "matched": matches, "found": now,
                })

        tally[src] = c

    # --- σύνοψη
    print(f"\n{'πηγή':<10} {'πέρασαν':>8} {'κόπηκαν':>8} {'θόρυβος':>8} {'παλιά':>7}")
    print("-" * 46)
    for src, c in tally.items():
        if c == "ΣΦΑΛΜΑ":
            print(f"{src:<10} {'ΣΦΑΛΜΑ':>8}")
            continue
        print(f"{src:<10} {c['pass']:>8} {c['reject']:>8} {c['noise']:>8} {c['old']:>7}")

    pend = [i for i in inbox if i["status"] == "pending"]
    print(f"\ninbox: {len(pend)} σε αναμονή   rejected: {len(rejected)}   seen: {len(seen)}")

    if stats_only:
        print("(--stats: δεν γράφτηκε τίποτα)")
        return

    save("inbox.json", inbox)
    save("rejected.json", rejected[-REJECTED_WINDOW:])
    save("seen.json", sorted(seen))
    print("γράφτηκαν: inbox.json, rejected.json, seen.json")


if __name__ == "__main__":
    main()
