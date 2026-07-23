import { useCallback, useState } from "react";
import { PipelineStep, ProjectPayload } from "./api";
import { DetectModality, DetectionTools } from "./DetectionTools";
import { PlantMap } from "./PlantMap";
import { SegmentationTools } from "./SegmentationTools";

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
