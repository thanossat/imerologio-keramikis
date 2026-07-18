#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scrape_calendar.py — διαβάζει τα ημερολόγια του Ceramics Now (χωρίς RSS)
και τροφοδοτεί το ΙΔΙΟ inbox.json με τα υπόλοιπα ευρήματα.

Η σελίδα δεν έχει feed· έχει όμως αυστηρά κανονική δομή, 4 γραμμές ανά event:

    [**Τίτλος**](url)
    *Κατηγορία*
    Ημερομηνίες
    Πόλη, ΧΩΡΑ                      (+ προαιρετικό «Applications are due …»)

ΑΣΦΑΛΕΙΑ — δύο δικλείδες:
  1. Αν βρει < MIN_EXPECTED events, ΔΕΝ γράφει τίποτα και σηκώνει συναγερμό.
     Έτσι μια αλλαγή στη διάταξη της σελίδας φαίνεται αμέσως, δεν μολύνει σιωπηλά.
  2. Τα ευρήματα μπαίνουν ως pending στο inbox — τα εγκρίνεις εσύ, όπως όλα.

Deduplication: ό,τι υπάρχει ήδη στο data.json (τα 39 χειροκίνητα) ή στο
seen.json ή στο inbox.json δεν ξαναμπαίνει. Ταυτότητα = ο σύνδεσμος.

