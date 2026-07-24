import { useCallback, useState } from "react";
import { PipelineStep, ProjectPayload } from "./api";
import { DetectModality, DetectionTools } from "./DetectionTools";
import { PlantMap } from "./PlantMap";
import { SegmentationTools } from "./SegmentationTools";

/** Legacy suite map display defaults (per modality). */
const DEFAULT_DISPLAY_RGB = 0.7;
const DEFAULT_DISPLAY_THERMAL = 0.7;

export function PlantWorkspace(props: {
  step: Extract<PipelineStep, "detection" | "segmentation">;
  onProjectChange: (p: ProjectPayload) => void;
  onError: (msg: string) => void;
  refreshProject: () => Promise<void>;
}) {
  const [refreshKey, setRefreshKey] = useState(0);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [drawEnabled, setDrawEnabled] = useState(false);
  const [editCorners, setEditCorners] = useState(false);
  const [modality, setModality] = useState<DetectModality>("rgb");
  const [displayConfidenceRgb, setDisplayConfidenceRgb] = useState(DEFAULT_DISPLAY_RGB);
  const [displayConfidenceThermal, setDisplayConfidenceThermal] = useState(DEFAULT_DISPLAY_THERMAL);

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
      />
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
          selectedId={selectedId}
          onSelect={setSelectedId}
          onRefreshMap={bumpMap}
          onProjectRefresh={onProjectRefresh}
          onError={props.onError}
        />
      )}
    </div>
  );
}
