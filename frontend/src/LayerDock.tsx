import { useT } from "./i18n";
import { useMinimized } from "./ui/useMinimized";

export type Basemap = "osm" | "satellite";

export function LayerDock(props: {
  basemap: Basemap;
  onBasemapChange: (b: Basemap) => void;
  rgbOpacity: number;
  onRgbOpacityChange: (v: number) => void;
  thermalOpacity: number;
  onThermalOpacityChange: (v: number) => void;
  onFitBounds: () => void;
  hideRgb?: boolean;
}) {
  const [dockMin, setDockMin] = useMinimized("layer-dock", false);
  const t = useT();

  return (
    <div className={`layer-dock process-dock-section ${dockMin ? "minimized" : "expanded"}`}>
      <div className="layer-dock-header">
        <div className="layer-dock-title">{t("layers.title")}</div>
        <button
          type="button"
          className="ghost icon-btn"
          title={dockMin ? t("layers.expand") : t("layers.minimize")}
          onClick={() => setDockMin(!dockMin)}
        >
          {dockMin ? "▸" : "▾"}
        </button>
      </div>
      {!dockMin && (
        <>
          <div
            className="basemap-toggle"
            role="group"
            aria-label={t("layers.basemapAria")}
            title={t("layers.basemapTitle")}
          >
            <button
              type="button"
              className={props.basemap === "osm" ? "active" : ""}
              title={t("layers.streetsTitle")}
              onClick={() => props.onBasemapChange("osm")}
            >
              {t("layers.streets")}
            </button>
            <button
              type="button"
              className={props.basemap === "satellite" ? "active" : ""}
              title={t("layers.satelliteTitle")}
              onClick={() => props.onBasemapChange("satellite")}
            >
              {t("layers.satellite")}
            </button>
          </div>
          {!props.hideRgb && (
          <label
            className="layer-row"
            title={t("layers.rgbOpacityTitle")}
          >
            <span>{t("layers.rgbOpacity", { pct: Math.round(props.rgbOpacity * 100) })}</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={props.rgbOpacity}
              onChange={(e) => props.onRgbOpacityChange(Number(e.target.value))}
            />
          </label>
          )}
          <label
            className="layer-row"
            title={t("layers.thermalOpacityTitle")}
          >
            <span>{t("layers.thermalOpacity", { pct: Math.round(props.thermalOpacity * 100) })}</span>
            <input
              type="range"
              min={0}
              max={1}
              step={0.05}
              value={props.thermalOpacity}
              onChange={(e) => props.onThermalOpacityChange(Number(e.target.value))}
            />
          </label>
          <button
            type="button"
            className="ghost"
            title={t("layers.fitBoundsTitle")}
            onClick={props.onFitBounds}
          >
            {t("layers.fitBounds")}
          </button>
        </>
      )}
    </div>
  );
}
