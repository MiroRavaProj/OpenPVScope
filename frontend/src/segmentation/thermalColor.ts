/** Legacy green→red thermal fill + IoU fallback (port of thermal_color.py). */

import type { GeoJsonFc } from "../api";

export type ThermalIndicator =
  | "max_temperature"
  | "min_temperature"
  | "mean_temperature"
  | "median_temperature"
  | "std_temperature"
  | "var_temperature";

export const THERMAL_INDICATORS: { id: ThermalIndicator; label: string }[] = [
  { id: "max_temperature", label: "Max °C" },
  { id: "min_temperature", label: "Min °C" },
  { id: "mean_temperature", label: "Mean °C" },
  { id: "median_temperature", label: "Median °C" },
  { id: "std_temperature", label: "Std °C" },
  { id: "var_temperature", label: "Variance" },
];

export const LABELABLE_INDICATORS: ThermalIndicator[] = [
  "max_temperature",
  "mean_temperature",
  "median_temperature",
  "std_temperature",
];

export type ColorRange = { min: number; max: number };

export function getThermalColorForValue(
  value: number | null | undefined,
  minVal: number,
  maxVal: number,
  colorRange?: ColorRange | null,
): string {
  if (value == null || Number.isNaN(value)) return "#808080";
  if (maxVal <= minVal) return "#00FF00";

  let normalized: number;
  if (colorRange) {
    const effectiveMin = colorRange.min;
    const effectiveMax = colorRange.max;
    if (effectiveMax <= effectiveMin) return "#00FF00";
    if (value <= effectiveMin) return "#00FF00";
    if (value >= effectiveMax) return "#FF0000";
    normalized = (value - effectiveMin) / (effectiveMax - effectiveMin);
  } else {
    normalized = (value - minVal) / (maxVal - minVal);
  }
  normalized = Math.max(0, Math.min(1, normalized));
  const red = Math.round(normalized * 255);
  const green = Math.round((1 - normalized) * 255);
  return `#${red.toString(16).padStart(2, "0")}${green.toString(16).padStart(2, "0")}00`;
}

export function iouFallbackColor(iou: number | null | undefined): string {
  const v = Number(iou ?? 0);
  if (v > 0.5) return "#00FF00";
  if (v > 0.3) return "#FFFF00";
  return "#FFA500";
}

export function percentileRange(values: number[], lo = 85, hi = 95): ColorRange {
  const arr = values.filter((v) => Number.isFinite(v)).sort((a, b) => a - b);
  if (arr.length === 0) return { min: 0, max: 1 };
  if (arr.length === 1) return { min: arr[0] - 0.5, max: arr[0] + 0.5 };
  const pct = (p: number) => {
    const i = ((arr.length - 1) * p) / 100;
    const loI = Math.floor(i);
    const hiI = Math.ceil(i);
    if (loI === hiI) return arr[loI];
    return arr[loI] + (arr[hiI] - arr[loI]) * (i - loI);
  };
  let min = pct(lo);
  let max = pct(hi);
  if (max <= min) max = min + 1e-3;
  return { min, max };
}

export function collectIndicatorValues(
  features: { properties?: Record<string, unknown> | null }[],
  indicator: ThermalIndicator,
): number[] {
  const out: number[] = [];
  for (const f of features) {
    const v = f.properties?.[indicator];
    if (typeof v === "number" && Number.isFinite(v)) out.push(v);
  }
  return out;
}

export function colorizePairsGeojson(
  fc: GeoJsonFc,
  opts: {
    indicator: ThermalIndicator;
    thermalColoring: boolean;
    colorRange: ColorRange | null;
  },
): GeoJsonFc {
  const values = collectIndicatorValues(fc.features || [], opts.indicator);
  const globalMin = values.length ? Math.min(...values) : 0;
  const globalMax = values.length ? Math.max(...values) : 1;
  return {
    type: "FeatureCollection",
    features: (fc.features || []).map((f) => {
      const props = { ...(f.properties || {}) };
      const val = props[opts.indicator];
      const fill = opts.thermalColoring
        ? getThermalColorForValue(
            typeof val === "number" ? val : null,
            globalMin,
            globalMax,
            opts.colorRange,
          )
        : iouFallbackColor(props.iou as number | undefined);
      props.fill_color = fill;
      return {
        ...f,
        properties: props,
        geometry: f.geometry,
      };
    }),
  };
}

export function softLabel(value: number | null, green: number, red: number): number | null {
  if (value == null || Number.isNaN(value)) return null;
  if (value <= green) return 0;
  if (value >= red) return 1;
  if (red <= green) return 0;
  return (value - green) / (red - green);
}
