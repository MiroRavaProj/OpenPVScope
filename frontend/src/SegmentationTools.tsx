import { useCallback, useEffect, useMemo, useState } from "react";
import { api, GeoJsonFc } from "./api";
import { useT } from "./i18n";
import { ThermalDistributionPlot } from "./segmentation/ThermalDistributionPlot";
import {
  collectIndicatorValues,
  ColorRange,
  LABELABLE_INDICATORS,
  percentileRange,
  THERMAL_INDICATORS,
  ThermalIndicator,
} from "./segmentation/thermalColor";
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
}) {
  const { onRefreshMap, onProjectRefresh, onError, colorState, onColorStateChange } = props;
  const t = useT();
  const [status, setStatus] = useState("");
  const [count, setCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [margin, setMargin] = useState(0.2);
  const [minIou, setMinIou] = useState(0.1);
  const [labelMsg, setLabelMsg] = useState<string | null>(null);
  const [controlsMin, setControlsMin] = useMinimized("seg-controls", false);
  const [histMin, setHistMin] = useMinimized("seg-histogram", false);

  const values = useMemo(() => {
    if (!colorState.pairsFc) return [];
    return collectIndicatorValues(colorState.pairsFc.features || [], colorState.indicator);
  }, [colorState.pairsFc, colorState.indicator]);

  const refresh = useCallback(async () => {
    try {
      const st = await api.segmentationStatus();
      setStatus(st.message);
      setCount(st.pair_count);
      setRunning(Boolean(st.job?.running));
      if (st.pair_count > 0) {
        const fc = await api.segmentationPairsGeojson();
        const vals = collectIndicatorValues(fc.features || [], colorState.indicator);
        const range = colorState.colorRange ?? percentileRange(vals);
        onColorStateChange({ pairsFc: fc, colorRange: range });
      }
    } catch (e) {
      onError(String(e));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [onError, onColorStateChange]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
          if (job.error) onError(String(job.error));
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
    colorState.thermalColoring &&
    LABELABLE_INDICATORS.includes(colorState.indicator) &&
    colorState.colorRange != null &&
    count > 0;

  const hasPairs = count > 0 && colorState.colorRange != null;

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
            <div className="seg-dock-row seg-dock-row-2">
              <label
                className="tool-field"
                title={t("segmentation.marginTitle")}
              >
                {t("segmentation.margin")}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={margin}
                  disabled={busy || running}
                  onChange={(e) => setMargin(Number(e.target.value))}
                />
              </label>
              <label
                className="tool-field"
                title={t("segmentation.minIouTitle")}
              >
                {t("segmentation.minIou")}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.05}
                  value={minIou}
                  disabled={busy || running}
                  onChange={(e) => setMinIou(Number(e.target.value))}
                />
              </label>
            </div>
            <div className="seg-dock-actions">
              <button
                type="button"
                className="primary"
                disabled={busy || running}
                title={t("segmentation.runTitle")}
                onClick={run}
              >
                {running ? t("segmentation.extracting") : t("segmentation.run")}
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
              {t("segmentation.pairsHint", { count })}
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
