import { useEffect, useState } from "react";
import { api, ProjectPayload } from "./api";

type Pt = { x: number; y: number; nx: number; ny: number };

export function AlignmentView(props: {
  project: ProjectPayload;
  onApplied: (p: ProjectPayload) => void;
  onError: (msg: string) => void;
}) {
  const [refPts, setRefPts] = useState<Pt[]>([]);
  const [tgtPts, setTgtPts] = useState<Pt[]>([]);
  const [rgbUrl, setRgbUrl] = useState<string | null>(null);
  const [thUrl, setThUrl] = useState<string | null>(null);
  const [meta, setMeta] = useState<{
    rgb?: { width: number; height: number };
    thermal?: { width: number; height: number };
  }>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.mapLayers().then(({ layers }) => {
      const rgb = layers.find((l) => l.id === "rgb");
      const th = layers.find((l) => l.id === "thermal");
      if (rgb) setRgbUrl(rgb.png_url);
      if (th) setThUrl(th.png_url);
    });
    // Use layer geotiff dims from project if available
    const rgbL = props.project.layers.find((l) => l.id === "rgb") as
      | { width?: number; height?: number }
      | undefined;
    const thL = props.project.layers.find((l) => l.id === "thermal" || l.id === "thermal_aligned") as
      | { width?: number; height?: number }
      | undefined;
    setMeta({
      rgb: rgbL?.width ? { width: rgbL.width, height: rgbL.height! } : undefined,
      thermal: thL?.width ? { width: thL.width, height: thL.height! } : undefined,
    });
  }, [props.project]);

  function clickImg(
    e: React.MouseEvent<HTMLImageElement>,
    which: "ref" | "tgt",
  ) {
    const img = e.currentTarget;
    const rect = img.getBoundingClientRect();
    const nx = (e.clientX - rect.left) / rect.width;
    const ny = (e.clientY - rect.top) / rect.height;
    const dims = which === "ref" ? meta.rgb : meta.thermal;
    const x = dims ? nx * dims.width : nx;
    const y = dims ? ny * dims.height : ny;
    const pt = { x, y, nx, ny };
    if (which === "ref") {
      if (refPts.length >= 4) return;
      setRefPts((p) => [...p, pt]);
    } else {
      if (tgtPts.length >= 4) return;
      setTgtPts((p) => [...p, pt]);
    }
  }

  async function apply() {
    if (refPts.length < 4 || tgtPts.length < 4) {
      props.onError("Place 4 points on RGB and 4 corresponding points on thermal.");
      return;
    }
    setBusy(true);
    try {
      const p = await api.applyAlignment(
        refPts.map((p) => [p.x, p.y]),
        tgtPts.map((p) => [p.x, p.y]),
      );
      props.onApplied(p);
    } catch (e) {
      props.onError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="card" style={{ marginBottom: "1rem", maxWidth: "none" }}>
        <h2>Alignment</h2>
        <p>
          Click 4 homologous points on RGB (reference) then thermal (target). We rewrite thermal
          georeferencing only — pixel values are preserved.
        </p>
        <div className="row">
          <span className="muted">
            RGB {refPts.length}/4 · Thermal {tgtPts.length}/4
          </span>
          <button
            onClick={() => {
              setRefPts([]);
              setTgtPts([]);
            }}
          >
            Reset points
          </button>
          <button className="primary" disabled={busy} onClick={apply}>
            Apply alignment
          </button>
        </div>
      </div>
      <div className="align-grid">
        <div className="align-pane">
          <div className="label">RGB (reference)</div>
          {rgbUrl && (
            <img src={rgbUrl} alt="RGB" onClick={(e) => clickImg(e, "ref")} />
          )}
          {refPts.map((p, i) => (
            <div
              key={i}
              className="point-dot"
              style={{ left: `${p.nx * 100}%`, top: `${p.ny * 100}%` }}
            />
          ))}
        </div>
        <div className="align-pane">
          <div className="label">Thermal (target)</div>
          {thUrl && (
            <img src={thUrl} alt="Thermal" onClick={(e) => clickImg(e, "tgt")} />
          )}
          {tgtPts.map((p, i) => (
            <div
              key={i}
              className="point-dot"
              style={{ left: `${p.nx * 100}%`, top: `${p.ny * 100}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
