import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

export function PanelInspector(props: {
  panelId: string | null;
  onClose: () => void;
}) {
  const [meta, setMeta] = useState<Record<string, unknown> | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!props.panelId) {
      setMeta(null);
      return;
    }
    let cancelled = false;
    setErr(null);
    api
      .segmentationPanelMeta(props.panelId)
      .then((m) => {
        if (!cancelled) setMeta(m);
      })
      .catch((e) => {
        if (!cancelled) {
          setMeta(null);
          setErr(String(e));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [props.panelId]);

  if (!props.panelId) return null;

  const stats = meta as {
    mean_temperature?: number | null;
    min_temperature?: number | null;
    max_temperature?: number | null;
    median_temperature?: number | null;
    std_temperature?: number | null;
    confidence?: number | null;
  } | null;

  return (
    <div className="panel-inspector">
      <div className="panel-inspector-header">
        <strong>Panel {props.panelId}</strong>
        <button type="button" className="ghost" onClick={props.onClose}>
          Close
        </button>
      </div>
      {err && <p className="muted">{err}</p>}
      <div className="inspector-thumbs">
        <div>
          <div className="muted">RGB</div>
          <img src={api.segmentationPreviewUrl(props.panelId, "rgb")} alt="RGB crop" />
        </div>
        <div>
          <div className="muted">Thermal</div>
          <img src={api.segmentationPreviewUrl(props.panelId, "thermal")} alt="Thermal crop" />
        </div>
      </div>
      {stats && (
        <dl className="inspector-stats">
          <div>
            <dt>Mean °C</dt>
            <dd>{fmt(stats.mean_temperature)}</dd>
          </div>
          <div>
            <dt>Min / Max</dt>
            <dd>
              {fmt(stats.min_temperature)} / {fmt(stats.max_temperature)}
            </dd>
          </div>
          <div>
            <dt>Median / Std</dt>
            <dd>
              {fmt(stats.median_temperature)} / {fmt(stats.std_temperature)}
            </dd>
          </div>
          <div>
            <dt>Confidence</dt>
            <dd>{fmt(stats.confidence)}</dd>
          </div>
        </dl>
      )}
    </div>
  );
}

function fmt(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return "—";
  return typeof v === "number" ? v.toFixed(2) : String(v);
}

export function SegmentationTools(props: {
  selectedId: string | null;
  onSelect: (id: string | null) => void;
  onRefreshMap: () => void;
  onProjectRefresh: () => void;
  onError: (msg: string) => void;
}) {
  const { onRefreshMap, onProjectRefresh, onError } = props;
  const [status, setStatus] = useState("");
  const [count, setCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const st = await api.segmentationStatus();
      setStatus(st.message);
      setCount(st.pair_count);
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
        const job = await api.segmentationJob();
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

  async function run() {
    setBusy(true);
    try {
      await api.runSegmentation();
      setRunning(true);
    } catch (e) {
      onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <div className="tool-panel">
        <h3>Segmentation</h3>
        <p className="muted tool-hint">{status}</p>
        <button type="button" className="primary" disabled={busy || running} onClick={run}>
          {running ? "Extracting…" : "Run pairing & extract"}
        </button>
        <div className="muted" style={{ fontSize: "0.85rem" }}>
          Pairs: {count}
        </div>
        <p className="muted tool-hint">Click a panel on the map to inspect crops and stats.</p>
      </div>
      <PanelInspector panelId={props.selectedId} onClose={() => props.onSelect(null)} />
    </>
  );
}
