import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useT } from "../i18n";
import type { ColorRange } from "./thermalColor";

type Props = {
  values: number[];
  range: ColorRange;
  onRangeChange: (r: ColorRange) => void;
  title?: string;
};

const BINS = 50;
const W = 420;
const H = 260;
const PAD = { l: 12, r: 12, t: 32, b: 28 };

export function ThermalDistributionPlot(props: Props) {
  const { values, range, onRangeChange } = props;
  const t = useT();
  const svgRef = useRef<SVGSVGElement>(null);
  const [drag, setDrag] = useState<"min" | "max" | null>(null);

  const stats = useMemo(() => {
    if (!values.length) {
      return { hist: [] as number[], edges: [] as number[], mu: 0, sigma: 0, vmin: 0, vmax: 1 };
    }
    const vmin = Math.min(...values);
    const vmax = Math.max(...values);
    const span = vmax - vmin || 1;
    const hist = new Array(BINS).fill(0);
    const edges: number[] = [];
    for (let i = 0; i <= BINS; i++) edges.push(vmin + (span * i) / BINS);
    for (const v of values) {
      let b = Math.floor(((v - vmin) / span) * BINS);
      if (b >= BINS) b = BINS - 1;
      if (b < 0) b = 0;
      hist[b]++;
    }
    const mu = values.reduce((a, b) => a + b, 0) / values.length;
    const variance = values.reduce((a, b) => a + (b - mu) ** 2, 0) / values.length;
    return { hist, edges, mu, sigma: Math.sqrt(variance), vmin, vmax };
  }, [values]);

  const plotW = W - PAD.l - PAD.r;
  const plotH = H - PAD.t - PAD.b;
  const maxCount = Math.max(1, ...stats.hist);

  const xForValue = useCallback(
    (v: number) => {
      const span = stats.vmax - stats.vmin || 1;
      return PAD.l + ((v - stats.vmin) / span) * plotW;
    },
    [stats.vmin, stats.vmax, plotW],
  );

  const valueForX = useCallback(
    (x: number) => {
      const span = stats.vmax - stats.vmin || 1;
      const t = Math.max(0, Math.min(1, (x - PAD.l) / plotW));
      return stats.vmin + t * span;
    },
    [stats.vmin, stats.vmax, plotW],
  );

  useEffect(() => {
    if (!drag) return;
    const onMove = (ev: PointerEvent) => {
      const svg = svgRef.current;
      if (!svg) return;
      const rect = svg.getBoundingClientRect();
      const x = ((ev.clientX - rect.left) / rect.width) * W;
      const v = valueForX(x);
      if (drag === "min") {
        onRangeChange({ min: Math.min(v, range.max - 1e-6), max: range.max });
      } else {
        onRangeChange({ min: range.min, max: Math.max(v, range.min + 1e-6) });
      }
    };
    const onUp = () => setDrag(null);
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
  }, [drag, onRangeChange, range.max, range.min, valueForX]);

  const xMin = xForValue(range.min);
  const xMax = xForValue(range.max);
  const span = range.max - range.min;

  return (
    <div className="thermal-dist-plot">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label={t("thermalViewer.distAria")}>
        <defs>
          <linearGradient id="thermalGradient" x1="0%" y1="0%" x2="100%" y2="0%">
            <stop offset="0%" stopColor="#00FF00" stopOpacity={0.35} />
            <stop offset="100%" stopColor="#FF0000" stopOpacity={0.35} />
          </linearGradient>
        </defs>
        <text x={PAD.l} y={18} className="dist-title">
          {props.title ?? t("thermalViewer.distTitle", { count: values.length })}
        </text>
        <text x={W - PAD.r} y={18} textAnchor="end" className="dist-mu">
          {t("thermalViewer.distMuSigma", { mu: stats.mu.toFixed(2), sigma: stats.sigma.toFixed(2) })}
        </text>
        <rect
          x={xMin}
          y={PAD.t}
          width={Math.max(0, xMax - xMin)}
          height={plotH}
          fill="url(#thermalGradient)"
        />
        {stats.hist.map((c, i) => {
          const x0 = PAD.l + (plotW * i) / BINS;
          const bw = plotW / BINS;
          const bh = (c / maxCount) * plotH;
          return (
            <rect
              key={i}
              x={x0}
              y={PAD.t + plotH - bh}
              width={Math.max(0.5, bw - 0.5)}
              height={bh}
              fill="#5b9fd4"
              opacity={0.85}
            />
          );
        })}
        <line
          x1={xMin}
          x2={xMin}
          y1={PAD.t}
          y2={PAD.t + plotH}
          stroke="#00FF00"
          strokeWidth={3}
          style={{ cursor: "ew-resize" }}
          onPointerDown={(e) => {
            e.preventDefault();
            setDrag("min");
          }}
        />
        <line
          x1={xMax}
          x2={xMax}
          y1={PAD.t}
          y2={PAD.t + plotH}
          stroke="#FF0000"
          strokeWidth={3}
          style={{ cursor: "ew-resize" }}
          onPointerDown={(e) => {
            e.preventDefault();
            setDrag("max");
          }}
        />
        <text x={PAD.l} y={H - 4} className="dist-footer">
          {t("thermalViewer.distRange", {
            min: range.min.toFixed(2),
            max: range.max.toFixed(2),
            span: span.toFixed(2),
          })}
        </text>
      </svg>
    </div>
  );
}
