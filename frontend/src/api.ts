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

export interface HistoryState {
  can_undo: boolean;
  can_redo: boolean;
  undo_label: string | null;
  redo_label: string | null;
  depth: number;
  redo_depth: number;
}

export interface ProjectPayload {
  manifest: { name: string; id?: string; created_at: string; updated_at: string };
  workflow: Record<PipelineStep, StepState>;
  root: string;
  opsx_path: string | null;
  orthos_ready: boolean;
  photo_setup?: PhotoSetup | null;
  rgb_ortho_missing?: boolean;
  thermal_ortho_ready?: boolean;
  layers: Array<Record<string, unknown>>;
  history?: HistoryState;
}

export interface AppSettings {
  history_max_steps: number;
  history_include_rasters: boolean;
  default_project_dir: string | null;
  recent_max: number;
  recent_projects: Array<{ path: string; name: string; opened_at: string }>;
  opsz_default_mode: "full" | "light";
  opsz_light_exclude: string[];
  language: "en" | "it" | "es" | "de" | "fr";
}

export interface RecentItem {
  path: string;
  name: string;
  opened_at: string;
  exists: boolean;
}

export type ConsoleLevel = "info" | "verbose" | "error" | "success" | "warn";

export interface ConsoleEntry {
  seq: number;
  ts: number;
  level: ConsoleLevel;
  message: string;
  step: string | null;
  job_id: string | null;
}

export interface ConsoleJob {
  id: string;
  title: string;
  status: "idle" | "running" | "done" | "error";
  progress: number | null;
  detail: string | null;
  started_at: number;
  ended_at: number | null;
}

export interface ConsoleSnapshot {
  seq: number;
  entries: ConsoleEntry[];
  job: ConsoleJob | null;
}

export type GeoJsonFc = {
  type: "FeatureCollection";
  features: Array<{
    type: string;
    id?: string | number;
    properties?: Record<string, unknown> | null;
    geometry: { type: string; coordinates: unknown };
  }>;
};

export interface MapLayerInfo {
  id: string;
  tile_url: string;
  bounds: { left: number; bottom: number; right: number; top: number };
  maxzoom?: number;
  tile_size?: number;
  width?: number;
  height?: number;
}

export type PhotoModality = "rgb" | "thermal";

export interface OpenClInfo {
  available: boolean;
  devices?: Array<{ platform?: string; device?: string; type?: string }>;
  clinfo?: string;
  error?: string;
}

export interface DjiSdkInfo {
  available: boolean;
  path?: string | null;
  dll?: string | null;
  error?: string | null;
}

export interface OdxInfo {
  available: boolean;
  root?: string | null;
  run_script?: string | null;
  error?: string | null;
}

export interface PhotoJobPublic {
  status: string;
  current_command: string | null;
  stage_index: number;
  stage_total: number;
  stage_name: string | null;
  error: string | null;
  ortho_path: string | null;
  cancelable: boolean;
}

export interface PhotoStatus {
  log: string[];
  running: boolean;
  job: PhotoJobPublic | null;
  ortho_exists: boolean;
  odx: OdxInfo;
  odx_root: string | null;
  opencl: OpenClInfo;
  dji_sdk: DjiSdkInfo;
}

export interface PhotoThermalParams {
  emissivity?: number;
  distance?: number;
  humidity?: number;
  reflection?: number;
  parametric_fallback?: boolean;
}

export type PhotoModalities = "rgb_and_thermal" | "thermal_only";
export type PhotoMode = "process" | "skip";
export type OdxQuality = "ultra" | "high" | "medium" | "low" | "lowest";

export interface OdxOptions {
  orthophoto_resolution: number;
  feature_quality: OdxQuality;
  pc_quality: OdxQuality;
  fast_orthophoto: boolean;
  crop: number;
}

export interface PhotoProducts {
  ortho: boolean;
  dense_pc: boolean;
  sparse_pc: boolean;
  dsm: boolean;
  dtm: boolean;
}

export interface PhotoSetup {
  wizard_complete: boolean;
  modalities: PhotoModalities;
  mode: PhotoMode;
  odx: OdxOptions;
  products: PhotoProducts;
}

export interface PhotoRunBody extends PhotoThermalParams {
  odx?: Partial<OdxOptions>;
  products?: Partial<PhotoProducts>;
}

export interface PhotoProductItem {
  id: string;
  label: string;
  path: string;
  exists: boolean;
  size: number;
}

