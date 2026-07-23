import { useCallback, useEffect, useRef, useState } from "react";
import { api, ProjectPayload } from "./api";
import { useConsole } from "./ActivityConsole";

type Pt = { x: number; y: number };

const MIN_SCALE = 0.02;
const MAX_SCALE = 64;
const ZOOM_FACTOR = 1.18;
const FETCH_DEBOUNCE_MS = 90;

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

type HiResFrame = {
  url: string;
  col: number;
  row: number;
  width: number;
  height: number;
};

function OrthoPane(props: {
  title: string;
  layerId: "rgb" | "thermal";
  imageWidth: number;
  imageHeight: number;
  points: Pt[];
  onAddPoint: (pt: Pt) => void;
  accent?: string;
  readOnly?: boolean;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [hiRes, setHiRes] = useState<HiResFrame | null>(null);
  const [loading, setLoading] = useState(false);
  const panRef = useRef<{
    active: boolean;
    startX: number;
    startY: number;
    origX: number;
    origY: number;
  } | null>(null);
  const didPanRef = useRef(false);
  const fetchGen = useRef(0);
  const scaleRef = useRef(scale);
  const offsetRef = useRef(offset);
  scaleRef.current = scale;
  offsetRef.current = offset;

  const fitToView = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || props.imageWidth < 2 || props.imageHeight < 2) return;
    const pad = 16;
    const sx = (vp.clientWidth - pad) / props.imageWidth;
    const sy = (vp.clientHeight - pad) / props.imageHeight;
    const s = clamp(Math.min(sx, sy), MIN_SCALE, 4);
    setScale(s);
    setOffset({
      x: (vp.clientWidth - props.imageWidth * s) / 2,
      y: (vp.clientHeight - props.imageHeight * s) / 2,
    });
  }, [props.imageWidth, props.imageHeight]);

  useEffect(() => {
    fitToView();
  }, [fitToView, props.layerId]);

  useEffect(() => {
    const onResize = () => fitToView();
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [fitToView]);

  const refreshHiRes = useCallback(async () => {
    const vp = viewportRef.current;
    if (!vp || props.imageWidth < 2) return;
    const s = scaleRef.current;
    const off = offsetRef.current;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);

    const left = Math.max(0, Math.floor(-off.x / s));
    const top = Math.max(0, Math.floor(-off.y / s));
    const right = Math.min(props.imageWidth, Math.ceil((vp.clientWidth - off.x) / s));
    const bottom = Math.min(props.imageHeight, Math.ceil((vp.clientHeight - off.y) / s));
    const w = Math.max(1, right - left);
    const h = Math.max(1, bottom - top);
    const outW = clamp(Math.round(w * s * dpr), 64, 4096);
    const outH = clamp(Math.round(h * s * dpr), 64, 4096);

    const gen = ++fetchGen.current;
    setLoading(true);
    try {
      const url = api.orthoWindowUrl(props.layerId, {
        col_off: left,
        row_off: top,
        width: w,
        height: h,
        out_w: outW,
        out_h: outH,
      });
      const res = await fetch(url);
      if (!res.ok) throw new Error(await res.text());
      const blob = await res.blob();
      if (gen !== fetchGen.current) return;
      const objectUrl = URL.createObjectURL(blob);
      setHiRes((prev) => {
        if (prev?.url) URL.revokeObjectURL(prev.url);
        return { url: objectUrl, col: left, row: top, width: w, height: h };
      });
    } catch (e) {
      console.error(e);
    } finally {
      if (gen === fetchGen.current) setLoading(false);
    }
  }, [props.layerId, props.imageWidth, props.imageHeight]);

  useEffect(() => {
    const t = window.setTimeout(() => void refreshHiRes(), FETCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [scale, offset, refreshHiRes]);

  useEffect(() => {
    return () => {
      setHiRes((prev) => {
        if (prev?.url) URL.revokeObjectURL(prev.url);
        return null;
      });
    };
  }, []);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheelNative = (e: WheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR;
      const rect = el.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      setScale((prev) => {
        const newScale = clamp(prev * factor, MIN_SCALE, MAX_SCALE);
        setOffset((off) => {
          const imgX = (mx - off.x) / prev;
          const imgY = (my - off.y) / prev;
          return { x: mx - imgX * newScale, y: my - imgY * newScale };
        });
        return newScale;
      });
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
  }, []);

  function zoomAt(clientX: number, clientY: number, factor: number) {
    const vp = viewportRef.current;
    if (!vp) return;
    const rect = vp.getBoundingClientRect();
    const mx = clientX - rect.left;
    const my = clientY - rect.top;
    setScale((prev) => {
      const newScale = clamp(prev * factor, MIN_SCALE, MAX_SCALE);
      setOffset((off) => {
        const imgX = (mx - off.x) / prev;
        const imgY = (my - off.y) / prev;
        return { x: mx - imgX * newScale, y: my - imgY * newScale };
      });
      return newScale;
    });
  }

  function screenToImage(clientX: number, clientY: number): Pt | null {
    const vp = viewportRef.current;
    if (!vp || props.imageWidth < 2) return null;
    const rect = vp.getBoundingClientRect();
    const mx = clientX - rect.left;
    const my = clientY - rect.top;
    const x = (mx - offset.x) / scale;
    const y = (my - offset.y) / scale;
    if (x < 0 || y < 0 || x > props.imageWidth || y > props.imageHeight) return null;
    return { x, y };
  }

  function onPointerDown(e: React.PointerEvent) {
    if (e.button === 1 || (e.button === 0 && (e.altKey || e.shiftKey))) {
      e.preventDefault();
      didPanRef.current = false;
      panRef.current = {
        active: true,
        startX: e.clientX,
        startY: e.clientY,
        origX: offset.x,
        origY: offset.y,
      };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    const pan = panRef.current;
    if (!pan?.active) return;
    const dx = e.clientX - pan.startX;
    const dy = e.clientY - pan.startY;
    if (Math.hypot(dx, dy) > 3) didPanRef.current = true;
    setOffset({ x: pan.origX + dx, y: pan.origY + dy });
  }

  function onPointerUp(e: React.PointerEvent) {
    if (panRef.current?.active) {
      panRef.current = null;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
    }
  }

  function onClick(e: React.MouseEvent) {
    if (props.readOnly) return;
    if (didPanRef.current) {
      didPanRef.current = false;
      return;
    }
    if (e.altKey || e.shiftKey || e.button !== 0) return;
    if (props.points.length >= 4) return;
    const pt = screenToImage(e.clientX, e.clientY);
    if (pt) props.onAddPoint(pt);
  }

  const centerZoom = (factor: number) => {
    const vp = viewportRef.current;
    if (!vp) return;
    const r = vp.getBoundingClientRect();
    zoomAt(r.left + r.width / 2, r.top + r.height / 2, factor);
  };

  return (
    <div className="ortho-pane">
      <div className="ortho-pane-toolbar">
        <span className="ortho-pane-title">{props.title}</span>
        <div className="row" style={{ gap: "0.35rem" }}>
          <button type="button" onClick={() => centerZoom(1 / ZOOM_FACTOR)}>
            −
          </button>
          <span className="muted" style={{ minWidth: "4rem", textAlign: "center" }}>
            {scale >= 1 ? `${Math.round(scale * 100)}%` : `${(scale * 100).toFixed(1)}%`}
            {loading ? " …" : ""}
          </span>
          <button type="button" onClick={() => centerZoom(ZOOM_FACTOR)}>
            +
          </button>
          <button type="button" onClick={fitToView}>
            Fit
          </button>
        </div>
      </div>
      <div
        className="ortho-viewport"
        ref={viewportRef}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onClick={onClick}
        onContextMenu={(e) => e.preventDefault()}
        style={{ cursor: props.readOnly ? "grab" : "crosshair" }}
      >
        {hiRes && (
          <img
            className="ortho-hires"
            src={hiRes.url}
            alt=""
            draggable={false}
            style={{
              left: offset.x + hiRes.col * scale,
              top: offset.y + hiRes.row * scale,
              width: hiRes.width * scale,
              height: hiRes.height * scale,
            }}
          />
        )}
        {props.points.map((p, i) => (
          <div
            key={i}
            className="point-marker"
            style={{
              left: offset.x + p.x * scale,
              top: offset.y + p.y * scale,
              ["--marker-accent" as string]: props.accent ?? "var(--danger)",
            }}
          >
            <span className="point-marker-dot" />
            <span className="point-marker-label">{i + 1}</span>
          </div>
        ))}
        {!hiRes && !loading && <div className="ortho-empty muted">Waiting for orthophoto…</div>}
      </div>
      <div className="ortho-hint muted">
        Scroll = zoom · Shift/Alt+drag or middle-click = pan
        {!props.readOnly && ` · Click = point ${props.points.length}/4`}
      </div>
    </div>
  );
}

/** Zoomable overlay of RGB + aligned thermal in RGB pixel / geo space. */
function OverlayConfirmModal(props: {
  rgbWidth: number;
  rgbHeight: number;
  rgbTransform: number[];
  cacheKey: string | number;
  onConfirm: () => void;
  onCancel: () => void;
  busy: boolean;
  title?: string;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [rgbOpacity, setRgbOpacity] = useState(50);
  const [thermalOpacity, setThermalOpacity] = useState(50);
  const [rgbUrl, setRgbUrl] = useState<string | null>(null);
  const [thUrl, setThUrl] = useState<string | null>(null);
  const [frame, setFrame] = useState<{ col: number; row: number; w: number; h: number } | null>(
    null,
  );
  const [loading, setLoading] = useState(false);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const panRef = useRef<{
    active: boolean;
    startX: number;
    startY: number;
    origX: number;
    origY: number;
  } | null>(null);
  const fetchGen = useRef(0);
  const scaleRef = useRef(scale);
  const offsetRef = useRef(offset);
  scaleRef.current = scale;
  offsetRef.current = offset;

  const fitToView = useCallback(() => {
    const vp = viewportRef.current;
    if (!vp || props.rgbWidth < 2) return false;
    if (vp.clientWidth < 8 || vp.clientHeight < 8) return false;
    const pad = 16;
    const s = clamp(
      Math.min((vp.clientWidth - pad) / props.rgbWidth, (vp.clientHeight - pad) / props.rgbHeight),
      MIN_SCALE,
      4,
    );
    setScale(s);
    setOffset({
      x: (vp.clientWidth - props.rgbWidth * s) / 2,
      y: (vp.clientHeight - props.rgbHeight * s) / 2,
    });
    setReady(true);
    return true;
  }, [props.rgbWidth, props.rgbHeight]);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp) return;
    const ro = new ResizeObserver(() => {
      fitToView();
    });
    ro.observe(vp);
    // Layout may settle one frame after mount
    const id = window.requestAnimationFrame(() => {
      fitToView();
    });
    return () => {
      ro.disconnect();
      window.cancelAnimationFrame(id);
    };
  }, [fitToView]);

  const refresh = useCallback(async () => {
    const vp = viewportRef.current;
    if (!vp || !ready || props.rgbWidth < 2 || props.rgbTransform.length < 6) return;
    if (vp.clientWidth < 8 || vp.clientHeight < 8) return;

    const s = scaleRef.current;
    const off = offsetRef.current;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const left = Math.max(0, Math.floor(-off.x / s));
    const top = Math.max(0, Math.floor(-off.y / s));
    const right = Math.min(props.rgbWidth, Math.ceil((vp.clientWidth - off.x) / s));
    const bottom = Math.min(props.rgbHeight, Math.ceil((vp.clientHeight - off.y) / s));
    const w = Math.max(1, right - left);
    const h = Math.max(1, bottom - top);
    const outW = clamp(Math.round(w * s * dpr), 64, 4096);
    const outH = clamp(Math.round(h * s * dpr), 64, 4096);
    const windowQ = {
      col_off: left,
      row_off: top,
      width: w,
      height: h,
      out_w: outW,
      out_h: outH,
      v: props.cacheKey,
    };

    const gen = ++fetchGen.current;
    setLoading(true);
    setLoadError(null);
    try {
      // Same RGB pixel window for both: thermal is reprojected onto that exact grid
      // so overlays stay locked at every zoom (not just when zoomed in).
      const [rgbRes, thRes] = await Promise.all([
        fetch(api.orthoWindowUrl("rgb", windowQ)),
        fetch(api.orthoMatchRgbWindowUrl("thermal_aligned", windowQ)),
      ]);
      if (!rgbRes.ok) throw new Error(`RGB window failed: ${await rgbRes.text()}`);
      if (!thRes.ok) throw new Error(`Thermal overlay failed: ${await thRes.text()}`);
      const [rgbBlob, thBlob] = await Promise.all([rgbRes.blob(), thRes.blob()]);
      if (gen !== fetchGen.current) return;
      const rgbObject = URL.createObjectURL(rgbBlob);
      const thObject = URL.createObjectURL(thBlob);
      setRgbUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return rgbObject;
      });
      setThUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return thObject;
      });
      setFrame({ col: left, row: top, w, h });
    } catch (e) {
      if (gen === fetchGen.current) setLoadError(String(e));
    } finally {
      if (gen === fetchGen.current) setLoading(false);
    }
  }, [props.rgbWidth, props.rgbHeight, props.cacheKey, ready]);

  useEffect(() => {
    if (!ready) return;
    const t = window.setTimeout(() => void refresh(), FETCH_DEBOUNCE_MS);
    return () => window.clearTimeout(t);
  }, [scale, offset, refresh, ready]);

  useEffect(() => {
    return () => {
      setRgbUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
      setThUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return null;
      });
    };
  }, []);

  useEffect(() => {
    const el = viewportRef.current;
    if (!el) return;
    const onWheelNative = (e: WheelEvent) => {
      e.preventDefault();
      const factor = e.deltaY < 0 ? ZOOM_FACTOR : 1 / ZOOM_FACTOR;
      const rect = el.getBoundingClientRect();
      const mx = e.clientX - rect.left;
      const my = e.clientY - rect.top;
      setScale((prev) => {
        const newScale = clamp(prev * factor, MIN_SCALE, MAX_SCALE);
        setOffset((off) => {
          const imgX = (mx - off.x) / prev;
          const imgY = (my - off.y) / prev;
          return { x: mx - imgX * newScale, y: my - imgY * newScale };
        });
        return newScale;
      });
    };
    el.addEventListener("wheel", onWheelNative, { passive: false });
    return () => el.removeEventListener("wheel", onWheelNative);
  }, []);

  function onPointerDown(e: React.PointerEvent) {
    if (e.button === 0 || e.button === 1) {
      e.preventDefault();
      panRef.current = {
        active: true,
        startX: e.clientX,
        startY: e.clientY,
        origX: offset.x,
        origY: offset.y,
      };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    }
  }

  function onPointerMove(e: React.PointerEvent) {
    const pan = panRef.current;
    if (!pan?.active) return;
    setOffset({
      x: pan.origX + (e.clientX - pan.startX),
      y: pan.origY + (e.clientY - pan.startY),
    });
  }

  function onPointerUp(e: React.PointerEvent) {
    if (panRef.current?.active) {
      panRef.current = null;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
    }
  }

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card overlay-modal">
        <div className="modal-header">
          <div>
            <h2>{props.title ?? "Confirm ortho alignment"}</h2>
            <p className="muted" style={{ margin: 0 }}>
              Check the overlay. Drag to pan, scroll to zoom. Adjust opacities (default 50%).
              {loading ? " Loading tiles…" : ""}
            </p>
          </div>
          <div className="row">
            <button type="button" onClick={props.onCancel} disabled={props.busy}>
              Back to editing
            </button>
            <button type="button" className="primary" onClick={props.onConfirm} disabled={props.busy}>
              {props.busy ? "Saving…" : "Save alignment"}
            </button>
          </div>
        </div>

        <div className="overlay-controls row">
          <label className="opacity-label">
            RGB {rgbOpacity}%
            <input
              type="range"
              min={0}
              max={100}
              value={rgbOpacity}
              onChange={(e) => setRgbOpacity(Number(e.target.value))}
            />
          </label>
          <label className="opacity-label">
            Thermal {thermalOpacity}%
            <input
              type="range"
              min={0}
              max={100}
              value={thermalOpacity}
              onChange={(e) => setThermalOpacity(Number(e.target.value))}
            />
          </label>
          <button type="button" onClick={() => fitToView()}>
            Fit
          </button>
        </div>

        <div
          className="ortho-viewport overlay-viewport"
          ref={viewportRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onContextMenu={(e) => e.preventDefault()}
        >
          {loadError && (
            <div className="overlay-error">
              <strong>Could not load overlay</strong>
              <p>{loadError}</p>
              <button type="button" onClick={() => void refresh()}>
                Retry
              </button>
            </div>
          )}
          {!loadError && !frame && (
            <div className="ortho-empty muted">{loading || !ready ? "Loading overlay…" : "No tiles"}</div>
          )}
          {frame && rgbUrl && (
            <img
              className="ortho-hires"
              src={rgbUrl}
              alt="RGB"
              draggable={false}
              style={{
                left: offset.x + frame.col * scale,
                top: offset.y + frame.row * scale,
                width: frame.w * scale,
                height: frame.h * scale,
                opacity: rgbOpacity / 100,
              }}
            />
          )}
          {frame && thUrl && (
            <img
              className="ortho-hires"
              src={thUrl}
              alt="Thermal aligned"
              draggable={false}
              style={{
                left: offset.x + frame.col * scale,
                top: offset.y + frame.row * scale,
                width: frame.w * scale,
                height: frame.h * scale,
                opacity: thermalOpacity / 100,
                mixBlendMode: "normal",
              }}
            />
          )}
        </div>
      </div>
    </div>
  );
}

