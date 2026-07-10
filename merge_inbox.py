#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_inbox.py — Δόση 2: ΣΥΓΧΩΝΕΥΣΗ.

Καθαρός μετασχηματισμός αρχείων. Κανένα δίκτυο, καμία εικασία.

    inbox.json  (status: confirmed)  →  data.json
    inbox.json  (status: pending)    →  μένει
    ό,τι έσβησες από το inbox        →  seen.json  (δεν επανέρχεται)

Έχει διάρκεια (start + end)  → events        → μπάρα στο Gantt
Έχει μόνο προθεσμία          → opportunities → λίστα προθεσμιών

Τρέχει από GitHub Action όποτε αλλάξει το inbox.json.
Τοπικός έλεγχος:  python3 merge_inbox.py --dry-run
"""

import json
import os
import re
import sys
import unicodedata
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load(name, default=None):
    p = os.path.join(HERE, name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def save(name, obj):
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


# Χωρίς αυτό, το «Προκήρυξη Διαγωνισμού 63ης…» γίνεται id «63».
GR2LAT = str.maketrans({
    "α":"a","β":"v","γ":"g","δ":"d","ε":"e","ζ":"z","η":"i","θ":"th","ι":"i",
    "κ":"k","λ":"l","μ":"m","ν":"n","ξ":"x","ο":"o","π":"p","ρ":"r",
    "σ":"s","ς":"s","τ":"t","υ":"y","φ":"f","χ":"ch","ψ":"ps","ω":"o",
})


def slug(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.translate(GR2LAT)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:48] or "item"


def unique(base, taken):
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def main():
    dry = "--dry-run" in sys.argv

    data = load("data.json")
    if data is None:
        sys.exit("Λείπει το data.json.")
    inbox = load("inbox.json", [])
    seen = set(load("seen.json", []))

    data.setdefault("opportunities", [])
    events, opps = data["events"], data["opportunities"]
    taken = {e["id"] for e in events} | {o["id"] for o in opps}

    # Ό,τι υπήρχε στο seen αλλά έφυγε από το inbox, το έσβησες εσύ. Μένει σβησμένο.
    confirmed = [i for i in inbox if i.get("status") == "confirmed"]
    pending = [i for i in inbox if i.get("status") != "confirmed"]

    added_e = added_o = 0
    for it in confirmed:
        rec_id = unique(slug(it["title"]), taken)
        taken.add(rec_id)
        seen.add(it["link"])

        common = {
            "id": rec_id,
            "title": it["title"].strip(),
            "url": it.get("link") or None,
            "source": it.get("source", "scanner"),
            "origin": "scanner",           # ← το σιελ στη σελίδα κρέμεται από αυτό
            "added": TODAY,
        }
        if it.get("deadline"):
            common["deadline"] = it["deadline"]
        if it.get("eligibility_flags"):
            common["eligibility"] = it["eligibility_flags"]

        if it.get("start") and it.get("end"):
            # Του έδωσες ημερομηνίες με το χέρι → μπαίνει στο Gantt.
            events.append({**common,
                           "kind": "event",
                           "category": it.get("category", "competition"),
                           "start": it["start"], "end": it["end"],
                           "city": it.get("city", ""), "country": it.get("country")})
            added_e += 1
        else:
            opps.append({**common, "kind": it.get("kind", "open_call")})
            added_o += 1

    if not confirmed:
        print("Καμία εγκεκριμένη εγγραφή. Τίποτα να συγχωνευτεί.")
        return

    events.sort(key=lambda e: (e["start"], e["title"]))
    opps.sort(key=lambda o: (o.get("deadline") or "9999", o["title"]))

    data["version"] = int(data.get("version", 1)) + 1
    data["last_checked"] = TODAY

    print(f"συγχωνεύτηκαν: {added_e} events, {added_o} opportunities "
          f"→ data.json v{data['version']}   (έμειναν {len(pending)} pending)")
    for it in confirmed:
        print("  + " + it["title"][:70])

    if dry:
        print("(--dry-run: δεν γράφτηκε τίποτα)")
        return

    save("data.json", data)
    save("inbox.json", pending)
    save("seen.json", sorted(seen))


if __name__ == "__main__":
    main()
