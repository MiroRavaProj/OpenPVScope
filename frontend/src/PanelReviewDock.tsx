import { useCallback, useEffect, useMemo, useState } from "react";
import { api, GeoJsonFc } from "./api";
import type { DetectModality } from "./DetectionTools";
import {
  FATE_COLORS,
  FATE_DEFAULT_INCLUDE,
  PANEL_FATES,
  PanelFate,
  isPanelFate,
} from "./panelFates";
import { useT } from "./i18n";
import { useMinimized } from "./ui/useMinimized";

type FateStats = { total: number; included: number };

export function PanelReviewDock(props: {
  modality: DetectModality;
  refreshKey: number;
  visibleFates: Record<PanelFate, boolean>;
  onVisibleFatesChange: (next: Record<PanelFate, boolean>) => void;
  onSelectionChanged: () => void;
  onError: (msg: string) => void;
}) {
  const t = useT();
  const [dockMin, setDockMin] = useMinimized("panel-review-dock", false);
  const [fc, setFc] = useState<GeoJsonFc | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    try {
      const data = await api.detectionGeojson("panels_all", props.modality);
      setFc(data);
    } catch (e) {
      setFc({ type: "FeatureCollection", features: [] });
      props.onError(String(e));
    }
  }, [props.modality, props.onError]);

  useEffect(() => {
    void reload();
  }, [reload, props.refreshKey, props.modality]);

  const stats = useMemo(() => {
    const byFate = Object.fromEntries(PANEL_FATES.map((f) => [f, { total: 0, included: 0 }])) as Record<
      PanelFate,
      FateStats
    >;
    let included = 0;
    for (const f of fc?.features || []) {
      const propsF = f.properties || {};
      const fateRaw = propsF.fate;
      const fate: PanelFate = isPanelFate(fateRaw) ? fateRaw : "kept";
      byFate[fate].total += 1;
      if (propsF.include) {
        byFate[fate].included += 1;
        included += 1;
      }
    }
    return { byFate, included, total: (fc?.features || []).length };
  }, [fc]);

  const patchSelection = async (body: {
    include_ids?: string[];
    exclude_ids?: string[];
    set_fate?: { fate: string; include: boolean };
    reset_defaults?: boolean;
  }) => {
    setBusy(true);
    try {
      await api.panelSelection({ modality: props.modality, ...body });
      await reload();
      props.onSelectionChanged();
    } catch (e) {
      props.onError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const fateIncludeAll = (fate: PanelFate): boolean | "mixed" => {
    const s = stats.byFate[fate];
    if (s.total === 0) return FATE_DEFAULT_INCLUDE[fate];
    if (s.included === 0) return false;
    if (s.included === s.total) return true;
    return "mixed";
  };

  if (stats.total === 0 && !dockMin) {
    // Still show dock header so user knows where it is after a run
  }

  return (
    <aside className={`review-dock process-dock-section ${dockMin ? "minimized" : "expanded"}`} aria-label={t("review.title")}>
      <div className="layer-dock-header">
        <div className="layer-dock-title">{t("review.title")}</div>
        <button
          type="button"
          className="ghost icon-btn"
          title={dockMin ? t("review.expand") : t("review.minimize")}
          onClick={() => setDockMin(!dockMin)}
        >
          {dockMin ? "▸" : "▾"}
        </button>
      </div>
      {!dockMin && (
        <div className="review-dock-body">
          <p className="review-hint">{t("review.hint")}</p>
          <div className="review-selected">
            {t("review.selected", { n: stats.included, total: stats.total })}
          </div>
          <ul className="review-fate-list">
            {PANEL_FATES.map((fate) => {
              const s = stats.byFate[fate];
              if (s.total === 0) return null;
              const vis = props.visibleFates[fate];
              const inc = fateIncludeAll(fate);
              return (
                <li key={fate} className="review-fate-row">
                  <span className="review-swatch" style={{ background: FATE_COLORS[fate] }} />
                  <span className="review-fate-label" title={t(`review.fate.${fate}`)}>
                    {t(`review.fate.${fate}`)}
                    <span className="review-fate-count">
                      {s.included}/{s.total}
                    </span>
                  </span>
                  <button
                    type="button"
                    className={`ghost icon-btn review-eye ${vis ? "on" : "off"}`}
                    title={t("review.toggleVisible")}
                    disabled={busy}
                    onClick={() =>
                      props.onVisibleFatesChange({
                        ...props.visibleFates,
                        [fate]: !vis,
                      })
                    }
                  >
                    {vis ? "👁" : "–"}
                  </button>
                  <input
                    type="checkbox"
                    title={t("review.toggleInclude")}
                    disabled={busy || s.total === 0}
                    checked={inc === true}
                    ref={(el) => {
                      if (el) el.indeterminate = inc === "mixed";
                    }}
                    onChange={(e) => {
                      void patchSelection({
                        set_fate: { fate, include: e.target.checked },
                      });
                    }}
                  />
                </li>
              );
            })}
          </ul>
          <div className="review-actions">
            <button
              type="button"
              className="ghost"
              disabled={busy || stats.total === 0}
              onClick={() => void patchSelection({ reset_defaults: true })}
            >
              {t("review.reset")}
            </button>
          </div>
        </div>
      )}
    </aside>
  );
}
