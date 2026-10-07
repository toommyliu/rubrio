import re
import sys
from pathlib import Path

import cv2
import numpy as np
import pymupdf

sys.argv = ["match.py", sys.argv[1], "out"]
exec(Path("match.py").read_text().split("report = {}")[0])

SAMPLE = Path(sys.argv[1])
LABEL = re.compile(r"^(\d+\.|[a-z]\))\s+\((\d+) pts\)\s*(.*)")
bands_by_page = {}
for version in "AB":
    doc = pymupdf.open(SAMPLE / f"template-v{version}.pdf")
    for i, page in enumerate(doc):
        labels = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                text = "".join(s["text"] for s in l["spans"]).strip()
                m = LABEL.match(text)
                if m:
                    labels.append((l["bbox"][1], m.group(3)[:30]))
        labels.sort()
        bottom = page.rect.height - 50
        bands_by_page[f"{version}{i + 1}"] = [
            (prompt, y - 6, (labels[k + 1][0] if k + 1 < len(labels) else bottom) - 6)
            for k, (y, prompt) in enumerate(labels)
        ]

pages = {p["scan_page"]: p for p in classify(SAMPLE / "submissions.pdf")}
groups = [[1, 2], [3, 4], [5, 6], [7, 8], [9, 10], [11, 12], [13, 14], [16, 17]]
wanted = ["What is 9 plus 6?", "Convert binary 1010 to decimal"]
s = DPI / 72
for want in wanted:
    tiles = []
    for g in groups:
        for sp in g:
            p = pages[sp]
            t = p["_t"]
            for prompt, y0, y1 in bands_by_page[t["key"]]:
                if prompt.startswith(want[:20]):
                    h, w = t["img"].shape
                    warped = cv2.warpPerspective(p["_img"], np.linalg.inv(p["_H"]), (w, h), borderValue=255)
                    c = warped[int(y0 * s):int(y1 * s), int(60 * s):int(560 * s)]
                    c = cv2.resize(c, (700, int(c.shape[0] * 700 / c.shape[1])))
                    label = np.full((26, 700), 255, np.uint8)
                    cv2.putText(label, f"scan p{sp} -> template {t['key']}  band {y0:.0f}-{y1:.0f}pt", (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, 0, 1)
                    tiles += [label, c, np.full((5, 700), 160, np.uint8)]
    name = want.split()[0].lower() + "-" + want.split()[-1].strip("?").lower()
    cv2.imwrite(f"out/band-{name}.png", np.vstack(tiles))
    print(name, len(tiles) // 3, "crops")
