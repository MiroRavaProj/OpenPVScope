import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function DetectionTools(props: {
  onRefreshMap: () => void;
  onProjectRefresh: () => void;
  onError: (msg: string) => void;
  drawEnabled: boolean;
  setDrawEnabled: (v: boolean) => void;
}) {
  const { onRefreshMap, onProjectRefresh, onError, drawEnabled, setDrawEnabled } = props;
  const [rows, setRows] = useState(4);
  const [cols, setCols] = useState(10);
  const [confidence, setConfidence] = useState(0.55);
  const [nms, setNms] = useState(0.15);
  const [status, setStatus] = useState<string>("");
  const [panelCount, setPanelCount] = useState(0);
  const [hasAoi, setHasAoi] = useState(false);
  const [hasGrid, setHasGrid] = useState(false);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const st = await api.detectionStatus();
      setStatus(st.message);
      setPanelCount(st.panel_count);
      setHasAoi(st.has_aoi);
      setHasGrid(st.has_grid);
      setRunning(Boolean(st.job?.running));
    } catch (e) {
      onError(String(e));
    }
  }, [onError]);

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
      await api.generateGrid(rows, cols);
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
      await api.clearDetection();
      await refresh();
      onRefreshMap();
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="tool-panel">
      <h3>Detection</h3>
      <p className="muted tool-hint">{status || "Draw a 4-corner AOI on the map."}</p>

      <label className="tool-field row-check">
        <input
          type="checkbox"
          checked={drawEnabled}
          onChange={(e) => setDrawEnabled(e.target.checked)}
        />
        <span>Draw AOI (4 corners)</span>
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
        Generate grid
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
          step={0.05}
          value={nms}
          onChange={(e) => setNms(Number(e.target.value))}
        />
      </label>

      <button
        type="button"
        className="primary"
        disabled={busy || running || !hasGrid}
        onClick={runDetect}
      >
        {running ? "Detecting…" : "Confirm & detect"}
      </button>

      <div className="muted" style={{ fontSize: "0.85rem" }}>
        Panels: {panelCount}
        {hasAoi ? " · AOI ✓" : ""}
        {hasGrid ? " · Grid ✓" : ""}
      </div>

      <button type="button" className="ghost" disabled={busy} onClick={clearAll}>
        Clear
      </button>
    </div>
  );
}
