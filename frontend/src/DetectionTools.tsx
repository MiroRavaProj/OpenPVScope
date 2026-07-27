import { useCallback, useEffect, useRef, useState } from "react";
import { api, DetectionParams } from "./api";
import { useT } from "./i18n";
import { NumberField } from "./ui/NumberField";
import { useMinimized } from "./ui/useMinimized";

export type DetectModality = "rgb" | "thermal";
export type DetectRunMode = "rgb" | "thermal" | "both";
export type ThermalMatchMode = "default" | "context_15" | "gradient";

const PARAM_SAVE_MS = 300;

export function DetectionTools(props: {
  onRefreshMap: () => void;
  onProjectRefresh: () => void;
  onError: (msg: string) => void;
  drawEnabled: boolean;
  setDrawEnabled: (v: boolean) => void;
  modality: DetectModality;
  setModality: (m: DetectModality) => void;
  editCorners: boolean;
  setEditCorners: (v: boolean) => void;
  displayConfidenceRgb: number;
  setDisplayConfidenceRgb: (v: number) => void;
  displayConfidenceThermal: number;
  setDisplayConfidenceThermal: (v: number) => void;
  thermalOnly?: boolean;
  /** Bump when AOI/grid changes outside this panel (e.g. map draw/edit). */
  statusEpoch?: number;
}) {
  const {
    onRefreshMap,
    onProjectRefresh,
    onError,
    drawEnabled,
    setDrawEnabled,
    modality,
    setModality,
    editCorners,
    setEditCorners,
    displayConfidenceRgb,
    setDisplayConfidenceRgb,
    displayConfidenceThermal,
    setDisplayConfidenceThermal,
    thermalOnly = false,
    statusEpoch = 0,
  } = props;
  const t = useT();
  const [rows, setRows] = useState(4);
  const [cols, setCols] = useState(10);
  const [confidenceRgb, setConfidenceRgb] = useState(0.65);
  const [confidenceThermal, setConfidenceThermal] = useState(0.65);
  const [advancedValidation, setAdvancedValidation] = useState(false);
  const [fineTuneConf, setFineTuneConf] = useState(0.65);
  const [keepHighConfOutliers, setKeepHighConfOutliers] = useState(false);
  const [minClusterSize, setMinClusterSize] = useState(12);
  const [dbscanMinSamples, setDbscanMinSamples] = useState(4);
  const [walkTolFrac, setWalkTolFrac] = useState(0.1);
  const [pitchSlack, setPitchSlack] = useState(0.05);
  const [fillConfidence, setFillConfidence] = useState(0.5);
  const [nms, setNms] = useState(0.05);
  const [numTemplates, setNumTemplates] = useState(0); // 0 = all grid cells
  const [thermalCap, setThermalCap] = useState(55);
  const [thermalMatchMode, setThermalMatchMode] = useState<ThermalMatchMode>("context_15");
  const [status, setStatus] = useState("");
  const [rgbCount, setRgbCount] = useState(0);
  const [thermalCount, setThermalCount] = useState(0);
  const [hasAoi, setHasAoi] = useState(false);
  const [hasGrid, setHasGrid] = useState(false);
  const [hasRgbGrid, setHasRgbGrid] = useState(false);
  const [hasThermalGrid, setHasThermalGrid] = useState(false);
  const [gridCellCount, setGridCellCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const paramsReady = useRef(false);
  const saveTimer = useRef<number | null>(null);

  function applyParams(p: DetectionParams) {
    setRows(p.rows);
    setCols(p.cols);
    setConfidenceRgb(p.confidence_rgb);
    setConfidenceThermal(p.confidence_thermal);
    setNms(p.nms_iou);
    setNumTemplates(p.num_templates);
    setThermalCap(p.thermal_temp_cap);
    setAdvancedValidation(p.advanced_validation);
    setFineTuneConf(p.fine_tuning_confidence);
    setThermalMatchMode(p.thermal_match_mode);
    setKeepHighConfOutliers(p.keep_high_conf_outliers);
    setMinClusterSize(p.min_cluster_size);
    setDbscanMinSamples(p.dbscan_min_samples);
    setWalkTolFrac(p.walk_tol_frac);
    setPitchSlack(p.pitch_slack);
    setFillConfidence(p.fill_confidence);
  }

  const refresh = useCallback(async () => {
    try {
      const st = await api.detectionStatus();
      setStatus(st.message);
      setRgbCount(st.rgb?.panel_count ?? (st.has_rgb_panels ? st.panel_count : 0));
      setThermalCount(st.thermal?.panel_count ?? 0);
      const mod = modality === "thermal" ? st.thermal : st.rgb;
      setHasAoi(Boolean(mod?.has_aoi ?? (modality === "rgb" && st.has_aoi)));
      setHasGrid(Boolean(mod?.has_grid ?? (modality === "rgb" && st.has_grid)));
      setHasRgbGrid(Boolean(st.rgb?.has_grid ?? st.has_grid));
      setHasThermalGrid(Boolean(st.thermal?.has_grid));
      setRunning(Boolean(st.job?.running));
      if (!paramsReady.current && st.params) {
        applyParams(st.params);
        paramsReady.current = true;
      }
    } catch (e) {
      onError(String(e));
    }
  }, [onError, modality]);

  useEffect(() => {
    void refresh();
  }, [refresh, statusEpoch]);

  useEffect(() => {
    if (!paramsReady.current) return;
    if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    saveTimer.current = window.setTimeout(() => {
      void api
        .putDetectionParams({
          rows,
          cols,
          confidence_rgb: confidenceRgb,
          confidence_thermal: confidenceThermal,
          nms_iou: nms,
          num_templates: numTemplates,
          thermal_temp_cap: thermalCap,
          advanced_validation: advancedValidation,
          fine_tuning_confidence: fineTuneConf,
          thermal_match_mode: thermalMatchMode,
          keep_high_conf_outliers: keepHighConfOutliers,
          min_cluster_size: minClusterSize,
          dbscan_min_samples: dbscanMinSamples,
          walk_tol_frac: walkTolFrac,
          pitch_slack: pitchSlack,
          fill_confidence: fillConfidence,
        })
        .catch(() => undefined);
    }, PARAM_SAVE_MS);
    return () => {
      if (saveTimer.current != null) window.clearTimeout(saveTimer.current);
    };
  }, [
    rows,
    cols,
    confidenceRgb,
    confidenceThermal,
    nms,
    numTemplates,
    thermalCap,
    advancedValidation,
    fineTuneConf,
    thermalMatchMode,
    keepHighConfOutliers,
    minClusterSize,
    dbscanMinSamples,
    walkTolFrac,
    pitchSlack,
    fillConfidence,
  ]);

  useEffect(() => {
    if (!hasGrid) {
      setGridCellCount(0);
      return;
    }
    void (async () => {
      try {
        const g = await api.detectionGeojson("grid", modality);
        setGridCellCount(g.features?.length ?? 0);
      } catch {
        setGridCellCount(0);
      }
    })();
  }, [hasGrid, modality, refresh]);

  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const job = await api.detectionJob();
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

  async function cancelDetect() {
    try {
      await api.cancelDetection();
    } catch (e) {
      onError(String(e));
    }
  }

  async function generateGrid() {
    setBusy(true);
    try {
      const result = await api.generateGrid(rows, cols, modality);
      if (result.suggested_thermal_temp_cap != null) {
        setThermalCap(result.suggested_thermal_temp_cap);
      }
      setEditCorners(false);
      await refresh();
      onRefreshMap();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function copyToThermal() {
    setBusy(true);
    try {
      const result = await api.copyGridToThermal();
      if (result.suggested_thermal_temp_cap != null) {
        setThermalCap(result.suggested_thermal_temp_cap);
      }
      setModality("thermal");
      await refresh();
      onRefreshMap();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runDetect() {
    setBusy(true);
    try {
      await api.runDetection({
        confidence_rgb: confidenceRgb,
        confidence_thermal: confidenceThermal,
        nms_iou: nms,
        num_templates: numTemplates,
        thermal_temp_cap: thermalCap,
        advanced_validation: advancedValidation,
        fine_tuning_confidence: fineTuneConf,
        keep_high_conf_outliers: keepHighConfOutliers,
        min_cluster_size: minClusterSize,
        dbscan_min_samples: dbscanMinSamples,
        walk_tol_frac: walkTolFrac,
        pitch_slack: pitchSlack,
        fill_confidence: fillConfidence,
        thermal_match_mode: thermalMatchMode,
        modality: thermalOnly ? "thermal" : "both",
      });
      setRunning(true);
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function clearAll() {
    setBusy(true);
    try {
      await api.clearDetection(modality);
      setEditCorners(false);
      await refresh();
      onRefreshMap();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const bothGridsReady = hasRgbGrid && hasThermalGrid;
  const canRun = thermalOnly ? hasThermalGrid : bothGridsReady;
  const tplHint =
    numTemplates <= 0
      ? t("detection.templatesAll", { count: gridCellCount || "grid" })
      : t("detection.templatesSome", {
          n: Math.min(numTemplates, gridCellCount || numTemplates),
          total: gridCellCount || "?",
        });
  const [toolsMin, setToolsMin] = useMinimized("det-tools", false);

  return (
    <div className={`tool-panel process-dock-section ${toolsMin ? "minimized" : "expanded"}`}>
      <div className="tool-panel-header">
        <h3>{t("detection.title")}</h3>
        <button
          type="button"
          className="ghost icon-btn"
          title={toolsMin ? t("detection.expand") : t("detection.minimize")}
          onClick={() => setToolsMin(!toolsMin)}
        >
          {toolsMin ? "▸" : "▾"}
        </button>
      </div>
      {!toolsMin && (
        <>
      <p className="muted tool-hint">
        {thermalOnly ? t("detection.hintThermalOnly") : t("detection.hint")}
      </p>
      <p className="muted tool-hint">{status}</p>

      {!thermalOnly && (
      <div
        className="basemap-toggle"
        role="group"
        aria-label={t("detection.modalityAria")}
        title={t("detection.modalityGroupTitle")}
      >
        <button
          type="button"
          className={modality === "rgb" ? "active" : ""}
          title={t("detection.rgbTitle")}
          onClick={() => {
            setModality("rgb");
            setEditCorners(false);
            setDrawEnabled(false);
          }}
        >
          {t("detection.rgb")}
        </button>
        <button
          type="button"
          className={modality === "thermal" ? "active" : ""}
          title={t("detection.thermalTitle")}
          onClick={() => {
            setModality("thermal");
            setEditCorners(false);
            setDrawEnabled(false);
          }}
        >
          {t("detection.thermal")}
        </button>
      </div>
      )}
      {thermalOnly && (
        <p className="muted tool-hint">{t("detection.thermalOnlyBadge")}</p>
      )}

      <label
        className="tool-field row-check"
        title={t("detection.drawFrameTitle")}
      >
        <input
          type="checkbox"
          checked={drawEnabled}
          onChange={(e) => {
            setDrawEnabled(e.target.checked);
            if (e.target.checked) setEditCorners(false);
          }}
        />
        <span>{t("detection.drawFrame")}</span>
      </label>

      <label
        className="tool-field row-check"
        title={t("detection.editCornersTitle")}
      >
        <input
          type="checkbox"
          checked={editCorners}
          disabled={!hasGrid}
          onChange={(e) => {
            setEditCorners(e.target.checked);
            if (e.target.checked) setDrawEnabled(false);
          }}
        />
        <span>{t("detection.editCorners")}</span>
      </label>

      <div
        className="tool-grid2"
        title={t("detection.gridSizeTitle")}
      >
        <label
          className="tool-field"
          title={t("detection.rowsTitle")}
        >
          {t("detection.rows")}
          <NumberField min={1} max={200} step={1} value={rows} onChange={setRows} />
        </label>
        <label
          className="tool-field"
          title={t("detection.colsTitle")}
        >
          {t("detection.cols")}
          <NumberField min={1} max={200} step={1} value={cols} onChange={setCols} />
        </label>
      </div>

      <button
        type="button"
        className={hasAoi && !hasGrid ? "primary" : undefined}
        disabled={busy || !hasAoi}
        title={
          hasAoi
            ? t("detection.generateGridTitle", { rows, cols, modality: modality.toUpperCase() })
            : t("detection.generateGridNeedAoi")
        }
        onClick={generateGrid}
      >
        {t("detection.generateGrid", { modality: modality.toUpperCase() })}
      </button>

      {!thermalOnly && (
      <button
        type="button"
        disabled={busy || !hasRgbGrid}
        title={t("detection.copyRgbThermalTitle")}
        onClick={copyToThermal}
      >
        {t("detection.copyRgbThermal")}
      </button>
      )}

      {!thermalOnly && (
      <label
        className="tool-field"
        title={t("detection.confRgbTitle")}
      >
        {t("detection.confRgb")}
        <NumberField
          min={0.1}
          max={0.99}
          step={0.01}
          value={confidenceRgb}
          onChange={setConfidenceRgb}
        />
      </label>
      )}
      <label
        className="tool-field"
        title={t("detection.confThermalTitle")}
      >
        {t("detection.confThermal")}
        <NumberField
          min={0.1}
          max={0.99}
          step={0.01}
          value={confidenceThermal}
          onChange={setConfidenceThermal}
        />
      </label>
      <div
        className="basemap-toggle"
        role="group"
        aria-label={t("detection.thermalMatchModeAria")}
        title={t("detection.thermalMatchModeTitle")}
      >
        <button
          type="button"
          className={thermalMatchMode === "default" ? "active" : ""}
          title={t("detection.thermalMatchDefaultTitle")}
          onClick={() => setThermalMatchMode("default")}
        >
          {t("detection.thermalMatchDefault")}
        </button>
        <button
          type="button"
          className={thermalMatchMode === "context_15" ? "active" : ""}
          title={t("detection.thermalMatchContextTitle")}
          onClick={() => setThermalMatchMode("context_15")}
        >
          {t("detection.thermalMatchContext")}
        </button>
        <button
          type="button"
          className={thermalMatchMode === "gradient" ? "active" : ""}
          title={t("detection.thermalMatchGradientTitle")}
          onClick={() => setThermalMatchMode("gradient")}
        >
          {t("detection.thermalMatchGradient")}
        </button>
      </div>
      <p className="muted tool-hint">{t("detection.thermalMatchModeHint")}</p>
      <label
        className="tool-field"
        title={t("detection.nmsTitle")}
      >
        {t("detection.nms")}
        <NumberField min={0.01} max={0.2} step={0.01} value={nms} onChange={setNms} />
      </label>
      <label
        className="tool-field"
        title={t("detection.templatesTitle")}
      >
        {t("detection.templates", { hint: tplHint })}
        <NumberField
          min={0}
          max={500}
          step={1}
          value={numTemplates}
          onChange={setNumTemplates}
        />
      </label>
      <label
        className="tool-field"
        title={t("detection.tempCapTitle")}
      >
        {t("detection.tempCap")}
        <NumberField min={10} max={70} step={1} value={thermalCap} onChange={setThermalCap} />
      </label>
      <label
        className="tool-field tool-check"
        title={t("detection.advancedValidationTitle")}
      >
        <input
          type="checkbox"
          checked={advancedValidation}
          onChange={(e) => setAdvancedValidation(e.target.checked)}
        />
        {t("detection.advancedValidation")}
      </label>
      {advancedValidation && (
        <div className="tool-advanced-block">
          <p className="muted tool-hint">{t("detection.advancedBlockHint")}</p>
          <label className="tool-field" title={t("detection.minClusterTitle")}>
            {t("detection.minCluster")}
            <NumberField
              min={3}
              max={200}
              step={1}
              value={minClusterSize}
              onChange={setMinClusterSize}
            />
          </label>
          <label className="tool-field" title={t("detection.dbscanMinTitle")}>
            {t("detection.dbscanMin")}
            <NumberField
              min={2}
              max={50}
              step={1}
              value={dbscanMinSamples}
              onChange={setDbscanMinSamples}
            />
          </label>
          <label className="tool-field" title={t("detection.fineTuneTitle")}>
            {t("detection.fineTune")}
            <NumberField
              min={0.1}
              max={0.99}
              step={0.01}
              value={fineTuneConf}
              onChange={setFineTuneConf}
            />
          </label>
          <label className="tool-field" title={t("detection.walkTolTitle")}>
            {t("detection.walkTol", { pct: Math.round(walkTolFrac * 100) })}
            <NumberField
              min={0.02}
              max={0.4}
              step={0.01}
              value={walkTolFrac}
              onChange={setWalkTolFrac}
            />
          </label>
          <label className="tool-field" title={t("detection.pitchSlackTitle")}>
            {t("detection.pitchSlack", { pct: Math.round(pitchSlack * 100) })}
            <NumberField
              min={0}
              max={0.3}
              step={0.01}
              value={pitchSlack}
              onChange={setPitchSlack}
            />
          </label>
          <label className="tool-field" title={t("detection.fillConfTitle")}>
            {t("detection.fillConf")}
            <NumberField
              min={0.05}
              max={0.99}
              step={0.01}
              value={fillConfidence}
              onChange={setFillConfidence}
            />
          </label>
          <label
            className="tool-field tool-check"
            title={t("detection.keepHighConfOutliersTitle")}
          >
            <input
              type="checkbox"
              checked={keepHighConfOutliers}
              onChange={(e) => setKeepHighConfOutliers(e.target.checked)}
            />
            {t("detection.keepHighConfOutliers")}
          </label>
        </div>
      )}
      {!thermalOnly && (
      <MapFilterSlider
        title={t("detection.mapFilterRgbTitle")}
        value={displayConfidenceRgb}
        onCommit={setDisplayConfidenceRgb}
        formatLabel={(v) => t("detection.mapFilterRgb", { value: v.toFixed(2) })}
      />
      )}
      <MapFilterSlider
        title={t("detection.mapFilterThermalTitle")}
        value={displayConfidenceThermal}
        onCommit={setDisplayConfidenceThermal}
        formatLabel={(v) => t("detection.mapFilterThermal", { value: v.toFixed(2) })}
      />

      <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
        <button
          type="button"
          className="primary"
          disabled={busy || running || !canRun}
          title={
            canRun
              ? thermalOnly
                ? t("detection.runTitleReadyThermal")
                : t("detection.runTitleReady")
              : thermalOnly
                ? t("detection.runTitleBlockedThermal")
                : t("detection.runTitleBlocked")
          }
          onClick={runDetect}
        >
          {running
            ? t("detection.running")
            : thermalOnly
              ? t("detection.runThermalOnly")
              : t("detection.run")}
        </button>
        {running && (
          <button type="button" onClick={() => void cancelDetect()} title={t("detection.cancelTitle")}>
            {t("detection.cancel")}
          </button>
        )}
      </div>

      <div
        className="detection-counts"
        title={t("detection.countsTitle")}
      >
        {!thermalOnly && (
        <span className="legend-item">
          <i className="legend-swatch legend-swatch-rgb" aria-hidden />
          {t("detection.legendRgb", { count: rgbCount })}
        </span>
        )}
        <span className="legend-item">
          <i className="legend-swatch legend-swatch-thermal" aria-hidden />
          {t("detection.legendThermal", { count: thermalCount })}
        </span>
        <span className="muted" style={{ fontSize: "0.8rem" }}>
          {thermalOnly
            ? hasThermalGrid
              ? t("detection.gridThermalOk")
              : t("detection.gridThermalMissing")
            : `${hasRgbGrid ? t("detection.gridRgbOk") : t("detection.gridRgbMissing")} · ${
                hasThermalGrid ? t("detection.gridThermalOk") : t("detection.gridThermalMissing")
              }`}
        </span>
      </div>

      <button
        type="button"
        className="ghost"
        disabled={busy}
        title={t("detection.clearTitle", { modality: modality.toUpperCase() })}
        onClick={clearAll}
      >
        {t("detection.clear", { modality: modality.toUpperCase() })}
      </button>
        </>
      )}
    </div>
  );
}

/** Range slider that only commits the value when the user releases (or finishes keyboard adjust). */
function MapFilterSlider(props: {
  title: string;
  value: number;
  onCommit: (v: number) => void;
  formatLabel: (v: number) => string;
}) {
  const { title, value, onCommit, formatLabel } = props;
  const [draft, setDraft] = useState(value);
  const draftRef = useRef(value);
  const dragging = useRef(false);

  useEffect(() => {
    if (!dragging.current) {
      draftRef.current = value;
      setDraft(value);
    }
  }, [value]);

  function commit() {
    dragging.current = false;
    const next = draftRef.current;
    if (next !== value) onCommit(next);
  }

  return (
    <label className="tool-field" title={title}>
      {formatLabel(draft)}
      <input
        type="range"
        min={0}
        max={0.99}
        step={0.01}
        value={draft}
        onPointerDown={() => {
          dragging.current = true;
        }}
        onChange={(e) => {
          const v = Number(e.target.value);
          draftRef.current = v;
          setDraft(v);
        }}
        onPointerUp={commit}
        onPointerCancel={commit}
        onBlur={commit}
        onKeyUp={(e) => {
          if (
            e.key === "ArrowLeft" ||
            e.key === "ArrowRight" ||
            e.key === "Home" ||
            e.key === "End" ||
            e.key === "PageUp" ||
            e.key === "PageDown"
          ) {
            commit();
          }
        }}
      />
    </label>
  );
}
