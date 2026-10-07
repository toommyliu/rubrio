import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from rapidocr import RapidOCR

sys.argv = ["match.py", sys.argv[1], "out"]
exec(Path("match.py").read_text().split("report = {}")[0])
SAMPLE = Path(sys.argv[1])

fields = {}
for version in "AB":
    page = pymupdf.open(SAMPLE / f"template-v{version}.pdf")[0]
    lines = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            lines.append(("".join(s["text"] for s in l["spans"]).strip(), l["bbox"]))
    name = next(bb for t, bb in lines if t == "Name")
    sid = next(bb for t, bb in lines if t == "Student ID")
    after = min(bb[1] for t, bb in lines if bb[1] > sid[3] + 5)
    fields[f"{version}1"] = {
        "name": (name[2] + 8, name[1] - 22, 560, sid[1] - 6),
        "id": (sid[2] + 8, sid[1] - 22, 560, after - 6),
    }

ocr = RapidOCR()
pages = classify(SAMPLE / "submissions.pdf")
roster = [l.split("\t") for l in (SAMPLE / "roster.tsv").read_text().strip().splitlines()]
truth = {s["scan_pages"][0] if s["scan"]["pages"][0]["printed_page"] == 1 else s["scan_pages"][1]: s["student"]["name"]
         for s in json.load(open(SAMPLE / "ground-truth.json"))["assignments"][0]["submissions"]}
s = DPI / 72
reads = {}
for p in pages:
    t = p["_t"]
    if not t or t["page"] != 1:
        continue
    h, w = t["img"].shape
    warped = cv2.warpPerspective(p["_img"], np.linalg.inv(p["_H"]), (w, h), borderValue=255)
    got = {}
    for field, (x0, y0, x1, y1) in fields[t["key"]].items():
        crop = warped[int(y0 * s):int(y1 * s), int(x0 * s):int(x1 * s)]
        res = ocr(crop)
        got[field] = " ".join(res.txts) if res.txts else ""
    reads[p["scan_page"]] = got


def norm(x):
    return unicodedata.normalize("NFKD", x).encode("ascii", "ignore").decode().lower().strip()


def opengrader(name, sid):
    best = max(roster, key=lambda e: 0.7 * SequenceMatcher(None, sid, e[0]).ratio() + 0.3 * SequenceMatcher(None, name.lower(), e[1].lower()).ratio())
    score = 0.7 * SequenceMatcher(None, sid, best[0]).ratio() + 0.3 * SequenceMatcher(None, name.lower(), best[1].lower()).ratio()
    return best[1] if score >= 0.6 else None


def score(entry, name_text, id_text):
    best = 0.0
    for a, b in ((name_text, id_text), (id_text, name_text)):
        digits = re.sub(r"\D", "", b)
        id_s = SequenceMatcher(None, digits, entry[0]).ratio() if len(digits) >= 4 else 0.0
        name_s = SequenceMatcher(None, norm(a), norm(entry[1])).ratio() if re.search(r"[A-Za-z]", a) else 0.0
        last = norm(entry[1]).split()[-1]
        if last and last in norm(a):
            name_s = max(name_s, 0.8)
        best = max(best, 0.6 * id_s + 0.4 * name_s)
    return best


rows = []
for sp, got in reads.items():
    ranked = sorted(((score(e, got["name"], got["id"]), e[1]) for e in roster), reverse=True)
    rows.append((sp, got, ranked))

taken, proposed = set(), {}
for sc, sp, who in sorted(((r[2][0][0], r[0], r[2][0][1]) for r in rows), reverse=True):
    ranked = next(r[2] for r in rows if r[0] == sp)
    choice = next(((c, n) for c, n in ranked if n not in taken), None)
    runner = next((c for c, n in ranked if n not in taken and n != choice[1]), 0)
    proposed[sp] = (choice[1], round(choice[0], 2), round(choice[0] - runner, 2))
    taken.add(choice[1])

ok_og = ok_new = sure = sure_ok = 0
for sp, got, ranked in rows:
    og = opengrader(got["name"], re.sub(r"\D", "", got["id"]))
    who, sc, margin = proposed[sp]
    confident = margin >= 0.15
    ok_og += og == truth[sp]
    ok_new += who == truth[sp]
    sure += confident
    sure_ok += confident and who == truth[sp]
    print(f"p{sp:<3} truth={truth[sp]:<13} read name={got['name']!r:<22} id={got['id']!r:<16} opengrader={og!s:<13} new={who:<13} score={sc} margin={margin} {'SUGGEST' if confident else 'ASK'}")
print(f"opengrader rule correct {ok_og}/{len(rows)}; new rule correct {ok_new}/{len(rows)}; confident {sure}, of which correct {sure_ok}")
