import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from rubrio._images import DPI, cache_path, read_image, rendered, write_bytes
from rubrio.home import Home


@dataclass(frozen=True)
class Alignment:
    page_index: int
    template_page: int | None
    homography: list[list[float]] | None
    inliers: int
    runner_up: int


_templates: list[tuple[int, NDArray[Any], NDArray[Any]]] = []


def template_features(home: Home, file: str, page: int) -> Path:
    path = cache_path(home, "sift", [file, page, DPI, 4000], ".npz")
    if not path.exists():
        image = read_image(rendered(home, file, page - 1))
        points, descriptors = cv2.SIFT.create(nfeatures=4000).detectAndCompute(image, None)
        stream = io.BytesIO()
        np.savez(
            stream,
            points=np.asarray([p.pt for p in points], dtype=np.float32).reshape(-1, 2),
            descriptors=descriptors if descriptors is not None else np.empty((0, 128), dtype=np.float32),
        )
        write_bytes(path, stream.getvalue())
    return path


def initialize(features: list[tuple[int, str]]) -> None:
    global _templates
    cv2.setNumThreads(1)
    _templates = []
    for template_id, path in features:
        with np.load(path) as data:
            _templates.append((template_id, data["points"], data["descriptors"]))


def match_page(job: tuple[str, str, int, str]) -> Alignment:
    home_path, file, page_index, cache = job
    path = Path(cache)
    if path.exists():
        return Alignment(**json.loads(path.read_text()))
    home = Home(Path(home_path))
    image = read_image(rendered(home, file, page_index))
    cv2.setRNGSeed(0)
    points, descriptors = cv2.SIFT.create(nfeatures=4000).detectAndCompute(image, None)
    results: list[tuple[int, int, NDArray[Any] | None]] = []
    if descriptors is not None and len(descriptors) >= 2:
        matcher = cv2.FlannBasedMatcher({"algorithm": 1, "trees": 5}, {"checks": 50})
        for template_id, template_points, template_descriptors in _templates:
            if not len(template_descriptors):
                continue
            pairs = matcher.knnMatch(template_descriptors, descriptors, k=2)
            good = [m for m, n in (p for p in pairs if len(p) == 2) if m.distance < 0.7 * n.distance]
            if len(good) < 12:
                results.append((0, template_id, None))
                continue
            src = np.asarray([template_points[m.queryIdx] for m in good], dtype=np.float32)
            dst = np.asarray([points[m.trainIdx].pt for m in good], dtype=np.float32)
            homography, mask = cv2.findHomography(src, dst, cv2.RANSAC, 4.0)
            results.append(
                (
                    int(mask.sum()) if homography is not None and mask is not None else 0,
                    template_id,
                    homography,
                )
            )
    results.sort(key=lambda r: -r[0])
    best, template_id, homography = results[0] if results else (0, 0, None)
    runner_up = results[1][0] if len(results) > 1 else 0
    accepted = best >= 60 and best >= 1.5 * max(runner_up, 1) and homography is not None
    result = Alignment(
        page_index,
        template_id if accepted else None,
        homography.tolist() if accepted and homography is not None else None,
        best,
        runner_up,
    )
    write_bytes(
        path,
        json.dumps(
            {
                "page_index": page_index,
                "template_page": result.template_page,
                "homography": result.homography,
                "inliers": best,
                "runner_up": runner_up,
            }
        ).encode(),
    )
    return result
