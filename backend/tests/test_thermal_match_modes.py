"""Unit tests for thermal match mode helpers."""

from __future__ import annotations

import numpy as np

from openpvscope.detection.template_match import (
    expand_bounds,
    gradient_magnitude_u8,
    match_templates,
    _recenter_boxes_xywh,
)


def test_expand_bounds_15pct() -> None:
    c0, r0, c1, r1 = expand_bounds(100, 200, 200, 300, 0.15)
    # width=100 → ±15; height=100 → ±15
    assert (c0, r0, c1, r1) == (85, 185, 215, 315)


def test_recenter_boxes_keeps_center() -> None:
    boxes = [[10.0, 20.0, 130.0, 130.0]]  # expanded
    out = _recenter_boxes_xywh(boxes, (100, 100))
    x, y, w, h = out[0]
    assert abs(w - 100) < 1e-6 and abs(h - 100) < 1e-6
    assert abs((x + w / 2) - (10 + 65)) < 1e-6
    assert abs((y + h / 2) - (20 + 65)) < 1e-6


def test_gradient_emphasizes_edges() -> None:
    img = np.zeros((64, 64), dtype=np.uint8)
    img[16:48, 16:48] = 180  # flat square
    g = gradient_magnitude_u8(img)
    # Interior of square should be low; border pixels higher
    interior = float(g[32, 32])
    border = float(g[16, 32])
    assert border > interior + 20


def test_match_templates_report_wh_shrinks_boxes() -> None:
    # Flat background with a bright 20x20 panel at (40,40)
    img = np.full((120, 120), 40, dtype=np.uint8)
    img[40:60, 40:60] = 120
    # Context template: 26x26 around the panel (≈ +15% each side of 20)
    tpl = img[37:63, 37:63].copy()
    assert tpl.shape == (26, 26)
    dets, peaks = match_templates(
        img,
        [tpl],
        threshold=0.7,
        nms_iou=0.05,
        use_color=False,
        report_wh=(20, 20),
    )
    assert peaks >= 1
    assert len(dets) >= 1
    x, y, w, h = dets[0]["bbox"]
    assert w == 20 and h == 20
    # Center should land near the panel center (50,50)
    cx, cy = x + w / 2, y + h / 2
    assert abs(cx - 50) <= 3
    assert abs(cy - 50) <= 3


def test_context_nms_uses_panel_size_not_expanded() -> None:
    """Adjacent +15% boxes overlap enough to NMS each other; panel-sized must both survive."""
    from openpvscope.detection.template_match import nms

    # Two abutting 20x20 panels → expanded 26x26 boxes with IoU > 0.05
    expanded = [
        [0.0, 0.0, 26.0, 26.0],
        [20.0, 0.0, 26.0, 26.0],
    ]
    scores = [0.9, 0.85]
    keep_expanded = nms(
        [(int(b[0]), int(b[1]), int(b[2]), int(b[3])) for b in expanded],
        scores,
        0.05,
    )
    assert len(keep_expanded) == 1  # checkerboard bug without shrink-first

    shrunk = _recenter_boxes_xywh(expanded, (20, 20))
    keep_shrunk = nms(
        [(int(round(b[0])), int(round(b[1])), int(round(b[2])), int(round(b[3]))) for b in shrunk],
        scores,
        0.05,
    )
    assert len(keep_shrunk) == 2
