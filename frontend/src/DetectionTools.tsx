import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

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
  const [rows, setRows] = useState(4);
  const [cols, setCols] = useState(10);
  // Legacy suite defaults (per-modality template_matching_threshold)
  const [confidenceRgb, setConfidenceRgb] = useState(0.5);
  const [confidenceThermal, setConfidenceThermal] = useState(0.5);
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
      ? `all ${gridCellCount || "grid"} cells`
      : `${Math.min(numTemplates, gridCellCount || numTemplates)} of ${gridCellCount || "?"}`;

  return (
    <div className="tool-panel">
      <h3>Detection</h3>
      <p className="muted tool-hint">
        Full-ortho deskew + multi-template match. AOI/grid set angle and templates only. Confirm runs
        RGB + Thermal.
      </p>
      <p className="muted tool-hint">{status}</p>

      <div
        className="basemap-toggle"
        role="group"
        aria-label="Edit modality"
        title="Which modality’s AOI/grid you are editing. Switching also toggles orthomosaic opacity (RGB↔Thermal)."
      >
        <button
          type="button"
          className={modality === "rgb" ? "active" : ""}
          title="Edit RGB AOI and grid. Sets RGB orthomosaic to full opacity."
          onClick={() => {
            setModality("rgb");
            setEditCorners(false);
            setDrawEnabled(false);
          }}
        >
          RGB
        </button>
        <button
          type="button"
          className={modality === "thermal" ? "active" : ""}
          title="Edit thermal AOI and grid. Sets thermal orthomosaic to full opacity."
          onClick={() => {
            setModality("thermal");
            setEditCorners(false);
            setDrawEnabled(false);
          }}
        >
          Thermal
        </button>
      </div>

      <label
        className="tool-field row-check"
        title="Click four corners on the map to define the panel-block frame (AOI) for the active modality. Saved automatically on the 4th click."
      >
        <input
          type="checkbox"
          checked={drawEnabled}
          onChange={(e) => {
            setDrawEnabled(e.target.checked);
            if (e.target.checked) setEditCorners(false);
          }}
        />
        <span>Draw frame (4 corners)</span>
      </label>

      <label
        className="tool-field row-check"
        title="Drag the four AOI corner handles on the map. On release, the grid is regenerated for the active modality."
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
        <span>Edit frame corners</span>
      </label>

      <div
        className="tool-grid2"
        title="Seed grid size inside the AOI. Rows × cols cells are used as panel templates (or a subset if Templates > 0)."
      >
        <label
          className="tool-field"
          title="Number of panel rows in the seed grid (along the short side of the AOI)."
        >
          Rows
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
          title="Number of panel columns in the seed grid (along the long side of the AOI)."
        >
          Cols
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
        title={`Build a ${rows}×${cols} seed grid inside the ${modality.toUpperCase()} AOI. Deskew angle comes from the AOI; templates are cut from these cells.`}
        onClick={generateGrid}
      >
        Generate grid ({modality.toUpperCase()})
      </button>

      <button
        type="button"
        disabled={busy || !hasRgbGrid}
        title="Copy the RGB AOI + grid into the thermal modality (same corners/cells in WGS84). Then tweak thermal corners if needed."
        onClick={copyToThermal}
      >
        Copy RGB → Thermal
      </button>

      <label
        className="tool-field"
        title="RGB match threshold (0–1). Peaks below this are discarded during RGB detection. Default 0.5. Thermal often needs a lower value because grayscale scores run lower than multi-channel RGB."
      >
        RGB match confidence
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
        title="Thermal match threshold (0–1). Independent of RGB. Default 0.5 (same as legacy suite). Lower if thermal boxes disappear while RGB looks fine."
      >
        Thermal match confidence
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
        title="Non-maximum suppression IoU. Overlapping boxes with IoU above this are merged (higher score wins). Lower = keep more nearby boxes. Default 0.05."
      >
        NMS IoU (default 0.05)
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
        title="How many grid cells to use as templates. 0 = ALL cells (slowest, best coverage on large plants). Higher variety of templates helps when panels look different across the plant."
      >
        Templates (0 = all grid cells → {tplHint})
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
        title="Thermal only: clamp temperatures above this °C when building the search image (hot outliers). Does not affect RGB. Default 45°C."
      >
        Thermal temp cap °C (default 45)
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
        className="tool-field"
        title="Map-only filter for blue (RGB) boxes. Does not re-run detection. Default 0.7 (legacy visualization filter)."
      >
        Map filter RGB ≥ {displayConfidenceRgb.toFixed(2)}
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
        title="Map-only filter for orange (thermal) boxes. Does not re-run detection. Default 0.7. Thermal scores are often lower — lower this slider to show more thermal panels."
      >
        Map filter Thermal ≥ {displayConfidenceThermal.toFixed(2)}
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
        title={
          bothGridsReady
            ? "Run full-ortho deskew + multi-template matching on both RGB and thermal. Progress appears in the activity console (Verbose for per-template detail)."
            : "Confirm both RGB and thermal grids first (generate RGB grid, then Copy RGB → Thermal or generate thermal separately)."
        }
        onClick={runDetect}
      >
        {running ? "Detecting RGB + Thermal…" : "Confirm & detect (RGB + Thermal)"}
      </button>

      <div
        className="detection-counts"
        title="Panel counts after the last detection run (all saved boxes, before map filter)."
      >
        <span className="legend-item">
          <i className="legend-swatch legend-swatch-rgb" aria-hidden />
          RGB (blue): {rgbCount}
        </span>
        <span className="legend-item">
          <i className="legend-swatch legend-swatch-thermal" aria-hidden />
          Thermal (orange): {thermalCount}
        </span>
        <span className="muted" style={{ fontSize: "0.8rem" }}>
          {hasRgbGrid ? "RGB grid ✓" : "RGB grid ✗"} ·{" "}
          {hasThermalGrid ? "Thermal grid ✓" : "Thermal grid ✗"}
        </span>
      </div>

      <button
        type="button"
        className="ghost"
        disabled={busy}
        title={`Delete AOI, grid, and panels for ${modality.toUpperCase()} only. The other modality is left untouched.`}
        onClick={clearAll}
      >
        Clear ({modality.toUpperCase()})
      </button>
    </div>
  );
}