export function OrthoAlignmentView(props: {
  project: ProjectPayload;
  onApplied: (p: ProjectPayload) => void;
  onError: (msg: string) => void;
}) {
  const { noteLocal } = useConsole();
  const [refPts, setRefPts] = useState<Pt[]>([]);
  const [tgtPts, setTgtPts] = useState<Pt[]>([]);
  const [meta, setMeta] = useState<{
    rgb: { width: number; height: number; transform: number[] };
    thermal: { width: number; height: number };
  }>({
    rgb: { width: 0, height: 0, transform: [] },
    thermal: { width: 0, height: 0 },
  });
  const [busy, setBusy] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [alignmentDone, setAlignmentDone] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [cacheKey, setCacheKey] = useState<string | number>(() => Date.now());

  const loadStatus = useCallback(async () => {
    try {
      const st = await api.alignmentStatus();
      const done = st.status === "done" && st.has_aligned;
      setAlignmentDone(done);
      if (st.aligned_mtime_ns) setCacheKey(st.aligned_mtime_ns);
      setStatusMsg(st.message ?? null);
      if (st.gcps?.ref_points?.length && st.gcps?.target_points?.length) {
        setRefPts(st.gcps.ref_points.map(([x, y]) => ({ x, y })));
        setTgtPts(st.gcps.target_points.map(([x, y]) => ({ x, y })));
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.orthoMeta("rgb"), api.orthoMeta("thermal"), loadStatus()])
      .then(([rgb, th]) => {
        if (cancelled) return;
        setMeta({
          rgb: { width: rgb.width, height: rgb.height, transform: rgb.transform ?? [] },
          thermal: { width: th.width, height: th.height },
        });
      })
      .catch((e) => {
        if (!cancelled) props.onError(String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.project.manifest.id, props.project.root, loadStatus]);

  async function preview() {
    if (refPts.length < 4 || tgtPts.length < 4) {
      props.onError("Place 4 corresponding points (1–4) on both RGB and thermal orthophotos.");
      return;
    }
    setBusy(true);
    noteLocal("Ortho alignment preview", "Computing transform and writing aligned GeoTIFF…");
    try {
      const p = await api.previewAlignment(
        refPts.map((pt) => [pt.x, pt.y]),
        tgtPts.map((pt) => [pt.x, pt.y]),
      );
      if (p.aligned_mtime_ns) setCacheKey(p.aligned_mtime_ns);
      else setCacheKey(Date.now());
      props.onApplied(p);
      setShowConfirm(true);
    } catch (e) {
      props.onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function confirm() {
    setBusy(true);
    noteLocal("Saving alignment", "Marking ortho alignment complete…");
    try {
      const p = await api.confirmAlignment();
      setAlignmentDone(true);
      setShowConfirm(false);
      setStatusMsg("Thermal georef aligned to RGB");
      props.onApplied(p);
    } catch (e) {
      props.onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="ortho-alignment">
      <div className="card" style={{ marginBottom: "1rem", maxWidth: "none" }}>
        <h2>Ortho alignment</h2>
        {alignmentDone ? (
          <p>
            <strong style={{ color: "var(--accent)" }}>Alignment saved.</strong>{" "}
            {statusMsg ? `${statusMsg}. ` : ""}
            Control points below are restored from the project. You can review the overlay or redo
            the points.
          </p>
        ) : (
          <p>
            Place four homologous control points (1–4) on RGB and thermal. Zoom reads from the full
            GeoTIFF. You will confirm the result on an overlay before saving.
          </p>
        )}
        <div className="row">
          <span className="muted">
            RGB {refPts.length}/4 · Thermal {tgtPts.length}/4
            {meta.rgb.width > 0
              ? ` · ${meta.rgb.width}×${meta.rgb.height} / ${meta.thermal.width}×${meta.thermal.height} px`
              : ""}
            {alignmentDone ? " · status: done" : ""}
          </span>
          <button
            type="button"
            onClick={() => {
              setRefPts([]);
              setTgtPts([]);
              setAlignmentDone(false);
            }}
          >
            Reset points
          </button>
          {alignmentDone && (
            <button
              type="button"
              disabled={busy || !meta.rgb.transform.length}
              onClick={() => setShowConfirm(true)}
            >
              Review overlay
            </button>
          )}
          <button
            type="button"
            className="primary"
            disabled={busy || refPts.length < 4 || tgtPts.length < 4}
            onClick={preview}
          >
            {alignmentDone ? "Re-preview alignment" : "Preview alignment"}
          </button>
        </div>
      </div>
      <div className="align-grid">
        <OrthoPane
          title="RGB (reference)"
          layerId="rgb"
          imageWidth={meta.rgb.width}
          imageHeight={meta.rgb.height}
          points={refPts}
          accent="var(--rgb)"
          onAddPoint={(pt) => setRefPts((p) => (p.length >= 4 ? p : [...p, pt]))}
        />
        <OrthoPane
          title="Thermal (target)"
          layerId="thermal"
          imageWidth={meta.thermal.width}
          imageHeight={meta.thermal.height}
          points={tgtPts}
          accent="var(--thermal)"
          onAddPoint={(pt) => setTgtPts((p) => (p.length >= 4 ? p : [...p, pt]))}
        />
      </div>

      {showConfirm && meta.rgb.transform.length >= 6 && (
        <OverlayConfirmModal
          rgbWidth={meta.rgb.width}
          rgbHeight={meta.rgb.height}
          rgbTransform={meta.rgb.transform}
          cacheKey={cacheKey}
          busy={busy}
          title={alignmentDone ? "Review ortho alignment" : "Confirm ortho alignment"}
          onCancel={() => setShowConfirm(false)}
          onConfirm={confirm}
        />
      )}
    </div>
  );
}

export const AlignmentView = OrthoAlignmentView;
