"""
Fast custom center-bin spatial NMS via Numba (LLVM JIT).

Same semantics as the pure-Python / C++ ports:
  - center cell only, 3×3 neighbor IoU
  - suppress when iou > threshold
  - returns original input indices

Uses sorted bin keys + searchsorted (Numba-friendly; no typed Dict).
"""

from __future__ import annotations

from typing import Callable

import numpy as np

_NUMBA_OK = False
try:
    from numba import njit

    _NUMBA_OK = True
except Exception:  # pragma: no cover
    njit = None  # type: ignore


def numba_available() -> bool:
    return _NUMBA_OK


if _NUMBA_OK:

    @njit(cache=True)
    def _pack_bin(bx: int, by: int) -> np.int64:
        return (np.int64(np.uint32(bx)) << np.int64(32)) | np.int64(np.uint32(by))

    @njit(cache=True)
    def _spatial_nms_kernel(
        boxes: np.ndarray,
        scores: np.ndarray,
        valid_orig: np.ndarray,
        iou_threshold: float,
    ) -> np.ndarray:
        n = boxes.shape[0]
        if n == 0:
            return np.empty(0, dtype=np.int64)

        order = np.argsort(scores)[::-1]

        sum_w = 0.0
        sum_h = 0.0
        min_x = boxes[0, 0]
        min_y = boxes[0, 1]
        for i in range(n):
            x1 = boxes[i, 0]
            y1 = boxes[i, 1]
            x2 = boxes[i, 2]
            y2 = boxes[i, 3]
            sum_w += x2 - x1
            sum_h += y2 - y1
            if x1 < min_x:
                min_x = x1
            if y1 < min_y:
                min_y = y1
        avg_w = sum_w / n
        avg_h = sum_h / n
        bin_size = max(avg_w, avg_h, 1.0) * 2.0

        bin_x = np.empty(n, dtype=np.int32)
        bin_y = np.empty(n, dtype=np.int32)
        keys = np.empty(n, dtype=np.int64)
        for i in range(n):
            cx = 0.5 * (boxes[i, 0] + boxes[i, 2])
            cy = 0.5 * (boxes[i, 1] + boxes[i, 3])
            bx = int(np.floor((cx - min_x) / bin_size))
            by = int(np.floor((cy - min_y) / bin_size))
            bin_x[i] = bx
            bin_y[i] = by
            keys[i] = _pack_bin(bx, by)

        # Sort indices by bin key for O(log N + k) neighbor lookup
        bin_order = np.argsort(keys)
        sorted_keys = keys[bin_order]

        suppressed = np.zeros(n, dtype=np.uint8)
        keep_buf = np.empty(n, dtype=np.int64)
        keep_n = 0

        for si in range(n):
            local_idx = order[si]
            if suppressed[local_idx] != 0:
                continue
            keep_buf[keep_n] = valid_orig[local_idx]
            keep_n += 1

            c0 = boxes[local_idx, 0]
            c1 = boxes[local_idx, 1]
            c2 = boxes[local_idx, 2]
            c3 = boxes[local_idx, 3]
            current_area = (c2 - c0) * (c3 - c1)
            bx0 = bin_x[local_idx]
            by0 = bin_y[local_idx]

            for dx in range(-1, 2):
                for dy in range(-1, 2):
                    q = _pack_bin(bx0 + dx, by0 + dy)
                    lo = np.searchsorted(sorted_keys, q, side="left")
                    hi = np.searchsorted(sorted_keys, q, side="right")
                    for t in range(lo, hi):
                        other_idx = bin_order[t]
                        if other_idx == local_idx or suppressed[other_idx] != 0:
                            continue
                        o0 = boxes[other_idx, 0]
                        o1 = boxes[other_idx, 1]
                        o2 = boxes[other_idx, 2]
                        o3 = boxes[other_idx, 3]
                        xx1 = c0 if c0 > o0 else o0
                        yy1 = c1 if c1 > o1 else o1
                        xx2 = c2 if c2 < o2 else o2
                        yy2 = c3 if c3 < o3 else o3
                        if xx2 > xx1 and yy2 > yy1:
                            inter = (xx2 - xx1) * (yy2 - yy1)
                            uni = current_area + (o2 - o0) * (o3 - o1) - inter
                            iou = inter / uni if uni > 0.0 else 0.0
                            if iou > iou_threshold:
                                suppressed[other_idx] = 1

        return keep_buf[:keep_n].copy()


def spatial_nms_numba(
    boxes_xyxy: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float,
    progress_callback: Callable[[str], None] | None = None,
) -> list[int]:
    if not _NUMBA_OK:
        raise RuntimeError("numba not available")

    if len(boxes_xyxy) == 0:
        return []

    boxes_in = np.asarray(boxes_xyxy, dtype=np.float32)
    scores_in = np.asarray(scores, dtype=np.float32)
    if boxes_in.ndim != 2 or boxes_in.shape[1] != 4:
        raise ValueError("boxes_xyxy must be (N,4)")
    if scores_in.shape[0] != boxes_in.shape[0]:
        raise ValueError("scores length mismatch")

    n_in = boxes_in.shape[0]
    boxes = boxes_in.copy()
    boxes[:, 2] = np.maximum(boxes[:, 0], boxes[:, 2])
    boxes[:, 3] = np.maximum(boxes[:, 1], boxes[:, 3])
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    valid = np.flatnonzero(area > 0).astype(np.int64)
    if len(valid) == 0:
        return []

    boxes_v = np.ascontiguousarray(boxes[valid])
    scores_v = np.ascontiguousarray(scores_in[valid])

    if progress_callback:
        progress_callback(f"NMS [Numba] {len(valid)} peaks (center-bin)…")

    keep = _spatial_nms_kernel(boxes_v, scores_v, valid, float(iou_threshold))

    if progress_callback:
        progress_callback(f"NMS [Numba] kept {len(keep)} / {n_in}")

    return [int(i) for i in keep]


def warmup_numba() -> bool:
    """Compile the kernel once so the first real call is fast."""
    if not _NUMBA_OK:
        return False
    b = np.array([[0.0, 0.0, 10.0, 10.0], [1.0, 1.0, 11.0, 11.0]], dtype=np.float32)
    s = np.array([0.9, 0.8], dtype=np.float32)
    spatial_nms_numba(b, s, 0.5)
    return True
