import { useCallback, useState } from "react";
import { PipelineStep, ProjectPayload } from "./api";
import { DetectionTools } from "./DetectionTools";
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

  const bumpMap = useCallback(() => setRefreshKey((k) => k + 1), []);
  const onProjectRefresh = useCallback(() => {
    void props.refreshProject();
  }, [props.refreshProject]);
  const onAoiSaved = useCallback(() => {
    bumpMap();
    setDrawEnabled(false);
  }, [bumpMap]);

  return (
    <div className="plant-workspace">
      <PlantMap
        mode={props.step}
        drawEnabled={drawEnabled && props.step === "detection"}
        refreshKey={refreshKey}
        selectedId={selectedId}
        onSelect={setSelectedId}
        onAoiSaved={onAoiSaved}
        onError={props.onError}
      />
      {props.step === "detection" && (
        <DetectionTools
          drawEnabled={drawEnabled}
          setDrawEnabled={setDrawEnabled}
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