export interface PhotoRawList {
  files: Array<{ name: string; size: number }>;
  count: number;
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
  health: () =>
    req<{
      status: string;
      version: string;
      odx: OdxInfo;
      odx_root: string | null;
      opencl: OpenClInfo;
      dji_sdk: DjiSdkInfo;
    }>("/api/health"),
  createProject: (name: string, project_dir: string) =>
    req<ProjectPayload>("/api/projects", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ name, project_dir }),
    }),
  openProject: (path: string) =>
    req<ProjectPayload>("/api/projects/open", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ path }),
    }),
  current: () => req<ProjectPayload>("/api/projects/current"),
  autosave: () =>
    req<ProjectPayload & { opsx_path: string }>("/api/projects/autosave", { method: "POST" }),
  save: () => req<ProjectPayload & { opsx_path: string }>("/api/projects/save", { method: "POST" }),
  closeProject: () => req<{ closed: boolean }>("/api/projects/close", { method: "POST" }),
  exportOpsz: (dest_path: string, mode?: "full" | "light") =>
    req<ProjectPayload & { opsz_path: string; mode: string }>("/api/projects/export-opsz", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ dest_path, mode }),
    }),
  importOpsz: (opsz_path: string, dest_dir: string) =>
    req<ProjectPayload>("/api/projects/import-opsz", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ opsz_path, dest_dir }),
    }),
  recentProjects: () => req<{ recent: RecentItem[] }>("/api/projects/recent"),
  history: () => req<HistoryState>("/api/projects/history"),
  undo: () => req<ProjectPayload & { undone: string }>("/api/projects/history/undo", { method: "POST" }),
  redo: () => req<ProjectPayload & { redone: string }>("/api/projects/history/redo", { method: "POST" }),
  getSettings: () => req<AppSettings>("/api/settings"),
  putSettings: (patch: Partial<AppSettings> & { clear_recent?: boolean; clear_default_project_dir?: boolean }) =>
    req<AppSettings>("/api/settings", {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(patch),
    }),
  console: (since = 0) => req<ConsoleSnapshot>(`/api/console?since=${since}`),
  consoleClear: () => req<{ cleared: boolean }>("/api/console/clear", { method: "POST" }),
  pickDirectory: () => req<{ path: string | null }>("/api/system/pick-directory", { method: "POST" }),
  pickOpsx: () => req<{ path: string | null }>("/api/system/pick-opsx", { method: "POST" }),
  pickOpszOpen: () => req<{ path: string | null }>("/api/system/pick-opsz-open", { method: "POST" }),
  pickOpszSave: () => req<{ path: string | null }>("/api/system/pick-opsz-save", { method: "POST" }),
  skipPhotogrammetry: async (thermal: File, rgb?: File | null) => {
    const fd = new FormData();
    fd.append("thermal", thermal);
    if (rgb) fd.append("rgb", rgb);
    const res = await fetch("/api/photogrammetry/skip", { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<ProjectPayload>;
  },
  uploadRaw: async (modality: PhotoModality, files: FileList | File[]) => {
    const fd = new FormData();
    Array.from(files).forEach((f) => fd.append("files", f));
    const res = await fetch(`/api/photogrammetry/upload-raw/${modality}`, { method: "POST", body: fd });
    if (!res.ok) throw new Error(await res.text());
    return res.json() as Promise<{ saved: string[]; formats?: unknown[]; count: number }>;
  },
  listRaw: (modality: PhotoModality) =>
    req<PhotoRawList>(`/api/photogrammetry/raw/${modality}`),
  getPhotoSetup: () => req<PhotoSetup>("/api/photogrammetry/setup"),
  putPhotoSetup: (body: Partial<PhotoSetup> & { wizard_complete?: boolean }) =>
    req<PhotoSetup>("/api/photogrammetry/setup", {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),
  listPhotoProducts: (modality: PhotoModality) =>
    req<{ modality: string; products: PhotoProductItem[] }>(
      `/api/photogrammetry/products/${modality}`,
    ),
  runPhoto: (modality: PhotoModality, body?: PhotoRunBody) =>
    req<{ started: boolean; modality: string }>(`/api/photogrammetry/run/${modality}`, {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(body ?? {}),
    }),
  runPhotoBoth: (body?: PhotoRunBody) =>
    req<{ started: boolean; modality: string }>("/api/photogrammetry/run-both", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(body ?? {}),
    }),
  cancelPhoto: (modality: PhotoModality) =>
    req<{ cancelled: boolean; modality: string }>(`/api/photogrammetry/cancel/${modality}`, {
      method: "POST",
    }),
  photoStatus: (modality: PhotoModality) =>
    req<PhotoStatus>(`/api/photogrammetry/status/${modality}`),
  opencl: () => req<OpenClInfo>("/api/photogrammetry/opencl"),
  mapLayers: () =>
    req<{
      layers: MapLayerInfo[];
      vectors?: Record<string, string>;
    }>("/api/map/layers"),
  orthoMeta: (layerId: "rgb" | "thermal" | "thermal_aligned") =>
    req<{
      width: number;
      height: number;
      crs: string | null;
      transform: number[];
      bounds: { left: number; bottom: number; right: number; top: number };
    }>(`/api/ortho/${layerId}/meta`),
  orthoWindowUrl: (
    layerId: "rgb" | "thermal" | "thermal_aligned",
    q: {
      col_off: number;
      row_off: number;
      width: number;
      height: number;
      out_w: number;
      out_h: number;
      v?: number | string;
    },
  ) => {
    const params = new URLSearchParams({
      col_off: String(q.col_off),
      row_off: String(q.row_off),
      width: String(q.width),
      height: String(q.height),
      out_w: String(q.out_w),
      out_h: String(q.out_h),
    });
    if (q.v != null) params.set("v", String(q.v));
    return `/api/ortho/${layerId}/window?${params}`;
  },
  orthoGeoWindowUrl: (
    layerId: "rgb" | "thermal" | "thermal_aligned",
    q: {
      west: number;
      south: number;
      east: number;
      north: number;
      out_w: number;
      out_h: number;
      v?: number | string;
    },
  ) => {
    const params = new URLSearchParams({
      west: String(q.west),
      south: String(q.south),
      east: String(q.east),
      north: String(q.north),
      out_w: String(q.out_w),
      out_h: String(q.out_h),
    });
    if (q.v != null) params.set("v", String(q.v));
    return `/api/ortho/${layerId}/geo-window?${params}`;
  },
  /** Reproject thermal onto the exact RGB pixel-window grid (overlay-aligned). */
  orthoMatchRgbWindowUrl: (
    layerId: "thermal" | "thermal_aligned",
    q: {
      col_off: number;
      row_off: number;
      width: number;
      height: number;
      out_w: number;
      out_h: number;
      v?: number | string;
    },
  ) => {
    const params = new URLSearchParams({
      col_off: String(q.col_off),
      row_off: String(q.row_off),
      width: String(q.width),
      height: String(q.height),
      out_w: String(q.out_w),
      out_h: String(q.out_h),
    });
    if (q.v != null) params.set("v", String(q.v));
    return `/api/ortho/${layerId}/match-rgb-window?${params}`;
  },
  alignmentStatus: () =>
    req<{
      status: string;
      message?: string | null;
      has_aligned: boolean;
      aligned_mtime_ns?: number | null;
      gcps: { ref_points: number[][]; target_points: number[][] } | null;
    }>("/api/alignment/status"),
  previewAlignment: (ref_points: number[][], target_points: number[][]) =>
    req<ProjectPayload & { aligned_mtime_ns?: number | null }>("/api/alignment/preview", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ ref_points, target_points }),
    }),
  confirmAlignment: () =>
    req<ProjectPayload>("/api/alignment/confirm", { method: "POST" }),
  applyAlignment: (ref_points: number[][], target_points: number[][]) =>
    req<ProjectPayload>("/api/alignment/apply", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ ref_points, target_points }),
    }),
  detection: () => req<{ message: string }>("/api/detection/status"),
  detectionStatus: () =>
    req<{
      message: string;
      ready: boolean;
      has_aoi: boolean;
      has_grid: boolean;
      has_rgb_panels: boolean;
      has_thermal_panels: boolean;
      panel_count: number;
      rgb?: { has_aoi: boolean; has_grid: boolean; has_panels: boolean; panel_count: number };
      thermal?: { has_aoi: boolean; has_grid: boolean; has_panels: boolean; panel_count: number };
      both_grids_ready?: boolean;
      job?: { running: boolean; error: string | null; result: unknown };
    }>("/api/detection/status"),
  putAoi: (
    ring: number[][],
    opts?: { modality?: "rgb" | "thermal"; regenerate_grid?: boolean },
  ) =>
    req<{ ok: boolean; geojson: GeoJsonFc; grid?: GeoJsonFc | null }>("/api/detection/aoi", {
      method: "PUT",
      headers: jsonHeaders,
      body: JSON.stringify({
        ring,
        modality: opts?.modality ?? "rgb",
        regenerate_grid: opts?.regenerate_grid ?? false,
      }),
    }),
  generateGrid: (rows: number, cols: number, modality: "rgb" | "thermal" = "rgb") =>
    req<{ rows: number; cols: number; cell_count: number; geojson: GeoJsonFc }>("/api/detection/grid", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({ rows, cols, modality }),
    }),
  copyGridToThermal: () =>
    req<{ ok: boolean; thermal_aoi?: GeoJsonFc; thermal_grid?: GeoJsonFc }>(
      "/api/detection/grid/copy-to-thermal",
      { method: "POST" },
    ),
  runDetection: (opts: {
    confidence_rgb: number;
    confidence_thermal: number;
    nms_iou: number;
    num_templates?: number;
    thermal_temp_cap?: number | null;
    advanced_validation?: boolean;
    fine_tuning_confidence?: number;
  }) =>
    req<{ started: boolean }>("/api/detection/run", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        confidence_rgb: opts.confidence_rgb,
        confidence_thermal: opts.confidence_thermal,
        nms_iou: opts.nms_iou,
        num_templates: opts.num_templates ?? 0,
        thermal_temp_cap: opts.thermal_temp_cap ?? 45,
        modality: "both",
        advanced_validation: opts.advanced_validation ?? true,
        fine_tuning_confidence: opts.fine_tuning_confidence ?? 0.65,
      }),
    }),
  detectionJob: () =>
    req<{ running: boolean; error: string | null; result: unknown }>("/api/detection/job"),
  detectionGeojson: (name: "aoi" | "grid" | "panels", modality: "rgb" | "thermal" = "rgb") =>
    req<GeoJsonFc>(`/api/detection/geojson/${name}?modality=${modality}`),
  clearDetection: (modality?: "rgb" | "thermal") =>
    req<{ message: string }>(
      modality ? `/api/detection/clear?modality=${modality}` : "/api/detection/clear",
      { method: "POST" },
    ),
  deletePanel: (id: string, modality: "rgb" | "thermal" = "rgb") =>
    req<{ ok: boolean; panel_count: number }>(
      `/api/detection/panel/${id}?modality=${modality}`,
      { method: "DELETE" },
    ),
  segmentation: () => req<{ message: string }>("/api/segmentation/status"),
  segmentationStatus: () =>
    req<{
      message: string;
      ready: boolean;
      has_pairs: boolean;
      pair_count: number;
      job?: { running: boolean; error: string | null; result: unknown };
    }>("/api/segmentation/status"),
  runSegmentation: (opts?: {
    margin_factor?: number;
    search_radius_m?: number | null;
    min_iou?: number;
  }) =>
    req<{ started: boolean }>("/api/segmentation/run", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify({
        margin_factor: opts?.margin_factor ?? 0.2,
        search_radius_m: opts?.search_radius_m ?? null,
        min_iou: opts?.min_iou ?? 0.1,
      }),
    }),
  segmentationJob: () =>
    req<{ running: boolean; error: string | null; result: unknown }>("/api/segmentation/job"),
  segmentationPairs: () => req<{ pairs: unknown[]; count: number }>("/api/segmentation/pairs"),
  segmentationPairsGeojson: () => req<GeoJsonFc>("/api/segmentation/pairs.geojson"),
  segmentationPanelMeta: (id: string) =>
    req<Record<string, unknown>>(`/api/segmentation/panel/${id}/meta`),
  segmentationPreviewUrl: (id: string, kind: "rgb" | "thermal") =>
    `/api/segmentation/panel/${id}/preview/${kind}`,
  segmentationThermalRaw: (id: string) =>
    req<{
      width: number;
      height: number;
      data: (number | null)[];
      min: number | null;
      max: number | null;
      mean: number | null;
    }>(`/api/segmentation/panel/${id}/thermal-raw`),
  saveSegmentationLabels: (body: { indicator: string; green: number; red: number }) =>
    req<{
      path: string;
      labeled: number;
      label_0: number;
      label_mid: number;
      label_1: number;
    }>("/api/segmentation/labels", {
      method: "POST",
      headers: jsonHeaders,
      body: JSON.stringify(body),
    }),
  ml: () => req<{ message: string }>("/api/ml/status"),
  exports: () => req<{ files: string[] }>("/api/exports/status"),
};

export const STEP_LABELS: Record<PipelineStep, string> = {
  photogrammetry: "Photogrammetry",
  alignment: "Ortho alignment",
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
