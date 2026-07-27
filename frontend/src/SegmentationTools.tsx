import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, GeoJsonFc } from "./api";
import { useT } from "./i18n";

const PARAM_SAVE_MS = 300;
import { ThermalDistributionPlot } from "./segmentation/ThermalDistributionPlot";
import {
  collectIndicatorValues,
  ColorRange,
  LABELABLE_INDICATORS,
  percentileRange,
  THERMAL_INDICATORS,
  ThermalIndicator,
} from "./segmentation/thermalColor";
import { NumberField } from "./ui/NumberField";
import { useMinimized } from "./ui/useMinimized";

const INDICATOR_KEY: Record<ThermalIndicator, string> = {
  max_temperature: "segmentation.indicatorMax",
  min_temperature: "segmentation.indicatorMin",
  mean_temperature: "segmentation.indicatorMean",
  median_temperature: "segmentation.indicatorMedian",
  std_temperature: "segmentation.indicatorStd",
  var_temperature: "segmentation.indicatorVariance",
};

export type SegColorState = {
  thermalColoring: boolean;
  indicator: ThermalIndicator;
  colorRange: ColorRange | null;
  pairsFc: GeoJsonFc | null;
};

export function SegmentationTools(props: {
  onRefreshMap: () => void;
  onProjectRefresh: () => void;
  onError: (msg: string) => void;
  colorState: SegColorState;
  onColorStateChange: (patch: Partial<SegColorState>) => void;
  thermalOnly?: boolean;
}) {
  const {
    onRefreshMap,
    onProjectRefresh,
    onError,
    colorState,
    onColorStateChange,
    thermalOnly = false,
  } = props;
  const t = useT();
  const [status, setStatus] = useState("");
  const [count, setCount] = useState(0);
  const [previewCount, setPreviewCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [margin, setMargin] = useState(0.2);
  const [minIou, setMinIou] = useState(0.75);
  const [labelMsg, setLabelMsg] = useState<string | null>(null);
  const [controlsMin, setControlsMin] = useMinimized("seg-controls", false);
  const [histMin, setHistMin] = useMinimized("seg-histogram", false);
  const paramsReady = useRef(false);
  const saveTimer = useRef<number | null>(null);
  /** Full greedy candidate pairs (min_iou=0) — filter locally; never re-pair on slider move. */
  const allCandidatesRef = useRef<GeoJsonFc | null>(null);
  /** User moved Min IoU — show filtered candidates instead of last extract. */
  const iouTouched = useRef(false);
  /** True after extract finishes — histogram/labels use real thermal stats. */
  const [extracted, setExtracted] = useState(false);

  const values = useMemo(() => {
    if (!colorState.pairsFc || !extracted) return [];
    return collectIndicatorValues(colorState.pairsFc.features || [], colorState.indicator);
  }, [colorState.pairsFc, colorState.indicator, extracted]);

  const applyIouFilter = useCallback(
    (iou: number, all?: GeoJsonFc | null) => {
      const src = all ?? allCandidatesRef.current;
      if (!src) return;
      const features = (src.features || []).filter((f) => {
        const v = f.properties?.iou;
        return typeof v === "number" ? v >= iou : true;
      });
      setPreviewCount(features.length);
      setExtracted(false);
      onColorStateChange({
        pairsFc: { type: "FeatureCollection", features },
        colorRange: null,
      });
    },
    [onColorStateChange],
  );

  const minIouRef = useRef(minIou);
  minIouRef.current = minIou;

  const loadCandidates = useCallback(async () => {
    if (thermalOnly) return;
    try {
      const fc = await api.segmentationPairPreview();
      allCandidatesRef.current = fc;
      applyIouFilter(minIouRef.current, fc);
    } catch (e) {
      allCandidatesRef.current = null;
      setPreviewCount(0);
      onColorStateChange({ pairsFc: { type: "FeatureCollection", features: [] }, colorRange: null });
      onError(String(e));
    }
  }, [thermalOnly, applyIouFilter, onColorStateChange, onError]);

  const refresh = useCallback(async () => {
    try {
      const st = await api.segmentationStatus();
      setStatus(st.message);
      setCount(st.pair_count);
      setRunning(Boolean(st.job?.running));
      if (!paramsReady.current && st.params) {
        setMargin(st.params.margin_factor);
        setMinIou(st.params.min_iou);
        paramsReady.current = true;
      }
      if (st.pair_count > 0 && !iouTouched.current) {
        const fc = await api.segmentationPairsGeojson();
        const vals = collectIndicatorValues(fc.features || [], colorState.indicator);
        const range = colorState.colorRange ?? percentileRange(vals);
        setExtracted(true);
        setPreviewCount(st.pair_count);
        onColorStateChange({ pairsFc: fc, colorRange: range });
        // Warm candidate cache in background for when the user moves IoU.
        if (!thermalOnly) {
          void api.segmentationPairPreview().then((c) => {
            allCandidatesRef.current = c;
          });
        }
      } else if (!thermalOnly) {
        await loadCandidates();
      }
    } catch (e) {
      onError(String(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onError, onColorStateChange, loadCandidates, thermalOnly]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  useEffect(() => {
    if (!paramsReady.current) return;
    if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void api
        .putSegmentationParams({ margin_factor: margin, min_iou: minIou })
        .catch(() => undefined);
    }, PARAM_SAVE_MS);
    return () => {
      if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    };
  }, [margin, minIou]);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const job = await api.segmentationJob();
        if (cancelled) return;
        if (!job.running) {
          setRunning(false);
          if (job.error && !job.cancelled) onError(String(job.error));
          await refresh();
          onRefreshMap();
          onProjectRefresh();
          return;
        }
      } catch {
        /* ignore */
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), 900);
    };

    void poll();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [running, refresh, onRefreshMap, onProjectRefresh, onError]);

  async function cancelRun() {
    try {
      await api.cancelSegmentation();
    } catch (e) {
      onError(String(e));
    }
  }

  async function removeIsolated() {
    setBusy(true);
    setLabelMsg(null);
    try {
      const r = await api.removeIsolatedPanels();
      iouTouched.current = true;
      await loadCandidates();
      if (thermalOnly) {
        await refresh();
      }
      onRefreshMap();
      onProjectRefresh();
      setLabelMsg(
        t("segmentation.isolatedRemoved", {
          rgb: r.removed_rgb,
          thermal: r.removed_thermal,
          pairs: r.removed_pairs,
        }),
      );
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  function setIndicator(ind: ThermalIndicator) {
    const vals = colorState.pairsFc
      ? collectIndicatorValues(colorState.pairsFc.features || [], ind)
      : [];
    onColorStateChange({
      indicator: ind,
      colorRange: percentileRange(vals),
    });
  }

  async function run() {
    setBusy(true);
    setLabelMsg(null);
    try {
      await api.runSegmentation({
        margin_factor: margin,
        min_iou: minIou,
        search_radius_m: null,
      });
      setRunning(true);
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveLabels() {
    if (!colorState.colorRange) return;
    if (!LABELABLE_INDICATORS.includes(colorState.indicator)) return;
    setBusy(true);
    setLabelMsg(null);
    try {
      const r = await api.saveSegmentationLabels({
        indicator: colorState.indicator,
        green: colorState.colorRange.min,
        red: colorState.colorRange.max,
      });
      setLabelMsg(
        t("segmentation.labelsSaved", {
          labeled: r.labeled,
          label0: r.label_0,
          labelMid: r.label_mid,
          label1: r.label_1,
        }),
      );
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const canLabel =
    extracted &&
    colorState.thermalColoring &&
    LABELABLE_INDICATORS.includes(colorState.indicator) &&
    colorState.colorRange != null &&
    count > 0;

  const hasPairs = extracted && count > 0 && colorState.colorRange != null;

  return (
    <>
      <section className={`process-dock-section seg-dock-controls ${controlsMin ? "minimized" : ""}`}>
        <div className="seg-dock-section-header">
          <h3>{t("segmentation.title")}</h3>
          <button
            type="button"
            className="ghost icon-btn"
            title={controlsMin ? t("segmentation.expandControls") : t("segmentation.minimizeControls")}
            onClick={() => setControlsMin(!controlsMin)}
          >
            {controlsMin ? "▸" : "▾"}
          </button>
        </div>
        {!controlsMin && (
          <div className="seg-dock-section-body">
            <p className="muted tool-hint">{status}</p>
            <div className="seg-dock-row">
              <label
                className="tool-field"
                title={t("segmentation.marginTitle")}
              >
                {t("segmentation.margin")}
                <NumberField
                  min={0}
                  max={1}
                  step={0.05}
                  value={margin}
                  disabled={busy || running}
                  onChange={setMargin}
                />
              </label>
            </div>
            {!thermalOnly && (
              <label className="tool-field" title={t("segmentation.minIouTitle")}>
                {t("segmentation.minIou", { value: minIou.toFixed(2) })}
                <input
                  type="range"
                  min={0}
                  max={0.99}
                  step={0.01}
                  value={minIou}
                  disabled={busy || running}
                  onChange={(e) => {
                    const v = Number(e.target.value);
                    iouTouched.current = true;
                    setMinIou(v);
                    // Instant local filter — pairing already cached at min_iou=0.
                    if (allCandidatesRef.current) applyIouFilter(v);
                    else void loadCandidates();
                  }}
                />
              </label>
            )}
            <div className="seg-dock-actions">
              <button
                type="button"
                className="primary"
                disabled={busy || running}
                title={thermalOnly ? t("segmentation.runTitleThermal") : t("segmentation.runTitle")}
                onClick={run}
              >
                {running
                  ? t("segmentation.extracting")
                  : thermalOnly
                    ? t("segmentation.runThermal")
                    : t("segmentation.run")}
              </button>
              {running && (
                <button
                  type="button"
                  onClick={() => void cancelRun()}
                  title={t("segmentation.cancelTitle")}
                >
                  {t("segmentation.cancel")}
                </button>
              )}
              <button
                type="button"
                disabled={busy || running}
                title={t("segmentation.removeIsolatedTitle")}
                onClick={() => void removeIsolated()}
              >
                {t("segmentation.removeIsolated")}
              </button>
              <button
                type="button"
                disabled={busy || !canLabel}
                title={t("segmentation.saveLabelsTitle")}
                onClick={() => void saveLabels()}
              >
                {t("segmentation.saveLabels")}
              </button>
            </div>
            {labelMsg && <p className="muted tool-hint">{labelMsg}</p>}
            <p className="muted tool-hint">
              {thermalOnly
                ? t("segmentation.panelsHint", { count })
                : extracted
                  ? t("segmentation.pairsHint", { count })
                  : t("segmentation.previewHint", { count: previewCount })}
            </p>
          </div>
        )}
      </section>

      {hasPairs && (
        <section className={`process-dock-section seg-dock-histogram ${histMin ? "minimized" : ""}`}>
          <div className="seg-dock-section-header">
            <h3>{t("segmentation.histTitle")}</h3>
            <button
              type="button"
              className="ghost icon-btn"
              title={histMin ? t("segmentation.expandHist") : t("segmentation.minimizeHist")}
              onClick={() => setHistMin(!histMin)}
            >
              {histMin ? "▸" : "▾"}
            </button>
          </div>
          {!histMin && (
            <div className="seg-dock-section-body seg-dock-hist-body">
              <div className="seg-dock-row">
                <label
                  className="tool-field tool-check"
                  title={t("segmentation.thermalColoringTitle")}
                >
                  <input
                    type="checkbox"
                    checked={colorState.thermalColoring}
                    onChange={(e) => onColorStateChange({ thermalColoring: e.target.checked })}
                  />
                  {t("segmentation.thermalColoring")}
                </label>
                <label
                  className="tool-field"
                  title={t("segmentation.indicatorTitle")}
                >
                  {t("segmentation.indicator")}
                  <select
                    value={colorState.indicator}
                    disabled={!colorState.thermalColoring}
                    onChange={(e) => setIndicator(e.target.value as ThermalIndicator)}
                  >
                    {THERMAL_INDICATORS.map((o) => (
                      <option key={o.id} value={o.id}>
                        {t(INDICATOR_KEY[o.id])}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <ThermalDistributionPlot
                values={values}
                range={colorState.colorRange!}
                onRangeChange={(r) => onColorStateChange({ colorRange: r })}
              />
              <p className="muted tool-hint">
                {t("segmentation.histHint")}
              </p>
            </div>
          )}
        </section>
      )}
    </>
  );
}
