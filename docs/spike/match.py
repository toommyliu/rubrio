import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np
import pymupdf

SAMPLE = Path(sys.argv[1])
OUT = Path(sys.argv[2])
DPI = 150
OUT.mkdir(parents=True, exist_ok=True)


def render(page, dpi=DPI):
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    return np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width).copy()


sift = cv2.SIFT_create(nfeatures=4000)
flann = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 50})

templates = []
for version in ("A", "B"):
    doc = pymupdf.open(SAMPLE / f"template-v{version}.pdf")
    for i, page in enumerate(doc):
        img = render(page)
        kp, des = sift.detectAndCompute(img, None)
        bands = {}
        words = page.get_text("words")
        name = next((w for w in words if w[4] == "Name"), None)
        if name:
            bands["name"] = (name[0] - 10, name[1] - 25, 540, name[3] + 8)
        labels = sorted((w[1], w[4]) for w in words if w[4] in ("1.", "2.", "3.", "4.", "5.", "6.", "7.") and w[0] < 80)
        for (y0, lab), (y1, _) in zip(labels, labels[1:] + [(760, None)]):
            bands[f"q{lab[:-1]}"] = (60, y0 - 6, 552, y1 - 6)
        templates.append({"key": f"{version}{i + 1}", "version": version, "page": i + 1, "img": img, "kp": kp, "des": des, "bands": bands})


def match(img):
    kp, des = sift.detectAndCompute(img, None)
    results = []
    for t in templates:
        pairs = flann.knnMatch(t["des"], des, k=2)
        good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.7 * n.distance]
        if len(good) < 12:
            results.append((0, t, None, None))
            continue
        src = np.float32([t["kp"][m.queryIdx].pt for m in good])
        dst = np.float32([kp[m.trainIdx].pt for m in good])
        H, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
        if H is None:
            results.append((0, t, None, None))
            continue
        inl = mask.ravel().astype(bool)
        hom = np.hstack([src[inl], np.ones((inl.sum(), 1), np.float32)]) @ H.T
        proj = hom[:, :2] / hom[:, 2:]
        err = float(np.median(np.linalg.norm(proj - dst[inl], axis=1)))
        results.append((int(inl.sum()), t, H, err))
    results.sort(key=lambda r: -r[0])
    return results


def classify(pdf):
    doc = pymupdf.open(pdf)
    pages = []
    for i, page in enumerate(doc):
        img = render(page)
        t0 = time.perf_counter()
        res = match(img)
        dt = time.perf_counter() - t0
        best, runner = res[0], res[1]
        inliers, t, H, err = best
        accepted = inliers >= 60 and inliers >= 1.5 * max(runner[0], 1)
        rot = None
        if H is not None:
            v = H[:2, :2] @ np.array([1.0, 0.0])
            rot = round(float(np.degrees(np.arctan2(v[1], v[0]))), 1)
        pages.append({
            "scan_page": i + 1,
            "match": t["key"] if accepted else None,
            "inliers": inliers,
            "runner_up": f"{runner[1]['key']}:{runner[0]}",
            "reproj_px": None if err is None else round(err, 2),
            "rotation_deg": rot,
            "ms": round(dt * 1000),
            "_img": img,
            "_H": H if accepted else None,
            "_t": t if accepted else None,
        })
    return pages


def segment(pages):
    subs, flags, current = [], [], None
    for p in pages:
        if p["match"] is None:
            if current is None:
                flags.append({"kind": "unassigned_extra", "scan_pages": [p["scan_page"]]})
            else:
                current["extra"].append(p["scan_page"])
                flags.append({"kind": "extra_page", "scan_pages": [p["scan_page"]]})
            continue
        t = p["_t"]
        if current is None or t["page"] == 1 and current["pages"]:
            current = {"pages": {}, "versions": set(), "extra": []}
            subs.append(current)
        if t["page"] in current["pages"].values():
            flags.append({"kind": "repeated_page", "scan_pages": [k for k, v in current["pages"].items() if v == t["page"]] + [p["scan_page"]]})
        prev = list(current["pages"].values())
        if prev and t["page"] < prev[-1]:
            flags.append({"kind": "out_of_order", "scan_pages": [p["scan_page"]]})
        current["pages"][p["scan_page"]] = t["page"]
        current["versions"].add(t["version"])
    for s in subs:
        sp = sorted(s["pages"])
        if len(s["versions"]) > 1:
            flags.append({"kind": "mixed_versions", "scan_pages": sp})
        missing = sorted({1, 2} - set(s["pages"].values()))
        if missing:
            flags.append({"kind": "missing_page", "scan_pages": sp, "printed_pages": missing})
    return [{"scan_pages": sorted(s["pages"]) + s["extra"], "versions": sorted(s["versions"])} for s in subs], flags


def crops(pages, tag):
    tiles = []
    for p in pages:
        if p["_H"] is None:
            continue
        t = p["_t"]
        h, w = t["img"].shape
        warped = cv2.warpPerspective(p["_img"], np.linalg.inv(p["_H"]), (w, h), flags=cv2.INTER_LINEAR, borderValue=255)
        s = DPI / 72
        for name, (x0, y0, x1, y1) in t["bands"].items():
            if name not in ("name", "q1", "q4"):
                continue
            c = warped[int(y0 * s):int(y1 * s), int(x0 * s):int(x1 * s)]
            c = cv2.resize(c, (900, int(c.shape[0] * 900 / c.shape[1])))
            label = np.full((28, 900), 255, np.uint8)
            cv2.putText(label, f"scan p{p['scan_page']} -> {t['key']} {name}", (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, 0, 1)
            tiles += [label, c, np.full((6, 900), 180, np.uint8)]
    if tiles:
        cv2.imwrite(str(OUT / f"{tag}-crops.png"), np.vstack(tiles))


report = {}
runs = [("original", SAMPLE / "submissions.pdf", SAMPLE / "ground-truth.json")]
for v in sorted((SAMPLE / "variants").iterdir()):
    if v.is_dir():
        runs.append((v.name, v / "submissions.pdf", v / "ground-truth.json"))
for tag, pdf, truth in runs:
    pages = classify(pdf)
    subs, flags = segment(pages)
    if tag == "original":
        crops(pages, tag)
    report[tag] = {
        "pages": [{k: v for k, v in p.items() if not k.startswith("_")} for p in pages],
        "submissions": subs,
        "flags": flags,
    }
    gt = json.loads(truth.read_text())
    if tag != "original":
        report[tag]["expected_flags"] = [{"kind": f["kind"], "scan_pages": f["scan_pages"]} for f in gt["expected_flags"]]
        report[tag]["expected_submissions"] = gt["submissions"]
(OUT / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False))
print("done")
