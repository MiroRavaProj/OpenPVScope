"""OpenCV template matching + NMS (slim rewrite)."""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)
    inter = max(0, x2 - x1) * max(0, y2 - y1)
    if inter <= 0:
        return 0.0
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def nms(
    boxes: list[tuple[int, int, int, int]],
    scores: list[float],
    iou_thresh: float,
) -> list[int]:
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    keep: list[int] = []
    while order:
        i = order.pop(0)
        keep.append(i)
        order = [j for j in order if _iou(boxes[i], boxes[j]) < iou_thresh]
    return keep


def match_template_multichannel(
    image_rgb: np.ndarray,
    template_rgb: np.ndarray,
    *,
    threshold: float = 0.55,
    nms_iou: float = 0.15,
) -> list[dict]:
    """
    Detect template occurrences in an RGB uint8 image.
    Returns [{bbox: (x,y,w,h), confidence: float}, ...] in image pixel coords.
    """
    if image_rgb.ndim != 3 or template_rgb.ndim != 3:
        raise ValueError("image and template must be HxWx3 RGB")
    th, tw = template_rgb.shape[:2]
    ih, iw = image_rgb.shape[:2]
    if th >= ih or tw >= iw or th < 2 or tw < 2:
        return []

    img_g = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    tpl_g = cv2.cvtColor(template_rgb, cv2.COLOR_RGB2GRAY)
    method = cv2.TM_CCOEFF_NORMED
    res = img_g.astype(np.float32) * 0  # placeholder shape
    res = cv2.matchTemplate(img_g, tpl_g, method) * 0.1
    for ch in range(3):
        w = (0.25, 0.25, 0.4)[ch]
        res = res + cv2.matchTemplate(image_rgb[:, :, ch], template_rgb[:, :, ch], method) * w

    ys, xs = np.where(res >= threshold)
    if len(xs) == 0:
        return []

    boxes: list[tuple[int, int, int, int]] = []
    scores: list[float] = []
    for x, y in zip(xs.tolist(), ys.tolist()):
        boxes.append((int(x), int(y), int(tw), int(th)))
        scores.append(float(res[y, x]))

    keep = nms(boxes, scores, nms_iou)
    return [{"bbox": boxes[i], "confidence": scores[i]} for i in keep]


def extract_patch_rgb(
    image_rgb: np.ndarray,
    col0: int,
    row0: int,
    col1: int,
    row1: int,
) -> np.ndarray | None:
    h, w = image_rgb.shape[:2]
    c0 = max(0, min(w - 1, col0))
    c1 = max(c0 + 1, min(w, col1))
    r0 = max(0, min(h - 1, row0))
    r1 = max(r0 + 1, min(h, row1))
    patch = image_rgb[r0:r1, c0:c1]
    if patch.size == 0 or patch.shape[0] < 2 or patch.shape[1] < 2:
        return None
    return patch
