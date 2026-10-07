import re
import sys

import numpy as np
import pymupdf
from rapidocr import RapidOCR

SAMPLE = sys.argv[1]
LABEL = re.compile(r"^(\d+\s*[.)]|[a-z]\s*\))")
ocr = RapidOCR()
for version in "AB":
    doc = pymupdf.open(f"{SAMPLE}/template-v{version}.pdf")
    for i, page in enumerate(doc):
        truth = []
        for b in page.get_text("dict")["blocks"]:
            for l in b.get("lines", []):
                t = "".join(s["text"] for s in l["spans"]).strip()
                if LABEL.match(t) and l["bbox"][0] < 120:
                    truth.append((round(l["bbox"][1], 1), t[:28]))
        pix = page.get_pixmap(dpi=150, colorspace=pymupdf.csGRAY)
        img = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
        res = ocr(img)
        found = []
        for box, text in zip(res.boxes, res.txts):
            x0 = min(p[0] for p in box) * 72 / 150
            y0 = min(p[1] for p in box) * 72 / 150
            if LABEL.match(text.strip()) and x0 < 120:
                found.append((round(y0, 1), text.strip()[:28]))
        found.sort()
        print(f"{version}{i + 1}: text layer {len(truth)} labels, OCR {len(found)}")
        for (ty, tt), f in zip(truth, found + [(None, None)] * len(truth)):
            print(f"   {tt!r:32} y={ty:<6} | ocr {f[1]!r:32} y={f[0]}  dy={None if f[0] is None else round(f[0] - ty, 1)}")
