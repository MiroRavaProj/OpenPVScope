import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import { useT } from "./i18n";
import { useMinimized } from "./ui/useMinimized";

export type DetectModality = "rgb" | "thermal";
export type DetectRunMode = "rgb" | "thermal" | "both";

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
  } = props;
  const t = useT();
  const [rows, setRows] = useState(4);
  const [cols, setCols] = useState(10);
  // Legacy suite defaults (per-modality template_matching_threshold)
  const [confidenceRgb, setConfidenceRgb] = useState(0.5);
  const [confidenceThermal, setConfidenceThermal] = useState(0.5);
  const [advancedValidation, setAdvancedValidation] = useState(true);
  const [fineTuneConf, setFineTuneConf] = useState(0.65);
  const [nms, setNms] = useState(0.05);
  const [numTemplates, setNumTemplates] = useState(0); // 0 = all grid cells
  const [thermalCap, setThermalCap] = useState(45);
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
    } catch (e) {
      onError(String(e));
    }
  }, [onError, modality]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
  }, [running, refresh, onRefreshMap, onProjectRefresh]);

  async function generateGrid() {
    setBusy(true);
    try {
      await api.generateGrid(rows, cols, modality);
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
      await api.copyGridToThermal();
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
      <p className="muted tool-hint">{t("detection.hint")}</p>
      <p className="muted tool-hint">{status}</p>

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
          <input
            type="number"
            min={1}
            max={200}
            value={rows}
            onChange={(e) => setRows(Number(e.target.value))}
          />
        </label>
        <label
          className="tool-field"
          title={t("detection.colsTitle")}
        >
          {t("detection.cols")}
          <input
            type="number"
            min={1}
            max={200}
            value={cols}
            onChange={(e) => setCols(Number(e.target.value))}
          />
        </label>
      </div>

      <button
        type="button"
        disabled={busy || !hasAoi}
        title={t("detection.generateGridTitle", { rows, cols, modality: modality.toUpperCase() })}
        onClick={generateGrid}
      >
        {t("detection.generateGrid", { modality: modality.toUpperCase() })}
      </button>

      <button
        type="button"
        disabled={busy || !hasRgbGrid}
        title={t("detection.copyRgbThermalTitle")}
        onClick={copyToThermal}
      >
        {t("detection.copyRgbThermal")}
      </button>

      <label
        className="tool-field"
        title={t("detection.confRgbTitle")}
      >
        {t("detection.confRgb")}
        <input
          type="number"
          min={0.1}
          max={0.99}
          step={0.01}
          value={confidenceRgb}
          onChange={(e) => setConfidenceRgb(Number(e.target.value))}
        />
      </label>
      <label
        className="tool-field"
        title={t("detection.confThermalTitle")}
      >
        {t("detection.confThermal")}
        <input
          type="number"
          min={0.1}
          max={0.99}
          step={0.01}
          value={confidenceThermal}
          onChange={(e) => setConfidenceThermal(Number(e.target.value))}
        />
      </label>
      <label
        className="tool-field"
        title={t("detection.nmsTitle")}
      >
        {t("detection.nms")}
        <input
          type="number"
          min={0.01}
          max={0.2}
          step={0.01}
          value={nms}
          onChange={(e) => setNms(Number(e.target.value))}
        />
      </label>
      <label
        className="tool-field"
        title={t("detection.templatesTitle")}
      >
        {t("detection.templates", { hint: tplHint })}
        <input
          type="number"
          min={0}
          max={500}
          step={1}
          value={numTemplates}
          onChange={(e) => setNumTemplates(Number(e.target.value))}
        />
      </label>
      <label
        className="tool-field"
        title={t("detection.tempCapTitle")}
      >
        {t("detection.tempCap")}
        <input
          type="number"
          min={10}
          max={70}
          step={1}
          value={thermalCap}
          onChange={(e) => setThermalCap(Number(e.target.value))}
        />
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
      <label
        className="tool-field"
        title={t("detection.fineTuneTitle")}
      >
        {t("detection.fineTune")}
        <input
          type="number"
          min={0.1}
          max={0.99}
          step={0.01}
          value={fineTuneConf}
          disabled={!advancedValidation}
          onChange={(e) => setFineTuneConf(Number(e.target.value))}
        />
      </label>
      <label
        className="tool-field"
        title={t("detection.mapFilterRgbTitle")}
      >
        {t("detection.mapFilterRgb", { value: displayConfidenceRgb.toFixed(2) })}
        <input
          type="range"
          min={0}
          max={0.99}
          step={0.01}
          value={displayConfidenceRgb}
          onChange={(e) => setDisplayConfidenceRgb(Number(e.target.value))}
        />
      </label>
      <label
        className="tool-field"
        title={t("detection.mapFilterThermalTitle")}
      >
        {t("detection.mapFilterThermal", { value: displayConfidenceThermal.toFixed(2) })}
        <input
          type="range"
          min={0}
          max={0.99}
          step={0.01}
          value={displayConfidenceThermal}
          onChange={(e) => setDisplayConfidenceThermal(Number(e.target.value))}
        />
      </label>

      <button
        type="button"
        className="primary"
        disabled={busy || running || !bothGridsReady}
        title={bothGridsReady ? t("detection.runTitleReady") : t("detection.runTitleBlocked")}
        onClick={runDetect}
      >
        {running ? t("detection.running") : t("detection.run")}
      </button>

      <div
        className="detection-counts"
        title={t("detection.countsTitle")}
      >
        <span className="legend-item">
          <i className="legend-swatch legend-swatch-rgb" aria-hidden />
          {t("detection.legendRgb", { count: rgbCount })}
        </span>
        <span className="legend-item">
          <i className="legend-swatch legend-swatch-thermal" aria-hidden />
          {t("detection.legendThermal", { count: thermalCount })}
        </span>
        <span className="muted" style={{ fontSize: "0.8rem" }}>
          {hasRgbGrid ? t("detection.gridRgbOk") : t("detection.gridRgbMissing")} ·{" "}
          {hasThermalGrid ? t("detection.gridThermalOk") : t("detection.gridThermalMissing")}
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
