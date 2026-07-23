export type StepStatus = "pending" | "active" | "done" | "skipped" | "error";

export type PipelineStep =
  | "photogrammetry"
  | "alignment"
  | "detection"
  | "segmentation"
  | "models"
  | "classification"
  | "outputs";

export interface StepState {
  status: StepStatus;
  skipped?: boolean;
  message?: string | null;
}

export interface ProjectPayload {
  manifest: { name: string; id?: string; created_at: string; updated_at: string };
  workflow: Record<PipelineStep, StepState>;
  root: string;
  opsx_path: string | null;
  orthos_ready: boolean;
  layers: Array<Record<string, unknown>>;
}

const jsonHeaders = { "Content-Type": "application/json" };

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<{ status: string; version: string; opencl: { available: boolean } }>("/api/health"),
  createProject: (name: string) =>
    req<ProjectPayload>("/api/projects", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ name }),
    }),
  openProject: (path: string) =>
    req<ProjectPayload>("/api/projects/open", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ path }),
    }),
  current: () => req<ProjectPayload>("/api/projects/current"),
  save: () => req<ProjectPayload & { opsx_path: string }>("/api/projects/save", { method: "POST" }),
  skipPhotogrammetry: async (rgb: File, thermal: File) => {
    const fd = new FormData();
    fd.append("rgb", rgb);
    fd.append("thermal", thermal);
    const res = await fetch("/api/photogrammetry/skip", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<ProjectPayload>;
  },
  uploadRaw: async (modality: "rgb" | "thermal", files: FileList) => {
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    const res = await fetch(`/api/photogrammetry/upload-raw/${modality}`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    return res.json();
  },
  runPhoto: (modality: "rgb" | "thermal") =>
    req<{ started: boolean }>(`/api/photogrammetry/run/${modality}`, { method: "POST" }),
  photoStatus: (modality: "rgb" | "thermal") =>
    req<{ log: string[]; ortho_exists: boolean; running: boolean }>(
      `/api/photogrammetry/status/${modality}`,
    ),
  opencl: () => req<{ available: boolean; devices?: unknown[] }>("/api/photogrammetry/opencl"),
  mapLayers: () =>
    req<{
      layers: Array<{ id: string; png_url: string; bounds: { left: number; bottom: number; right: number; top: number } }>;
    }>("/api/map/layers"),
  applyAlignment: (ref_points: number[][], target_points: number[][]) =>
    req<ProjectPayload>("/api/alignment/apply", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ ref_points, target_points }),
    }),
  detection: () => req<{ message: string }>("/api/detection/status"),
  segmentation: () => req<{ message: string }>("/api/segmentation/status"),
  ml: () => req<{ message: string }>("/api/ml/status"),
  exports: () => req<{ files: string[] }>("/api/exports/status"),
};

export const STEP_LABELS: Record<PipelineStep, string> = {
  photogrammetry: "Photogrammetry",
  alignment: "Alignment",
  detection: "Detection",
  segmentation: "Segmentation",
  models: "Models",
  classification: "Classification",
  outputs: "Outputs",
};

export const STEPS: PipelineStep[] = [
  "photogrammetry",
  "alignment",
  "detection",
  "segmentation",
  "models",
  "classification",
  "outputs",
];
