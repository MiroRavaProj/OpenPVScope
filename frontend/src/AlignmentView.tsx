import { useCallback, useEffect, useRef, useState } from "react";
import { AlignmentParams, AlignmentStatus, api, ProjectPayload } from "./api";
import { useConsole } from "./ActivityConsole";
import { useT } from "./i18n";

const MIN_SCALE = 0.02;
const MAX_SCALE = 64;
const ZOOM_FACTOR = 1.18;
const FETCH_DEBOUNCE_MS = 160;

function loadObjectUrl(url: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve();
    img.onerror = () => reject(new Error("Failed to decode tile"));
    img.src = url;
  });
}

const DEFAULT_PARAMS: AlignmentParams = {
  max_reg_gsd_m: 0.02,
  global_max_m: 2.5,
  local_max_m: 0.5,
  tile_m: 5.76,
  stride_m: 3.84,
};

function clamp(v: number, lo: number, hi: number) {
  return Math.max(lo, Math.min(hi, v));
}

/** Zoomable split compare: RGB left / thermal right with a movable divider. */
function SplitConfirmModal(props: {
  rgbWidth: number;
  rgbHeight: number;
  cacheKey: string | number;
  onConfirm: () => void;
  onCancel: () => void;
  busy: boolean;
  title?: string;
  metaSummary?: string | null;
}) {
  const t = useT();
  const viewportRef = useRef<HTMLDivElement>(null);
  const [scale, setScale] = useState(1);
  const [offset, setOffset] = useState({ x: 0, y: 0 });
  const [splitPct, setSplitPct] = useState(50);
  const [thermalOpacity, setThermalOpacity] = useState(100);
  const [thermalOnly, setThermalOnly] = useState(false);
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
  const splitDragRef = useRef(false);
  const fetchGen = useRef(0);
  const hasTilesRef = useRef(false);
  const scaleRef = useRef(scale);
  const offsetRef = useRef(offset);
  scaleRef.current = scale;
  offsetRef.current = offset;

  const effectiveSplit = thermalOnly ? 0 : splitPct;

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
    if (!vp || !ready || props.rgbWidth < 2) return;
    if (vp.clientWidth < 8 || vp.clientHeight < 8) return;
    // Keep the current raster while dragging; pointer-up / zoom settle will refetch.
    if (panRef.current?.active) return;

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
    // Only show loading on the first paint — never flash UI while panning/zooming.
    const initial = !hasTilesRef.current;
    if (initial) setLoading(true);
    setLoadError(null);
    let rgbObject: string | null = null;
    let thObject: string | null = null;
    try {
      // Aligned thermal is native-thermal resolution; reproject onto the RGB window.
      const [rgbRes, thRes] = await Promise.all([
        fetch(api.orthoWindowUrl("rgb", windowQ)),
        fetch(api.orthoMatchRgbWindowUrl("thermal_aligned", windowQ)),
      ]);
      if (!rgbRes.ok) throw new Error(`RGB window failed: ${await rgbRes.text()}`);
      if (!thRes.ok) throw new Error(`Thermal overlay failed: ${await thRes.text()}`);
      const [rgbBlob, thBlob] = await Promise.all([rgbRes.blob(), thRes.blob()]);
      if (gen !== fetchGen.current) return;
      rgbObject = URL.createObjectURL(rgbBlob);
      thObject = URL.createObjectURL(thBlob);
      // Decode before swapping so the viewport never blanks between tiles.
      await Promise.all([loadObjectUrl(rgbObject), loadObjectUrl(thObject)]);
      if (gen !== fetchGen.current) {
        URL.revokeObjectURL(rgbObject);
        URL.revokeObjectURL(thObject);
        return;
      }
      setRgbUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return rgbObject;
      });
      setThUrl((prev) => {
        if (prev) URL.revokeObjectURL(prev);
        return thObject;
      });
      hasTilesRef.current = true;
      setFrame({ col: left, row: top, w, h });
    } catch (e) {
      if (rgbObject) URL.revokeObjectURL(rgbObject);
      if (thObject) URL.revokeObjectURL(thObject);
      if (gen === fetchGen.current) setLoadError(String(e));
    } finally {
      if (gen === fetchGen.current && initial) setLoading(false);
    }
  }, [props.rgbWidth, props.rgbHeight, props.cacheKey, ready]);

  useEffect(() => {
    if (!ready) return;
    if (panRef.current?.active) return;
    const timer = window.setTimeout(() => void refresh(), FETCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
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

  function updateSplitFromClientX(clientX: number) {
    const vp = viewportRef.current;
    if (!vp) return;
    const rect = vp.getBoundingClientRect();
    const pct = ((clientX - rect.left) / Math.max(1, rect.width)) * 100;
    setSplitPct(clamp(pct, 0, 100));
    if (thermalOnly) setThermalOnly(false);
  }

  function onPointerDown(e: React.PointerEvent) {
    const target = e.target as HTMLElement;
    if (target.closest(".split-handle")) {
      e.preventDefault();
      e.stopPropagation();
      splitDragRef.current = true;
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      updateSplitFromClientX(e.clientX);
      return;
    }
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
    if (splitDragRef.current) {
      updateSplitFromClientX(e.clientX);
      return;
    }
    const pan = panRef.current;
    if (!pan?.active) return;
    setOffset({
      x: pan.origX + (e.clientX - pan.startX),
      y: pan.origY + (e.clientY - pan.startY),
    });
  }

  function onPointerUp(e: React.PointerEvent) {
    if (splitDragRef.current) {
      splitDragRef.current = false;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      return;
    }
    if (panRef.current?.active) {
      panRef.current = null;
      try {
        (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
      } catch {
        /* ignore */
      }
      void refresh();
    }
  }

  const imgStyle = frame
    ? {
        left: offset.x + frame.col * scale,
        top: offset.y + frame.row * scale,
        width: frame.w * scale,
        height: frame.h * scale,
      }
    : undefined;

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true">
      <div className="modal-card overlay-modal">
        <div className="modal-header">
          <div>
            <h2>{props.title ?? t("alignment.modalConfirmTitle")}</h2>
            <p className="muted" style={{ margin: 0 }}>
              {t("alignment.modalHint")}
              {props.metaSummary ? ` ${props.metaSummary}` : ""}
            </p>
          </div>
          <div className="row">
            <button type="button" onClick={props.onCancel} disabled={props.busy}>
              {t("alignment.modalBack")}
            </button>
            <button type="button" className="primary" onClick={props.onConfirm} disabled={props.busy}>
              {props.busy ? t("alignment.modalSaving") : t("alignment.modalSave")}
            </button>
          </div>
        </div>

        <div className="overlay-controls row">
          <label className="opacity-label">
            {t("alignment.modalThermalOpacity", { pct: thermalOpacity })}
            <input
              type="range"
              min={0}
              max={100}
              value={thermalOpacity}
              onChange={(e) => setThermalOpacity(Number(e.target.value))}
            />
          </label>
          <label className="check-label">
            <input
              type="checkbox"
              checked={thermalOnly}
              onChange={(e) => setThermalOnly(e.target.checked)}
            />
            {t("alignment.thermalOnly")}
          </label>
          <button type="button" onClick={() => fitToView()}>
            {t("alignment.fit")}
          </button>
        </div>

        <div
          className="ortho-viewport overlay-viewport split-viewport"
          ref={viewportRef}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onContextMenu={(e) => e.preventDefault()}
        >
          {loadError && (
            <div className="overlay-error">
              <strong>{t("alignment.modalLoadError")}</strong>
              <p>{loadError}</p>
              <button type="button" onClick={() => void refresh()}>
                {t("alignment.modalRetry")}
              </button>
            </div>
          )}
          {!loadError && !frame && (
            <div className="ortho-empty muted">
              {loading || !ready ? t("alignment.modalLoadingOverlay") : t("alignment.modalNoTiles")}
            </div>
          )}
          {frame && rgbUrl && (
            <div className="split-layer split-layer-rgb" style={{ clipPath: `inset(0 ${100 - effectiveSplit}% 0 0)` }}>
              <img
                className="ortho-hires"
                src={rgbUrl}
                alt={t("alignment.modalAltRgb")}
                draggable={false}
                style={imgStyle}
              />
            </div>
          )}
          {frame && thUrl && (
            <div
              className="split-layer split-layer-thermal"
              style={{
                clipPath: `inset(0 0 0 ${effectiveSplit}%)`,
                opacity: thermalOpacity / 100,
              }}
            >
              <img
                className="ortho-hires"
                src={thUrl}
                alt={t("alignment.modalAltThermal")}
                draggable={false}
                style={imgStyle}
              />
            </div>
          )}
          {frame && !thermalOnly && (
            <div className="split-handle" style={{ left: `${effectiveSplit}%` }} aria-hidden>
              <div className="split-handle-line" />
              <div className="split-handle-knob" title={t("alignment.splitDrag")}>
                ⟷
              </div>
            </div>
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
  const t = useT();
  const { noteLocal } = useConsole();
  const [params, setParams] = useState<AlignmentParams>({ ...DEFAULT_PARAMS });
  const [defaults, setDefaults] = useState<AlignmentParams>({ ...DEFAULT_PARAMS });
  const [meta, setMeta] = useState<{
    rgb: { width: number; height: number };
    lastMeta?: AlignmentStatus["meta"];
  }>({
    rgb: { width: 0, height: 0 },
  });
  const [busy, setBusy] = useState(false);
  const [showConfirm, setShowConfirm] = useState(false);
  const [alignmentDone, setAlignmentDone] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [cacheKey, setCacheKey] = useState<string | number>(() => Date.now());

  const loadStatus = useCallback(async () => {
    try {
      const st = await api.alignmentStatus();
      if (st.defaults) {
        setDefaults({ ...DEFAULT_PARAMS, ...st.defaults });
      }
      const done = st.status === "done" && st.has_aligned;
      setAlignmentDone(done);
      if (st.aligned_mtime_ns) setCacheKey(st.aligned_mtime_ns);
      setStatusMsg(st.message ?? null);
      const base = { ...DEFAULT_PARAMS, ...(st.defaults ?? {}) };
      if (st.params) {
        setParams({
          max_reg_gsd_m: st.params.max_reg_gsd_m ?? base.max_reg_gsd_m,
          global_max_m: st.params.global_max_m ?? base.global_max_m,
          local_max_m: st.params.local_max_m ?? base.local_max_m,
          tile_m: st.params.tile_m ?? base.tile_m,
          stride_m: st.params.stride_m ?? base.stride_m,
          nodata: st.params.nodata ?? base.nodata,
        });
      } else {
        setParams({ ...base });
      }
      if (st.meta) {
        setMeta((m) => ({ ...m, lastMeta: st.meta }));
      }
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    Promise.all([api.orthoMeta("rgb"), loadStatus()])
      .then(([rgb]) => {
        if (cancelled) return;
        setMeta((m) => ({
          ...m,
          rgb: { width: rgb.width, height: rgb.height },
        }));
      })
      .catch((e) => {
        if (!cancelled) props.onError(String(e));
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [props.project.manifest.id, props.project.root, loadStatus]);

  function setParam(key: keyof AlignmentParams, value: number) {
    setParams((p) => ({ ...p, [key]: value }));
  }

  async function preview() {
    if (
      !(params.max_reg_gsd_m > 0) ||
      !(params.global_max_m > 0) ||
      !(params.local_max_m > 0) ||
      !(params.tile_m > 0) ||
      !(params.stride_m > 0)
    ) {
      props.onError(t("alignment.errorBadParams"));
      return;
    }
    setBusy(true);
    noteLocal(t("alignment.consolePreviewTitle"), t("alignment.consolePreviewDetail"));
    try {
      const p = await api.previewAlignment(params);
      if (p.aligned_mtime_ns) setCacheKey(p.aligned_mtime_ns);
      else setCacheKey(Date.now());
      if (p.meta) setMeta((m) => ({ ...m, lastMeta: p.meta ?? null }));
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
    noteLocal(t("alignment.consoleSaveTitle"), t("alignment.consoleSaveDetail"));
    try {
      const p = await api.confirmAlignment();
      setAlignmentDone(true);
      setShowConfirm(false);
      setStatusMsg(t("alignment.statusAligned"));
      props.onApplied(p);
    } catch (e) {
      props.onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const last = meta.lastMeta;
  const metaSummary =
    last && typeof last.runtime_s === "number"
      ? t("alignment.metaSummary", {
          runtime: last.runtime_s.toFixed(1),
          ctrl: String(last.ctrl_pts ?? "—"),
          workGsd:
            typeof last.gsd_work_m === "number"
              ? (last.gsd_work_m * 100).toFixed(1)
              : "—",
          pass1: String(last.pass1 ?? "—"),
        })
      : null;

  return (
    <div className="ortho-alignment">
      <div className="card" style={{ marginBottom: "1rem", maxWidth: "none" }}>
        <h2>{t("alignment.title")}</h2>
        {alignmentDone ? (
          <p>
            <strong style={{ color: "var(--accent)" }}>{t("alignment.saved")}</strong>{" "}
            {statusMsg ? `${statusMsg}. ` : ""}
            {t("alignment.savedBody")}
          </p>
        ) : (
          <p>{t("alignment.intro")}</p>
        )}

        <div className="align-params">
          <label>
            {t("alignment.paramMaxRegGsd")}
            <input
              type="number"
              min={0.005}
              step={0.005}
              value={params.max_reg_gsd_m}
              onChange={(e) => setParam("max_reg_gsd_m", Number(e.target.value))}
            />
          </label>
          <label>
            {t("alignment.paramGlobalMax")}
            <input
              type="number"
              min={0.01}
              step={0.1}
              value={params.global_max_m}
              onChange={(e) => setParam("global_max_m", Number(e.target.value))}
            />
          </label>
          <label>
            {t("alignment.paramLocalMax")}
            <input
              type="number"
              min={0.01}
              step={0.05}
              value={params.local_max_m}
              onChange={(e) => setParam("local_max_m", Number(e.target.value))}
            />
          </label>
          <label>
            {t("alignment.paramTile")}
            <input
              type="number"
              min={0.01}
              step={0.01}
              value={params.tile_m}
              onChange={(e) => setParam("tile_m", Number(e.target.value))}
            />
          </label>
          <label>
            {t("alignment.paramStride")}
            <input
              type="number"
              min={0.01}
              step={0.01}
              value={params.stride_m}
              onChange={(e) => setParam("stride_m", Number(e.target.value))}
            />
          </label>
        </div>

        <div className="row" style={{ marginTop: "0.85rem" }}>
          <span className="muted">
            {meta.rgb.width > 0 ? `${meta.rgb.width}×${meta.rgb.height} px` : t("alignment.waitingOrtho")}
            {alignmentDone ? ` ${t("alignment.statusDone")}` : ""}
            {metaSummary ? ` · ${metaSummary}` : ""}
          </span>
          <button
            type="button"
            onClick={() => setParams({ ...defaults })}
            disabled={busy}
          >
            {t("alignment.resetDefaults")}
          </button>
          {alignmentDone && (
            <button
              type="button"
              disabled={busy || meta.rgb.width < 2}
              onClick={() => setShowConfirm(true)}
            >
              {t("alignment.reviewOverlay")}
            </button>
          )}
          <button
            type="button"
            className="primary"
            disabled={busy || meta.rgb.width < 2}
            onClick={() => void preview()}
          >
            {busy
              ? t("alignment.previewRunning")
              : alignmentDone
                ? t("alignment.rePreview")
                : t("alignment.preview")}
          </button>
        </div>
      </div>

      {showConfirm && meta.rgb.width >= 2 && (
        <SplitConfirmModal
          rgbWidth={meta.rgb.width}
          rgbHeight={meta.rgb.height}
          cacheKey={cacheKey}
          busy={busy}
          metaSummary={metaSummary}
          title={alignmentDone ? t("alignment.modalReviewTitle") : t("alignment.modalConfirmTitle")}
          onCancel={() => setShowConfirm(false)}
          onConfirm={() => void confirm()}
        />
      )}
    </div>
  );
}

export const AlignmentView = OrthoAlignmentView;
