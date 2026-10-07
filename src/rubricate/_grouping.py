from collections import Counter


def segment(pages: list[tuple[int, str | None, int | None]], counts: dict[str, int]) -> list[list[int]]:
    seq = [(sid, v, p) for sid, v, p in pages if v is not None and p is not None]
    n = len(seq)
    costs = [0.0] * (n + 1)
    ends = [n] * n
    bound = 2 * max(counts.values()) + 1
    for i in range(n - 1, -1, -1):
        options = []
        for j in range(i + 1, min(n, i + bound) + 1):
            run = seq[i:j]
            versions = Counter(v for _, v, _ in run)
            version = max(versions, key=lambda v: versions[v])
            printed = [p for _, _, p in run]
            missing = len(set(range(1, counts[version] + 1)) - set(printed))
            repeated = len(printed) - len(set(printed))
            inversions = sum(a > b for k, a in enumerate(printed) for b in printed[k + 1 :])
            cost = missing + 0.6 * repeated + 2 * (len(versions) - 1) + 0.25 * inversions
            options.append((cost + costs[j], j))
        costs[i], ends[i] = min(options)
    groups = []
    i = 0
    while i < n:
        j = ends[i]
        groups.append([sid for sid, _, _ in seq[i:j]])
        i = j
    owners = {sid: group for group in groups for sid in group}
    previous: list[int] | None = None
    for sid, version, _ in pages:
        if version is not None:
            previous = owners[sid]
        elif previous is not None:
            previous.append(sid)
    return [sorted(group) for group in groups]
