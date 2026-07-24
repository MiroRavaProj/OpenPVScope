"""
Multi-channel template matching + spatial NMS.

Port of legacy suite:
  utils/detection/template_matching.py
  utils/detection/nms_operations.py

RGB weights: gray 10%, R 25%, G 25%, B 40%.
IR / grayscale: 100% gray.
NMS suppress when iou > threshold (not >=).

Parallel template waves (ThreadPoolExecutor, workers = max(1, CPU count − 2)).
Mid-run NMS at >=70% system RAM uses 2× user IoU (lighter prune);
final NMS uses the user IoU.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Sequence, Callable

import cv2
import numpy as np

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

RAM_TRIGGER_PERCENT = 70.0
EARLY_NMS_MIN_PEAKS = 1000
# Force mid-run / pre-final prune before spatial NMS builds huge structures
PEAK_BUFFER_CAP = 1_500_000
PEAK_HARD_CAP = 2_000_000


class _Heartbeat:
    """Emit progress while a blocking OpenCV call runs (matchTemplate, etc.)."""

    def __init__(
        self,
        progress: Callable[[float, str], None] | None,
        pct: float,
        label: str,
        *,
        interval_s: float = 2.0,
    ) -> None:
        self._progress = progress
        self._pct = pct
        self._label = label
        self._interval = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._t0 = 0.0

    def __enter__(self) -> _Heartbeat:
        if not self._progress:
            return self
        self._t0 = time.monotonic()
        self._thread = threading.Thread(target=self._run, name="match-heartbeat", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)

    def _run(self) -> None:
        assert self._progress is not None
        while not self._stop.wait(self._interval):
            elapsed = int(time.monotonic() - self._t0)
            self._progress(self._pct, f"{self._label} ({elapsed}s…)")


def _system_ram_percent() -> float:
    if psutil is None:
        return 0.0
    return float(psutil.virtual_memory().percent)


def _iou_xywh(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
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
    """Simple NMS; suppress when iou > iou_thresh."""
    order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
    keep: list[int] = []
    while order:
        i = order.pop(0)
        keep.append(i)
        order = [j for j in order if _iou_xywh(boxes[i], boxes[j]) <= iou_thresh]
    return keep


def _optimized_spatial_nms_py(
    boxes_xyxy: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float,
    progress_callback: Callable[[str], None] | None = None,
) -> list[int]:
    """
    Memory-lean spatial NMS (pure Python).

    Each box is hashed to its *center* cell only; suppression checks the 3×3
    neighborhood. Suppress when iou > iou_threshold.
    """
    if len(boxes_xyxy) == 0:
        return []

    boxes = np.array(boxes_xyxy, dtype=np.float32, copy=True)
    boxes[:, 2] = np.maximum(boxes[:, 0], boxes[:, 2])
    boxes[:, 3] = np.maximum(boxes[:, 1], boxes[:, 3])
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    valid = np.flatnonzero(area > 0)
    if len(valid) == 0:
        return []

    boxes = boxes[valid]
    scores_v = np.asarray(scores, dtype=np.float32)[valid]
    area = area[valid]
    n = len(boxes)
    # Highest score first
    sorted_indices = np.argsort(scores_v, kind="mergesort")[::-1]

    avg_w = float(np.mean(boxes[:, 2] - boxes[:, 0]))
    avg_h = float(np.mean(boxes[:, 3] - boxes[:, 1]))
    bin_size = max(avg_w, avg_h, 1.0) * 2.0
    min_x = float(np.min(boxes[:, 0]))
    min_y = float(np.min(boxes[:, 1]))

    cx = (boxes[:, 0] + boxes[:, 2]) * 0.5
    cy = (boxes[:, 1] + boxes[:, 3]) * 0.5
    bin_x = np.floor((cx - min_x) / bin_size).astype(np.int32)
    bin_y = np.floor((cy - min_y) / bin_size).astype(np.int32)

    # One list per center cell — O(N) entries total, not O(N × cells_per_box)
    spatial_bins: dict[tuple[int, int], list[int]] = {}
    prog_step = max(50_000, n // 20)  # ~5% or 50k, whichever larger
    for i in range(n):
        if progress_callback and i > 0 and (i % prog_step == 0 or i + 1 == n):
            progress_callback(f"NMS binning {i}/{n}")
        key = (int(bin_x[i]), int(bin_y[i]))
        spatial_bins.setdefault(key, []).append(i)

    keep_local: list[int] = []
    suppressed = np.zeros(n, dtype=np.bool_)

    for i, local_idx in enumerate(sorted_indices):
        if progress_callback and i > 0 and (i % prog_step == 0 or i + 1 == n):
            progress_callback(f"NMS suppress {i}/{n} (kept {len(keep_local)})")
        local_idx = int(local_idx)
        if suppressed[local_idx]:
            continue
        keep_local.append(int(valid[local_idx]))
        current = boxes[local_idx]
        current_area = float(area[local_idx])
        bx0 = int(bin_x[local_idx])
        by0 = int(bin_y[local_idx])
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for other_idx in spatial_bins.get((bx0 + dx, by0 + dy), ()):
                    if other_idx == local_idx or suppressed[other_idx]:
                        continue
                    other = boxes[other_idx]
                    xx1 = max(current[0], other[0])
                    yy1 = max(current[1], other[1])
                    xx2 = min(current[2], other[2])
                    yy2 = min(current[3], other[3])
                    if xx2 > xx1 and yy2 > yy1:
                        inter = (xx2 - xx1) * (yy2 - yy1)
                        union = current_area + float(area[other_idx]) - inter
                        iou = inter / union if union > 0 else 0.0
                        if iou > iou_threshold:
                            suppressed[other_idx] = True

    return keep_local


_CPP_NMS = None
_CPP_NMS_TRIED = False


def _load_cpp_nms():
    global _CPP_NMS, _CPP_NMS_TRIED
    if _CPP_NMS_TRIED:
        return _CPP_NMS
    _CPP_NMS_TRIED = True
    try:
        from openpvscope.detection import _spatial_nms as mod  # type: ignore

        _CPP_NMS = mod
    except Exception:
        _CPP_NMS = None
    return _CPP_NMS


def optimized_spatial_nms(
    boxes_xyxy: np.ndarray,
    scores: np.ndarray,
    iou_threshold: float,
    progress_callback: Callable[[str], None] | None = None,
) -> list[int]:
    """
    Custom center-bin spatial NMS.

    Prefer Numba (LLVM JIT, no MSVC) > optional C++ extension > pure Python.
    Suppress when iou > threshold.
    """
    # 1) Numba — best default on Windows (no compiler toolchain required)
    try:
        from openpvscope.detection.spatial_nms_fast import numba_available, spatial_nms_numba

        if numba_available():
            return spatial_nms_numba(
                boxes_xyxy, scores, iou_threshold, progress_callback=progress_callback
            )
    except Exception:
        pass

    # 2) Optional C++ extension if previously built
    cpp = _load_cpp_nms()
    if cpp is not None:
        boxes = np.asarray(boxes_xyxy, dtype=np.float32)
        sc = np.asarray(scores, dtype=np.float32)
        cb = progress_callback if progress_callback is not None else None
        return list(cpp.spatial_nms(boxes, sc, float(iou_threshold), cb))

    # 3) Pure Python fallback
    return _optimized_spatial_nms_py(
        boxes_xyxy, scores, iou_threshold, progress_callback=progress_callback
    )


def _boxes_xywh_to_xyxy(detected: np.ndarray) -> np.ndarray:
    nms_boxes = np.empty((detected.shape[0], 4), dtype=np.float32)
    nms_boxes[:, 0] = detected[:, 0]
    nms_boxes[:, 1] = detected[:, 1]
    nms_boxes[:, 2] = detected[:, 0] + detected[:, 2]
    nms_boxes[:, 3] = detected[:, 1] + detected[:, 3]
    return nms_boxes


def _top_k_peaks(
    all_boxes: list[list[float]],
    all_scores: list[float],
    k: int,
) -> tuple[list[list[float]], list[float]]:
    """Keep the k highest-scoring peaks (safety valve before NMS)."""
    n = len(all_scores)
    if n <= k:
        return all_boxes, all_scores
    scores_np = np.asarray(all_scores, dtype=np.float32)
    # argpartition then sort the top-k slice
    idx = np.argpartition(scores_np, -k)[-k:]
    idx = idx[np.argsort(scores_np[idx])[::-1]]
    return [all_boxes[int(i)] for i in idx], [float(scores_np[int(i)]) for i in idx]


def _prune_peaks(
    all_boxes: list[list[float]],
    all_scores: list[float],
    iou_threshold: float,
    progress_callback: Callable[[str], None] | None = None,
) -> tuple[list[list[float]], list[float]]:
    if not all_boxes:
        return all_boxes, all_scores
    detected = np.asarray(all_boxes, dtype=np.float32)
    scores_np = np.asarray(all_scores, dtype=np.float32)
    indices = optimized_spatial_nms(
        _boxes_xywh_to_xyxy(detected),
        scores_np,
        iou_threshold,
        progress_callback=progress_callback,
    )
    kept_boxes = [all_boxes[i] for i in indices]
    kept_scores = [all_scores[i] for i in indices]
    return kept_boxes, kept_scores


def _channel_weight(cname: str, *, use_color: bool, n_channels: int) -> float:
    if use_color and n_channels > 1:
        if cname == "grayscale":
            return 0.1
        if cname == "red":
            return 0.25
        if cname == "green":
            return 0.25
        if cname == "blue":
            return 0.4
        return 0.0
    return 1.0 if cname == "grayscale" else 0.0


def _prep_image_channels(
    image: np.ndarray, *, use_color: bool
) -> list[tuple[str, np.ndarray]]:
    channels_img: list[tuple[str, np.ndarray]] = []
    if image.ndim == 3 and image.shape[2] >= 3 and use_color:
        gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
        channels_img.append(("grayscale", gray))
        channels_img.append(("blue", image[:, :, 2]))
        channels_img.append(("green", image[:, :, 1]))
        channels_img.append(("red", image[:, :, 0]))
    elif image.ndim == 3 and image.shape[2] >= 3:
        channels_img.append(("grayscale", cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)))
    else:
        g = image if image.ndim == 2 else image[:, :, 0]
        channels_img.append(("grayscale", np.ascontiguousarray(g)))
    return channels_img


def _prep_template_channels(
    tpl: np.ndarray, *, use_color: bool
) -> list[tuple[str, np.ndarray]]:
    tpl_channels: list[tuple[str, np.ndarray]] = []
    if tpl.ndim == 3 and tpl.shape[2] >= 3 and use_color:
        tpl_channels.append(("grayscale", cv2.cvtColor(tpl, cv2.COLOR_RGB2GRAY)))
        tpl_channels.append(("blue", tpl[:, :, 2]))
        tpl_channels.append(("green", tpl[:, :, 1]))
        tpl_channels.append(("red", tpl[:, :, 0]))
    elif tpl.ndim == 3 and tpl.shape[2] >= 3:
        tpl_channels.append(("grayscale", cv2.cvtColor(tpl, cv2.COLOR_RGB2GRAY)))
    else:
        tg = tpl if tpl.ndim == 2 else tpl[:, :, 0]
        tpl_channels.append(("grayscale", np.ascontiguousarray(tg)))
    return tpl_channels


def _match_one_template(
    template_idx: int,
    tpl: np.ndarray,
    channels_img: list[tuple[str, np.ndarray]],
    *,
    threshold: float,
    use_color: bool,
    ih: int,
    iw: int,
) -> tuple[int, list[list[float]], list[float], str | None]:
    """
    Match a single template. Returns (template_idx, boxes_xywh, scores, skip_reason).
    No progress callbacks (safe for worker threads).
    """
    method = cv2.TM_CCOEFF_NORMED
    tpl_channels = _prep_template_channels(tpl, use_color=use_color)
    th, tw = tpl_channels[0][1].shape[:2]
    if th < 2 or tw < 2 or th > ih or tw > iw:
        return template_idx, [], [], f"skip size {tw}x{th}"

    combined = None
    n_img_ch = len(channels_img)
    for (cname, img_ch), (_, tpl_ch) in zip(channels_img, tpl_channels):
        if img_ch.shape[0] < tpl_ch.shape[0] or img_ch.shape[1] < tpl_ch.shape[1]:
            continue
        res = cv2.matchTemplate(img_ch, tpl_ch, method)
        weight = _channel_weight(cname, use_color=use_color, n_channels=n_img_ch)
        if combined is None:
            combined = res * weight
        elif combined.shape == res.shape:
            combined = combined + res * weight

    if combined is None:
        return template_idx, [], [], "no channel match"

    ys, xs = np.where(combined >= threshold)
    boxes: list[list[float]] = []
    scores: list[float] = []
    for y, x in zip(ys.tolist(), xs.tolist()):
        boxes.append([float(x), float(y), float(tw), float(th)])
        scores.append(float(combined[y, x]))
    return template_idx, boxes, scores, None


def match_templates(
    image: np.ndarray,
    templates: Sequence[np.ndarray],
    *,
    threshold: float = 0.5,
    nms_iou: float = 0.05,
    use_color: bool = True,
    progress: Callable[[float, str], None] | None = None,
) -> tuple[list[dict], int]:
    """
    Multi-template match with parallel waves + memory-triggered mid-run NMS.

    Mid-run NMS (RAM >= 70%, peaks >= 1000) uses iou = min(0.99, 2× nms_iou).
    Final NMS uses nms_iou.

    Returns (detections, raw_peaks_seen) where raw_peaks_seen counts peaks
    before any mid-run prune.
    """
    channels_img = _prep_image_channels(image, use_color=use_color)
    ih = channels_img[0][1].shape[0]
    iw = channels_img[0][1].shape[1]

    all_boxes: list[list[float]] = []
    all_scores: list[float] = []
    raw_peaks_seen = 0
    n_tpl = len(templates)
    workers = max(1, min(max(1, (os.cpu_count() or 2) - 2), n_tpl if n_tpl > 0 else 1))
    early_iou = min(0.99, float(nms_iou) * 2.0)
    done_count = 0

    if progress:
        progress(
            0.0,
            f"parallel match start — {n_tpl} templates, workers={workers}",
        )

    prev_threads = cv2.getNumThreads()
    cv2.setNumThreads(1)
    try:
        wave = 0
        for start in range(0, n_tpl, workers):
            wave += 1
            batch = list(enumerate(templates))[start : start + workers]
            n_waves = (n_tpl + workers - 1) // workers
            if progress:
                progress(
                    (start / max(n_tpl, 1)) * 88.0,
                    f"parallel match wave {wave}/{n_waves} "
                    f"({len(batch)} tpl, workers={workers}, peaks={len(all_boxes)}, "
                    f"RAM={_system_ram_percent():.0f}%)",
                )

            wave_label = f"parallel match wave {wave}/{n_waves}"
            wave_pct = (start / max(n_tpl, 1)) * 88.0
            with _Heartbeat(progress, wave_pct, wave_label, interval_s=2.0):
                with ThreadPoolExecutor(max_workers=len(batch)) as ex:
                    futs = {
                        ex.submit(
                            _match_one_template,
                            idx,
                            tpl,
                            channels_img,
                            threshold=threshold,
                            use_color=use_color,
                            ih=ih,
                            iw=iw,
                        ): idx
                        for idx, tpl in batch
                    }
                    for fut in as_completed(futs):
                        template_idx, boxes, scores, skip = fut.result()
                        done_count += 1
                        n_new = len(boxes)
                        raw_peaks_seen += n_new
                        if skip:
                            if progress:
                                progress(
                                    (done_count / max(n_tpl, 1)) * 88.0,
                                    f"template {template_idx + 1}/{n_tpl} {skip}",
                                )
                            continue
                        all_boxes.extend(boxes)
                        all_scores.extend(scores)
                        if progress:
                            progress(
                                (done_count / max(n_tpl, 1)) * 88.0,
                                f"template {template_idx + 1}/{n_tpl} done "
                                f"(+{n_new} peaks, buffer {len(all_boxes)}, "
                                f"raw {raw_peaks_seen})",
                            )

            # Mid-run NMS between waves: RAM pressure OR buffer size cap
            ram = _system_ram_percent()
            need_mid = len(all_boxes) >= EARLY_NMS_MIN_PEAKS and (
                ram >= RAM_TRIGGER_PERCENT or len(all_boxes) >= PEAK_BUFFER_CAP
            )
            if need_mid:
                before = len(all_boxes)
                reason = (
                    f"buffer≥{PEAK_BUFFER_CAP}"
                    if len(all_boxes) >= PEAK_BUFFER_CAP
                    else f"RAM {ram:.0f}%"
                )
                if progress:
                    progress(
                        (done_count / max(n_tpl, 1)) * 88.0,
                        f"mid-run NMS (iou={early_iou:.3f}=2×user, lighter) "
                        f"on {before} peaks — {reason}",
                    )

                def mid_prog(msg: str, _dc=done_count) -> None:
                    if progress:
                        progress((_dc / max(n_tpl, 1)) * 88.0, msg)

                all_boxes, all_scores = _prune_peaks(
                    all_boxes,
                    all_scores,
                    early_iou,
                    progress_callback=mid_prog,
                )
                if len(all_boxes) > PEAK_HARD_CAP:
                    before_top = len(all_boxes)
                    all_boxes, all_scores = _top_k_peaks(
                        all_boxes, all_scores, PEAK_HARD_CAP
                    )
                    if progress:
                        progress(
                            (done_count / max(n_tpl, 1)) * 88.0,
                            f"mid-run top-{PEAK_HARD_CAP}: "
                            f"{before_top} → {len(all_boxes)} peaks",
                        )
                if progress:
                    progress(
                        (done_count / max(n_tpl, 1)) * 88.0,
                        f"mid-run NMS (iou=2×user, lighter): "
                        f"pruned {before} → {len(all_boxes)} peaks "
                        f"(RAM {_system_ram_percent():.0f}%)",
                    )
    finally:
        cv2.setNumThreads(prev_threads)

    if raw_peaks_seen == 0 or not all_boxes:
        if progress:
            progress(100.0, "no peaks above threshold")
        return [], 0

    # Safety before final NMS — never hand tens of millions of peaks to the hasher
    if len(all_boxes) > PEAK_BUFFER_CAP:
        before = len(all_boxes)
        if progress:
            progress(
                88.5,
                f"pre-final prune (iou={early_iou:.3f}=2×user) on {before} peaks",
            )

        def pre_prog(msg: str) -> None:
            if progress:
                progress(89.0, msg)

        all_boxes, all_scores = _prune_peaks(
            all_boxes, all_scores, early_iou, progress_callback=pre_prog
        )
        if progress:
            progress(89.5, f"pre-final prune: {before} → {len(all_boxes)} peaks")
    if len(all_boxes) > PEAK_HARD_CAP:
        before = len(all_boxes)
        all_boxes, all_scores = _top_k_peaks(all_boxes, all_scores, PEAK_HARD_CAP)
        if progress:
            progress(
                89.7,
                f"pre-final top-{PEAK_HARD_CAP}: {before} → {len(all_boxes)} peaks",
            )

    if progress:
        progress(
            90.0,
            f"final spatial NMS on {len(all_boxes)} buffered peaks "
            f"(raw seen {raw_peaks_seen}, iou={nms_iou})",
        )

    detected = np.asarray(all_boxes, dtype=np.float32)
    scores_np = np.asarray(all_scores, dtype=np.float32)

    def nms_prog(msg: str) -> None:
        if progress:
            progress(92.0, msg)

    indices = optimized_spatial_nms(
        _boxes_xywh_to_xyxy(detected),
        scores_np,
        nms_iou,
        progress_callback=nms_prog,
    )
    out: list[dict] = []
    for i in indices:
        x, y, w, h = detected[i]
        out.append(
            {
                "bbox": (int(x), int(y), int(w), int(h)),
                "confidence": float(scores_np[i]),
                "template_index": 0,
            }
        )
    if progress:
        progress(100.0, f"NMS kept {len(out)} / {raw_peaks_seen} raw peaks")
    return out, raw_peaks_seen


def match_template_multichannel(
    image_rgb: np.ndarray,
    template_rgb: np.ndarray,
    *,
    threshold: float = 0.5,
    nms_iou: float = 0.05,
) -> list[dict]:
    dets, _ = match_templates(
        image_rgb, [template_rgb], threshold=threshold, nms_iou=nms_iou, use_color=True
    )
    return dets


def extract_patch(
    image: np.ndarray,
    col0: int,
    row0: int,
    col1: int,
    row1: int,
) -> np.ndarray | None:
    h, w = image.shape[:2]
    c0 = max(0, min(w - 1, col0))
    c1 = max(c0 + 1, min(w, col1))
    r0 = max(0, min(h - 1, row0))
    r1 = max(r0 + 1, min(h, row1))
    patch = image[r0:r1, c0:c1]
    if patch.size == 0 or patch.shape[0] < 2 or patch.shape[1] < 2:
        return None
    return patch.copy()


def extract_patch_rgb(
    image_rgb: np.ndarray,
    col0: int,
    row0: int,
    col1: int,
    row1: int,
) -> np.ndarray | None:
    return extract_patch(image_rgb, col0, row0, col1, row1)
