import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useT } from "../i18n";

const PALETTES: Record<string, number[][]> = {
  hot: [
    [0, 0, 0],
    [128, 0, 0],
    [255, 0, 0],
    [255, 128, 0],
    [255, 255, 0],
    [255, 255, 255],
  ],
  cool: [
    [0, 255, 255],
    [0, 128, 255],
    [0, 0, 255],
    [128, 0, 255],
    [255, 0, 255],
    [255, 255, 255],
  ],
  viridis: [
    [68, 1, 84],
    [72, 40, 120],
    [62, 74, 137],
    [49, 104, 142],
    [38, 130, 142],
    [31, 158, 137],
  ],
  plasma: [
    [13, 8, 135],
    [75, 3, 161],
    [125, 3, 168],
    [168, 34, 150],
    [208, 90, 110],
    [240, 148, 65],
  ],
  inferno: [
    [0, 0, 4],
    [40, 11, 84],
    [101, 21, 110],
    [159, 42, 99],
    [212, 72, 66],
    [245, 125, 21],
  ],
  magma: [
    [0, 0, 4],
    [28, 16, 68],
    [79, 18, 123],
    [129, 37, 129],
    [181, 54, 122],
    [229, 91, 104],
  ],
  jet: [
    [0, 0, 143],
    [0, 0, 255],
    [0, 255, 255],
    [255, 255, 0],
    [255, 0, 0],
    [128, 0, 0],
  ],
};

function lerpColor(colors: number[][], t: number): number[] {
  const clamped = Math.max(0, Math.min(1, t));
  const scaled = clamped * (colors.length - 1);
  const idx = Math.floor(scaled);
  const frac = scaled - idx;
  if (idx >= colors.length - 1) return colors[colors.length - 1];
  const c1 = colors[idx];
  const c2 = colors[idx + 1];
  return [
    Math.round(c1[0] + (c2[0] - c1[0]) * frac),
    Math.round(c1[1] + (c2[1] - c1[1]) * frac),
    Math.round(c1[2] + (c2[2] - c1[2]) * frac),
  ];
}

const SCALE = 4;

export function ThermalImageViewer(props: { panelId: string }) {
  const t = useT();
  const [raw, setRaw] = useState<{
    width: number;
    height: number;
    data: (number | null)[];
    min: number | null;
    max: number | null;
    mean: number | null;
  } | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [palette, setPalette] = useState("hot");
  const [tMin, setTMin] = useState(0);
  const [tMax, setTMax] = useState(1);
  const [probe, setProbe] = useState<{ x: number; y: number; t: number | null } | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    let cancelled = false;
    setErr(null);
    setRaw(null);
    api
      .segmentationThermalRaw(props.panelId)
      .then((r) => {
        if (cancelled) return;
        setRaw(r);
        const lo = Number((r.min ?? 0).toFixed(2));
        const hi = Number((r.max ?? 1).toFixed(2));
        setTMin(lo);
        setTMax(hi > lo ? hi : lo + 1);
      })
      .catch((e) => {
        if (!cancelled) setErr(String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [props.panelId]);

  const dataMin = raw?.min ?? 0;
  const dataMax = raw?.max ?? 1;

  useEffect(() => {
    if (!raw || !canvasRef.current) return;
    const { width, height, data } = raw;
    const canvas = canvasRef.current;
    canvas.width = width * SCALE;
    canvas.height = height * SCALE;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const img = ctx.createImageData(width, height);
    const colors = PALETTES[palette] || PALETTES.hot;
    const span = tMax - tMin || 1;
    for (let i = 0; i < data.length; i++) {
      const v = data[i];
      let rgb = [0, 0, 0];
      if (v != null && Number.isFinite(v) && v > -100) {
        const n = (v - tMin) / span;
        rgb = lerpColor(colors, n);
      }
      const o = i * 4;
      img.data[o] = rgb[0];
      img.data[o + 1] = rgb[1];
      img.data[o + 2] = rgb[2];
      img.data[o + 3] = 255;
    }
    // draw at native then scale with nearest
    const off = document.createElement("canvas");
    off.width = width;
    off.height = height;
    const octx = off.getContext("2d");
    if (!octx) return;
    octx.putImageData(img, 0, 0);
    ctx.imageSmoothingEnabled = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(off, 0, 0, canvas.width, canvas.height);
  }, [raw, palette, tMin, tMax]);

  const footer = useMemo(() => {
    if (!raw) return null;
    return t("thermalViewer.footer", { min: fmt(raw.min), max: fmt(raw.max), mean: fmt(raw.mean) });
  }, [raw, t]);

  if (err) return <p className="muted">{err}</p>;
  if (!raw) return <p className="muted">{t("thermalViewer.loading")}</p>;

  return (
    <div className="thermal-viewer">
      <div className="thermal-viewer-controls">
        <label
          className="tool-field"
          title={t("thermalViewer.paletteTitle")}
        >
          {t("thermalViewer.palette")}
          <select value={palette} onChange={(e) => setPalette(e.target.value)}>
            {Object.keys(PALETTES).map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </label>
        <label
          className="tool-field"
          title={t("thermalViewer.displayMinTitle")}
        >
          {t("thermalViewer.displayMin")}
          <input
            type="number"
            step={0.1}
            value={Number(tMin.toFixed(2))}
            min={dataMin - 5}
            max={dataMax + 5}
            onChange={(e) => setTMin(Number(Number(e.target.value).toFixed(2)))}
          />
        </label>
        <label
          className="tool-field"
          title={t("thermalViewer.displayMaxTitle")}
        >
          {t("thermalViewer.displayMax")}
          <input
            type="number"
            step={0.1}
            value={Number(tMax.toFixed(2))}
            min={dataMin - 5}
            max={dataMax + 5}
            onChange={(e) => setTMax(Number(Number(e.target.value).toFixed(2)))}
          />
        </label>
      </div>
      <div className="thermal-viewer-canvas-wrap">
        <canvas
          ref={canvasRef}
          className="thermal-viewer-canvas"
          onMouseMove={(e) => {
            const rect = e.currentTarget.getBoundingClientRect();
            const px = Math.floor(((e.clientX - rect.left) / rect.width) * raw.width);
            const py = Math.floor(((e.clientY - rect.top) / rect.height) * raw.height);
            if (px < 0 || py < 0 || px >= raw.width || py >= raw.height) {
              setProbe(null);
              return;
            }
            const val = raw.data[py * raw.width + px];
            setProbe({ x: e.clientX - rect.left, y: e.clientY - rect.top, t: val });
          }}
          onMouseLeave={() => setProbe(null)}
        />
        {probe && (
          <div
            className="thermal-probe"
            style={{ left: probe.x + 12, top: probe.y + 12 }}
          >
            {probe.t == null || !Number.isFinite(probe.t)
              ? t("common.emDash")
              : t("thermalViewer.probeC", { value: probe.t.toFixed(2) })}
          </div>
        )}
      </div>
      {footer && <p className="muted thermal-viewer-footer">{footer}</p>}
    </div>
  );
}

function fmt(v: number | null | undefined) {
  if (v == null || Number.isNaN(v)) return "—";
  return v.toFixed(2);
}
