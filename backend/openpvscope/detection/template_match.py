"""OpenCV template matching + NMS (aligned with thesis multi-channel weights)."""

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


def match_templates(
    image: np.ndarray,
    templates: Sequence[np.ndarray],
    *,
    threshold: float = 0.5,
    nms_iou: float = 0.05,
    use_color: bool = True,
) -> tuple[list[dict], int]:
    """
    Multi-template match over a deskewed search image.

    RGB weights (thesis notebook): gray 10%, R 25%, G 25%, B 40%.
    Thermal / grayscale-only: 100% grayscale.
    Collects all peaks then one global NMS (same as old suite).

    Returns (detections, raw_peak_count_before_nms).
    """
    if image.ndim == 2:
        image_rgb = cv2.cvtColor(image, cv2.COLOR_GRAY2RGB)
        use_color = False
    else:
        image_rgb = image

    ih, iw = image_rgb.shape[:2]
    img_gray = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2GRAY)
    method = cv2.TM_CCOEFF_NORMED

    boxes: list[tuple[int, int, int, int]] = []
    scores: list[float] = []

    for ti, tpl in enumerate(templates):
        if tpl.ndim == 2:
            tpl_rgb = cv2.cvtColor(tpl, cv2.COLOR_GRAY2RGB)
        else:
            tpl_rgb = tpl
        th, tw = tpl_rgb.shape[:2]
        if th < 2 or tw < 2 or th >= ih or tw >= iw:
            continue

        tpl_gray = cv2.cvtColor(tpl_rgb, cv2.COLOR_RGB2GRAY)
        if use_color:
            # Exact notebook weights
            combined = cv2.matchTemplate(img_gray, tpl_gray, method) * 0.1
            combined = combined + cv2.matchTemplate(image_rgb[:, :, 0], tpl_rgb[:, :, 0], method) * 0.25
            combined = combined + cv2.matchTemplate(image_rgb[:, :, 1], tpl_rgb[:, :, 1], method) * 0.25
            combined = combined + cv2.matchTemplate(image_rgb[:, :, 2], tpl_rgb[:, :, 2], method) * 0.40
        else:
            combined = cv2.matchTemplate(img_gray, tpl_gray, method)

        ys, xs = np.where(combined >= threshold)
        for x, y in zip(xs.tolist(), ys.tolist()):
            boxes.append((int(x), int(y), int(tw), int(th)))
            scores.append(float(combined[y, x]))

    if not boxes:
        return [], 0

    raw = len(boxes)
    keep = nms(boxes, scores, nms_iou)
    return (
        [{"bbox": boxes[i], "confidence": scores[i], "template_index": 0} for i in keep],
        raw,
    )


# Back-compat alias used by older tests / callers
def match_template_multichannel(
    image_rgb: np.ndarray,
    template_rgb: np.ndarray,
    *,
    threshold: float = 0.5,
    nms_iou: float = 0.05,
) -> list[dict]:
    dets, _ = match_templates(
        image_rgb,
        [template_rgb],
        threshold=threshold,
        nms_iou=nms_iou,
        use_color=True,
    )
    return dets


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