Μόνο stdlib.  Έξοδος 2 = συναγερμός (το cron/notify το πιάνει).
"""

import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (X11; Linux aarch64) Gecko/20100101 Firefox/128.0"
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")

CALENDARS = [
    "https://www.ceramicsnow.org/calendar2026/",
    "https://www.ceramicsnow.org/calendar2027/",
]
MIN_EXPECTED = 10        # κάτω από αυτό → η σελίδα μάλλον άλλαξε → συναγερμός

COUNTRY = {
    "SPAIN": "ES", "SLOVENIA": "SI", "GREECE": "GR", "CHINA": "CN", "ITALY": "IT",
    "TAIWAN": "TW", "GERMANY": "DE", "AUSTRIA": "AT", "SWITZERLAND": "CH",
    "SOUTH KOREA": "KR", "BELGIUM": "BE", "FRANCE": "FR", "JAPAN": "JP",
    "INDIA": "IN", "UK": "UK", "US": "US", "USA": "US", "SINGAPORE": "SG",
    "AUSTRALIA": "AU", "PORTUGAL": "PT", "LATVIA": "LV", "NETHERLANDS": "NL",
}

CATEGORY = {
    "competition": "competition", "biennale": "biennale", "biennial": "biennale",
    "triennale": "biennale", "triennial": "biennale", "fair": "fair",
    "market": "fair", "festival": "festival", "conference": "conference",
}

MONTHS = {m: i + 1 for i, m in enumerate(
    "january february march april may june july august "
    "september october november december".split())}


# ---- ημερομηνίες: «May 8 – July 31, 2026», «October 2026», «Opens in December 2026»

def _mk(y, m, d):
    try:
        return datetime(y, m, d).strftime("%Y-%m-%d")
    except ValueError:
        return None


def parse_range(line):
    """Επιστρέφει (start, end, approx_end) ή (None, None, False)."""
    s = line.strip().lower().replace("–", "-").replace("—", "-")
    yrs = [int(y) for y in re.findall(r"\b(20\d{2})\b", s)]

    # «Month D - Month D, YYYY»  ή  «Month D-D, YYYY»
    m = re.search(r"([a-z]+)\s+(\d{1,2})\s*-\s*(?:([a-z]+)\s+)?(\d{1,2}),?\s*(\d{4})", s)
    if m and m.group(1) in MONTHS:
        y2 = int(m.group(5))
        m2 = MONTHS.get(m.group(3) or m.group(1), MONTHS[m.group(1)])
        # έτος έναρξης: αν διασχίζει χρονιά, το πρώτο yr· αλλιώς ίδιο
        y1 = yrs[0] if len(yrs) >= 2 else y2
        start = _mk(y1, MONTHS[m.group(1)], int(m.group(2)))
        end = _mk(y2, m2, int(m.group(4)))
        return start, end, False

    # «Month D, YYYY - Month D, YYYY» (χρονιά σε κάθε άκρο)
    m = re.search(r"([a-z]+)\s+(\d{1,2}),\s*(\d{4})\s*-\s*([a-z]+)\s+(\d{1,2}),\s*(\d{4})", s)
    if m and m.group(1) in MONTHS and m.group(4) in MONTHS:
        return (_mk(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))),
                _mk(int(m.group(6)), MONTHS[m.group(4)], int(m.group(5))), False)

    # «Month YYYY» / «Opens in Month YYYY» — μόνο μήνας, τέλος κατά προσέγγιση
    m = re.search(r"([a-z]+)\s+(\d{4})", s)
    if m and m.group(1) in MONTHS:
        y, mo = int(m.group(2)), MONTHS[m.group(1)]
        start = _mk(y, mo, 1)
        last = 31
        while last > 28 and _mk(y, mo, last) is None:
            last -= 1
        return start, _mk(y, mo, last), True

    return None, None, False


DUE = re.compile(r"applications?\s+are\s+due\s+(.+?)(?:$|\n)", re.I)
DUE_DATE = re.compile(r"([a-z]+)\s+(\d{1,2}),?\s+(\d{4})", re.I)


def parse_deadline(block):
    m = DUE.search(block)
    if not m:
        return None
    d = DUE_DATE.search(m.group(1))
    if d and d.group(1).lower() in MONTHS:
        return _mk(int(d.group(3)), MONTHS[d.group(1).lower()], int(d.group(2)))
    return None


# ---- εξαγωγή events από το markdown της σελίδας

ROW = re.compile(r"\[\*\*(.+?)\*\*\]\((https?://[^)]+)\)(.*?)(?=\[\*\*|\Z)", re.S)


def slug(s):
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48] or "item"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "replace")


def extract(md):
    out = []
    for m in ROW.finditer(md):
        title = html.unescape(m.group(1)).strip()
        url = m.group(2).strip()
        tail = [l.strip() for l in m.group(3).splitlines() if l.strip()]
        if not tail:
            continue

        cat = "competition"
        if tail and tail[0].startswith("*"):
            raw = tail[0].strip("*").lower()
            for kw, c in CATEGORY.items():
                if kw in raw:
                    cat = c
                    break

        start = end = None
        approx = False
        country = None
        city = ""
        for ln in tail[1:]:
            if re.search(r"\b20\d{2}\b", ln) and start is None:
                start, end, approx = parse_range(ln)
            up = ln.rsplit(",", 1)
            if len(up) == 2 and up[1].strip().upper() in COUNTRY:
                country = COUNTRY[up[1].strip().upper()]
                city = up[0].strip()

        out.append({
            "title": title, "url": url, "category": cat,
            "start": start, "end": end, "approx_end": approx,
            "deadline": parse_deadline(m.group(3)),
            "city": city, "country": country,
        })
    return out


def load(name, default):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    return json.load(open(p, encoding="utf-8"))


def main():
    found = []
    for url in CALENDARS:
        try:
            found += extract(fetch(url))
        except Exception as e:
            print(f"  !! {url}: {e}", file=sys.stderr)

    if len(found) < MIN_EXPECTED:
        # Δικλείδα 1: μην γράψεις τίποτα, σήκωσε συναγερμό.
        print(f"ΣΥΝΑΓΕΡΜΟΣ: βρέθηκαν μόνο {len(found)} events "
              f"(περίμενα ≥{MIN_EXPECTED}). Η σελίδα ίσως άλλαξε δομή. "
              f"Δεν γράφτηκε τίποτα.", file=sys.stderr)
        sys.exit(2)

    data = load("data.json", {"events": []})
    inbox = load("inbox.json", [])
    seen = set(load("seen.json", []))

    known = ({e.get("url") for e in data.get("events", [])}
             | {o.get("url") for o in data.get("opportunities", [])}
             | {i.get("link") for i in inbox} | seen)

    added = 0
    for ev in found:
        if not ev["url"] or ev["url"] in known:
            continue
        rec = {
            "status": "pending", "found": TODAY, "source": "cn-calendar",
            "kind": "open_call" if ev["deadline"] else "event",
            "title": ev["title"], "link": ev["url"],
            "deadline": ev["deadline"], "deadline_kind": "text" if ev["deadline"] else None,
            "eligibility_flags": [],
            "category": ev["category"], "city": ev["city"], "country": ev["country"],
        }
        if ev["start"] and ev["end"]:
            rec["start"], rec["end"], rec["approx_end"] = ev["start"], ev["end"], ev["approx_end"]
        inbox.append(rec)
        known.add(ev["url"])
        added += 1

    print(f"scraper: {len(found)} στη σελίδα, {added} νέα → inbox "
          f"({len(found) - added} ήδη γνωστά)")
    if added:
        json.dump(inbox, open(os.path.join(HERE, "inbox.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
