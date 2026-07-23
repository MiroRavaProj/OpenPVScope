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
  } = props;
  const [rows, setRows] = useState(4);
  const [cols, setCols] = useState(10);
  const [confidence, setConfidence] = useState(0.5);
  const [nms, setNms] = useState(0.05);
  const [status, setStatus] = useState("");
  const [rgbCount, setRgbCount] = useState(0);
  const [thermalCount, setThermalCount] = useState(0);
  const [hasAoi, setHasAoi] = useState(false);
  const [hasGrid, setHasGrid] = useState(false);
  const [hasRgbGrid, setHasRgbGrid] = useState(false);
  const [hasThermalGrid, setHasThermalGrid] = useState(false);
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
        /* ignore transient errors */
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
      await api.runDetection(confidence, nms);
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

  return (
    <div className="tool-panel">
      <h3>Detection</h3>
      <p className="muted tool-hint">
        Matching searches the deskewed AOI at full resolution; grid cells are templates only. Panels
        are drawn oriented like the grid. Confirm & detect always runs RGB and thermal.
      </p>
      <p className="muted tool-hint">{status}</p>

      <div className="basemap-toggle" role="group" aria-label="Edit modality">
        <button
          type="button"
          className={modality === "rgb" ? "active" : ""}
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
          onClick={() => {
            setModality("thermal");
            setEditCorners(false);
            setDrawEnabled(false);
          }}
        >
          Thermal
        </button>
      </div>

      <label className="tool-field row-check">
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

      <label className="tool-field row-check">
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

      <div className="tool-grid2">
        <label className="tool-field">
          Rows
          <input
            type="number"
            min={1}
            max={200}
            value={rows}
            onChange={(e) => setRows(Number(e.target.value))}
          />
        </label>
        <label className="tool-field">
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

      <button type="button" disabled={busy || !hasAoi} onClick={generateGrid}>
        Generate grid ({modality.toUpperCase()})
      </button>

      <button type="button" disabled={busy || !hasRgbGrid} onClick={copyToThermal}>
        Copy RGB → Thermal
      </button>

      <label className="tool-field">
        Confidence
        <input
          type="number"
          min={0.1}
          max={0.99}
          step={0.05}
          value={confidence}
          onChange={(e) => setConfidence(Number(e.target.value))}
        />
      </label>
      <label className="tool-field">
        NMS IoU
        <input
          type="number"
          min={0.01}
          max={0.9}
          step={0.01}
          value={nms}
          onChange={(e) => setNms(Number(e.target.value))}
        />
      </label>

      <button
        type="button"
        className="primary"
        disabled={busy || running || !bothGridsReady}
        title={
          bothGridsReady
            ? "Run detection on RGB and thermal"
            : "Confirm both RGB and thermal grids first (generate + copy)"
        }
        onClick={runDetect}
      >
        {running ? "Detecting RGB + Thermal…" : "Confirm & detect (RGB + Thermal)"}
      </button>

      <div className="muted" style={{ fontSize: "0.85rem" }}>
        RGB panels: {rgbCount} · Thermal panels: {thermalCount}
        {hasRgbGrid ? " · RGB grid ✓" : " · RGB grid ✗"}
        {hasThermalGrid ? " · Thermal grid ✓" : " · Thermal grid ✗"}
      </div>

      <button type="button" className="ghost" disabled={busy} onClick={clearAll}>
        Clear ({modality.toUpperCase()})
      </button>
    </div>
  );
}
