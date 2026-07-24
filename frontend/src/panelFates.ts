/** Refine fate labels for panel audit map / review dock. */

export const PANEL_FATES = [
  "kept",
  "filled_restored",
  "filled_synth",
  "readded",
  "dbscan_noise",
  "tiny_cluster",
  "walk_reject",
  "no_fit",
  "border_prune",
] as const;

export type PanelFate = (typeof PANEL_FATES)[number];

/** High-chroma fills (no grey/white). Strokes use the darker twin. */
export const FATE_COLORS: Record<PanelFate, string> = {
  kept: "#00c853",
  filled_restored: "#69f0ae",
  filled_synth: "#00b8d4",
  readded: "#ffd600",
  dbscan_noise: "#d500f9",
  tiny_cluster: "#aa00ff",
  walk_reject: "#ff1744",
  no_fit: "#f50057",
  border_prune: "#ff6d00",
};

export const FATE_STROKE: Record<PanelFate, string> = {
  kept: "#007e33",
  filled_restored: "#00c853",
  filled_synth: "#0091a8",
  readded: "#c7a500",
  dbscan_noise: "#9c00b8",
  tiny_cluster: "#7a00b8",
  walk_reject: "#c4001d",
  no_fit: "#b0003a",
  border_prune: "#c44f00",
};

export const FATE_DEFAULT_INCLUDE: Record<PanelFate, boolean> = {
  kept: true,
  filled_restored: true,
  filled_synth: true,
  readded: true,
  dbscan_noise: false,
  tiny_cluster: false,
  walk_reject: false,
  no_fit: false,
  border_prune: false,
};

export function defaultVisibleFates(): Record<PanelFate, boolean> {
  return Object.fromEntries(PANEL_FATES.map((f) => [f, true])) as Record<PanelFate, boolean>;
}

export function isPanelFate(v: unknown): v is PanelFate {
  return typeof v === "string" && (PANEL_FATES as readonly string[]).includes(v);
}

/** MapLibre match expression for fill/line color from feature property `fate`. */
export function fateColorExpression(selectedId: string | null): unknown[] {
  const cases: unknown[] = ["case", ["==", ["get", "id"], selectedId ?? ""], "#ff9100"];
  for (const fate of PANEL_FATES) {
    cases.push(["==", ["get", "fate"], fate], FATE_COLORS[fate]);
  }
  cases.push(FATE_COLORS.kept);
  return cases;
}

export function fateStrokeExpression(selectedId: string | null): unknown[] {
  const cases: unknown[] = ["case", ["==", ["get", "id"], selectedId ?? ""], "#e65100"];
  for (const fate of PANEL_FATES) {
    cases.push(["==", ["get", "fate"], fate], FATE_STROKE[fate]);
  }
  cases.push(FATE_STROKE.kept);
  return cases;
}

/** @deprecated use fateColorExpression */
export function fateFillColorExpression(selectedId: string | null): unknown[] {
  return fateColorExpression(selectedId);
}
