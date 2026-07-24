import { useCallback, useMemo, useRef, useState } from "react";
import { PipelineStep, ProjectPayload } from "./api";
import { DetectModality, DetectionTools } from "./DetectionTools";
import { LayerDock, Basemap } from "./LayerDock";
import { PlantMap } from "./PlantMap";
import { SegmentationTools, SegColorState } from "./SegmentationTools";
import { PanelInspector } from "./segmentation/PanelInspector";
import { colorizePairsGeojson } from "./segmentation/thermalColor";
import { useMinimized } from "./ui/useMinimized";
import { useT } from "./i18n";

/** Legacy suite map display defaults (per modality). */
const DEFAULT_DISPLAY_RGB = 0.7;
const DEFAULT_DISPLAY_THERMAL = 0.7;

export function PlantWorkspace(props: {
  step: Extract<PipelineStep, "detection" | "segmentation">;
  onProjectChange: (p: ProjectPayload) => void;
  onError: (msg: string) => void;
  refreshProject: () => Promise<void>;
}) {
  const t = useT();
  const [refreshKey, setRefreshKey] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawEnabled, setDrawEnabled] = useState(false);
  const [editCorners, setEditCorners] = useState(false);
  const [modality, setModality] = useState<DetectModality>("rgb");
  const [displayConfidenceRgb, setDisplayConfidenceRgb] = useState(DEFAULT_DISPLAY_RGB);
  const [displayConfidenceThermal, setDisplayConfidenceThermal] = useState(DEFAULT_DISPLAY_THERMAL);
  const [rgbOpacity, setRgbOpacity] = useState(1);
  const [thermalOpacity, setThermalOpacity] = useState(0);
  const [basemap, setBasemap] = useState<Basemap>("satellite");
  const fitBoundsRef = useRef<(() => void) | null>(null);
  const [inspMin, setInspMin] = useMinimized("seg-inspector", false);
  const [segColor, setSegColor] = useState<SegColorState>({
    thermalColoring: true,
    indicator: "max_temperature",
    colorRange: null,
    pairsFc: null,
  });

  const bumpMap = useCallback(() => setRefreshKey((k) => k + 1), []);
  const onProjectRefresh = useCallback(() => {
    void props.refreshProject();
  }, [props.refreshProject]);
  const onAoiSaved = useCallback(() => {
    bumpMap();
    setDrawEnabled(false);
  }, [bumpMap]);
  const onCornersEdited = useCallback(() => {
    bumpMap();
  }, [bumpMap]);

  const onColorStateChange = useCallback((patch: Partial<SegColorState>) => {
    setSegColor((prev) => ({ ...prev, ...patch }));
  }, []);

  const colorizedPairs = useMemo(() => {
    if (!segColor.pairsFc) return null;
    return colorizePairsGeojson(segColor.pairsFc, {
      indicator: segColor.indicator,
      thermalColoring: segColor.thermalColoring,
      colorRange: segColor.colorRange,
    });
  }, [segColor]);

  return (
    <div className="plant-workspace">
      <PlantMap
        mode={props.step}
        drawEnabled={drawEnabled && props.step === "detection"}
        editCorners={editCorners && props.step === "detection"}
        modality={modality}
        showPanels={props.step === "detection" ? "both" : "rgb"}
        displayConfidenceRgb={props.step === "detection" ? displayConfidenceRgb : 0}
        displayConfidenceThermal={props.step === "detection" ? displayConfidenceThermal : 0}
        refreshKey={refreshKey}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onAoiSaved={onAoiSaved}
        onCornersEdited={onCornersEdited}
        onError={props.onError}
        pairsGeojson={props.step === "segmentation" ? colorizedPairs : null}
        thermalColoring={segColor.thermalColoring}
        rgbOpacity={rgbOpacity}
        thermalOpacity={thermalOpacity}
        basemap={basemap}
        onRgbOpacityChange={setRgbOpacity}
        onThermalOpacityChange={setThermalOpacity}
        fitBoundsRef={fitBoundsRef}
      />

      <aside className="process-dock" aria-label={t("app.processDock")}>
        {props.step === "detection" && (
          <DetectionTools
            drawEnabled={drawEnabled}
            setDrawEnabled={setDrawEnabled}
            modality={modality}
            setModality={(m) => {
              setModality(m);
              bumpMap();
            }}
            editCorners={editCorners}
            setEditCorners={setEditCorners}
            displayConfidenceRgb={displayConfidenceRgb}
            setDisplayConfidenceRgb={(v) => {
              setDisplayConfidenceRgb(v);
              bumpMap();
            }}
            displayConfidenceThermal={displayConfidenceThermal}
            setDisplayConfidenceThermal={(v) => {
              setDisplayConfidenceThermal(v);
              bumpMap();
            }}
            onRefreshMap={bumpMap}
            onProjectRefresh={onProjectRefresh}
            onError={props.onError}
          />
        )}
        {props.step === "segmentation" && (
          <SegmentationTools
            onRefreshMap={bumpMap}
            onProjectRefresh={onProjectRefresh}
            onError={props.onError}
            colorState={segColor}
            onColorStateChange={onColorStateChange}
          />
        )}
        <LayerDock
          basemap={basemap}
          onBasemapChange={setBasemap}
          rgbOpacity={rgbOpacity}
          onRgbOpacityChange={setRgbOpacity}
          thermalOpacity={thermalOpacity}
          onThermalOpacityChange={setThermalOpacity}
          onFitBounds={() => fitBoundsRef.current?.()}
        />
      </aside>

      {props.step === "segmentation" && selectedId && (
        <PanelInspector
          panelId={selectedId}
          minimized={inspMin}
          onToggleMinimize={() => setInspMin(!inspMin)}
          onClose={() => setSelectedId(null)}
        />
      )}
    </div>
  );
}
