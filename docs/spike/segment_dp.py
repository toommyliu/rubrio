import json
import sys
from functools import lru_cache

PAGES_PER_EXAM = 2
report = json.load(open(sys.argv[1]))


def cost(run):
    printed = [p for _, v, p in run]
    versions = {v for _, v, p in run}
    c = len(set(range(1, PAGES_PER_EXAM + 1)) - set(printed))
    c += 0.6 * (len(printed) - len(set(printed)))
    c += 2 * (len(versions) - 1)
    c += 0.25 * sum(1 for i in range(len(printed)) for j in range(i + 1, len(printed)) if printed[i] > printed[j])
    return c


def segment(pages):
    seq = [(p["scan_page"], p["match"][0], int(p["match"][1:])) for p in pages if p["match"]]
    extras = [p["scan_page"] for p in pages if not p["match"]]
    n = len(seq)

    @lru_cache(None)
    def best(i):
        if i == n:
            return 0.0, ()
        options = []
        for j in range(i + 1, min(n, i + 2 * PAGES_PER_EXAM + 1) + 1):
            rest_cost, rest = best(j)
            options.append((cost(tuple(seq[i:j])) + rest_cost, ((i, j),) + rest))
        return min(options)

    total, cuts = best(0)
    subs = [[s for s, _, _ in seq[i:j]] for i, j in cuts]
    flags = []
    for i, j in cuts:
        run = seq[i:j]
        printed = [p for _, _, p in run]
        pages_ = [s for s, _, _ in run]
        if len({v for _, v, _ in run}) > 1:
            flags.append(("mixed_versions", pages_))
        if set(range(1, PAGES_PER_EXAM + 1)) - set(printed):
            flags.append(("missing_page", pages_))
        if len(printed) != len(set(printed)):
            flags.append(("repeated_page", pages_))
        if printed != sorted(printed):
            flags.append(("out_of_order", pages_))
    for e in extras:
        owner = next((s for s in subs if s[-1] < e and all(x < e or x > e for x in s)), None)
        prev = [s for s in subs if max(s) < e]
        if prev:
            prev[-1].append(e)
            flags.append(("extra_page", [e]))
        else:
            flags.append(("unassigned_extra", [e]))
    return subs, flags


ok_all = True
for tag, v in report.items():
    subs, flags = segment(v["pages"])
    got = sorted(sorted(s) for s in subs)
    if tag == "original":
        print(f"== {tag}\n  subs: {subs}\n  flags: {flags}")
        continue
    want = sorted(sorted(s["scan_pages"]) for s in v["expected_submissions"])
    exp = [(f["kind"], f["scan_pages"]) for f in v["expected_flags"]]
    print(f"== {tag}\n  subs: {subs}\n  want: {[s['scan_pages'] for s in v['expected_submissions']]}  grouping_match={got == want}\n  flags: {flags}\n  expect: {exp}")
