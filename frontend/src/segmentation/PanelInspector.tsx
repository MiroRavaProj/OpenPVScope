import { useEffect, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";
import { ThermalImageViewer } from "./ThermalImageViewer";

export function PanelInspector(props: {
  panelId: string | null;
  minimized: boolean;
  onToggleMinimize: () => void;
  onClose: () => void;
  /** Inside the right seg-dock (no absolute positioning). */
  embedded?: boolean;
  thermalOnly?: boolean;
}) {
  const t = useT();
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

  const iou = num(meta?.iou);
  const conf = num(meta?.confidence);

  return (
    <div
      className={`panel-inspector ${props.embedded ? "embedded" : ""} ${props.minimized ? "minimized" : "expanded"}`}
    >
      <div className="panel-inspector-header">
        <strong title={props.panelId}>
          {t("inspector.pairTitle", { id: props.panelId.slice(0, 8) })}
        </strong>
        <div className="panel-header-actions">
          <button
            type="button"
            className="ghost icon-btn"
            title={props.minimized ? t("inspector.expand") : t("inspector.minimize")}
            onClick={props.onToggleMinimize}
          >
            {props.minimized ? "▸" : "▾"}
          </button>
          <button type="button" className="ghost" onClick={props.onClose}>
            {t("inspector.close")}
          </button>
        </div>
      </div>
      {!props.minimized && (
        <>
          {err && <p className="muted">{err}</p>}
          <div className="inspector-badges">
            <span className={`badge ${iou != null && iou > 0.8 ? "badge-ok" : "badge-warn"}`}>
              {t("inspector.iou", { value: iou != null ? `${(iou * 100).toFixed(0)}%` : t("common.emDash") })}
            </span>
            <span className="badge badge-info">
              {t("inspector.conf", { value: conf != null ? `${(conf * 100).toFixed(0)}%` : t("common.emDash") })}
            </span>
          </div>
          <div className="inspector-section">
            <div className="muted">{t("inspector.thermal")}</div>
            <ThermalImageViewer panelId={props.panelId} />
          </div>
          {!props.thermalOnly && (
          <div className="inspector-section">
            <div className="muted">{t("inspector.rgb")}</div>
            <img
              className="inspector-rgb"
              src={api.segmentationPreviewUrl(props.panelId, "rgb")}
              alt={t("inspector.rgbCropAlt")}
            />
          </div>
          )}
          {meta && (
            <dl className="inspector-stats">
              <div>
                <dt>{t("inspector.meanC")}</dt>
                <dd>{fmt(meta.mean_temperature)}</dd>
              </div>
              <div>
                <dt>{t("inspector.minMax")}</dt>
                <dd>
                  {fmt(meta.min_temperature)} / {fmt(meta.max_temperature)}
                </dd>
              </div>
              <div>
                <dt>{t("inspector.medianStd")}</dt>
                <dd>
                  {fmt(meta.median_temperature)} / {fmt(meta.std_temperature)}
                </dd>
              </div>
              <div>
                <dt>{t("inspector.variance")}</dt>
                <dd>{fmt(meta.var_temperature)}</dd>
              </div>
              {!props.thermalOnly && (
              <div>
                <dt>{t("inspector.matchIouM")}</dt>
                <dd>
                  {fmt(meta.iou)} / {fmt(meta.distance_m)}
                </dd>
              </div>
              )}
            </dl>
          )}
        </>
      )}
    </div>
  );
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function fmt(v: unknown) {
  if (typeof v !== "number" || Number.isNaN(v)) return "—";
  return v.toFixed(2);
}
