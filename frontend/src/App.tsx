import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  PipelineStep,
  ProjectPayload,
  STEP_LABELS,
  STEPS,
} from "./api";
import { MapView } from "./MapView";
import { AlignmentView } from "./AlignmentView";

export default function App() {
  const [project, setProject] = useState<ProjectPayload | null>(null);
  const [step, setStep] = useState<PipelineStep>("photogrammetry");
  const [name, setName] = useState("My PV Plant");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openPath, setOpenPath] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [openclOk, setOpenclOk] = useState<boolean | null>(null);

  const refresh = useCallback(async () => {
    try {
      const p = await api.current();
      setProject(p);
    } catch {
      /* no project */
    }
  }, []);

  useEffect(() => {
    refresh();
    api.opencl().then((r) => setOpenclOk(r.available)).catch(() => setOpenclOk(false));
  }, [refresh]);

  const activeStep = useMemo(() => {
    if (!project) return "photogrammetry" as PipelineStep;
    const active = STEPS.find((s) => project.workflow[s]?.status === "active");
    return active ?? step;
  }, [project, step]);

  useEffect(() => {
    if (project) setStep(activeStep);
  }, [project, activeStep]);

  async function create() {
    setBusy(true);
    setError(null);
    try {
      const p = await api.createProject(name.trim() || "Untitled");
      setProject(p);
      setStep("photogrammetry");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openProj() {
    setBusy(true);
    setError(null);
    try {
      const p = await api.openProject(openPath.trim());
      setProject(p);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    setBusy(true);
    try {
      const p = await api.save();
      setProject(p);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSkipGeotiff(rgb: File, thermal: File) {
    setBusy(true);
    setError(null);
    try {
      const p = await api.skipPhotogrammetry(rgb, thermal);
      setProject(p);
      setStep("alignment");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runModality(modality: "rgb" | "thermal") {
    setBusy(true);
    setError(null);
    try {
      await api.runPhoto(modality);
      const poll = setInterval(async () => {
        const st = await api.photoStatus(modality);
        setLog(st.log);
        if (!st.running) {
          clearInterval(poll);
          setBusy(false);
          await refresh();
        }
      }, 1500);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  if (!project) {
    return (
      <div className="welcome">
        <div className="card">
          <h2>
            <span style={{ color: "var(--accent)" }}>Open</span>PVScope
          </h2>
          <p>
            Guided pipeline from drone RGB + thermal photos to PV anomaly outputs.
            New projects start at Photogrammetry.
          </p>
          <div className="row" style={{ marginBottom: "1rem" }}>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Project name"
            />
            <button className="primary" disabled={busy} onClick={create}>
              New project
            </button>
          </div>
          <p className="muted">Or open an existing .opsx / working folder:</p>
          <div className="row">
            <input
              type="text"
              value={openPath}
              onChange={(e) => setOpenPath(e.target.value)}
              placeholder="C:\\path\\to\\project.opsx"
              style={{ minWidth: 320 }}
            />
            <button disabled={busy || !openPath.trim()} onClick={openProj}>
              Open
            </button>
          </div>
          {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
        </div>
      </div>
    );
  }

  return (
    <div className="app">
      <aside className="sidebar">
        <div>
          <div className="brand">
            Open<span>PV</span>Scope
          </div>
          <div className="muted">{project.manifest.name}</div>
        </div>
        <ul className="steps">
          {STEPS.map((s, i) => {
            const st = project.workflow[s]?.status ?? "pending";
            const unlocked =
              st === "active" || st === "done" || st === "skipped" || st === "error";
            return (
              <li key={s}>
                <button
                  className={step === s ? "active" : ""}
                  disabled={!unlocked && s !== "photogrammetry"}
                  onClick={() => setStep(s)}
                >
                  <span className={`badge ${st}`}>{i + 1}</span>
                  {STEP_LABELS[s]}
                </button>
              </li>
            );
          })}
        </ul>
        <div className="row">
          <button className="ghost" onClick={save} disabled={busy}>
            Save
          </button>
        </div>
      </aside>
      <main className="main">
        <div className="topbar">
          <div>
            <strong>{STEP_LABELS[step]}</strong>
            <div className="muted">
              {project.opsx_path ?? project.root}
              {openclOk === false && " · OpenCL not detected"}
              {openclOk === true && " · OpenCL OK"}
            </div>
          </div>
          {project.orthos_ready && step !== "alignment" && (
            <button onClick={() => setStep("alignment")}>View map / align</button>
          )}
        </div>
        <div className="content">
          {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
          {step === "photogrammetry" && (
            <PhotogrammetryPanel
              busy={busy}
              log={log}
              onSkip={onSkipGeotiff}
              onUpload={async (modality, files) => {
                await api.uploadRaw(modality, files);
              }}
              onRun={runModality}
            />
          )}
          {step === "alignment" && (
            <AlignmentView
              project={project}
              onApplied={(p) => {
                setProject(p);
                setStep("detection");
              }}
              onError={setError}
            />
          )}
          {step === "detection" && <ScaffoldStep kind="detection" />}
          {step === "segmentation" && <ScaffoldStep kind="segmentation" />}
          {step === "models" && <ScaffoldStep kind="models" />}
          {step === "classification" && <ScaffoldStep kind="classification" />}
          {step === "outputs" && <ScaffoldStep kind="outputs" />}
          {project.orthos_ready && step === "photogrammetry" && (
            <div style={{ marginTop: "1.25rem" }}>
              <MapView />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function PhotogrammetryPanel(props: {
  busy: boolean;
  log: string[];
  onSkip: (rgb: File, thermal: File) => void;
  onUpload: (m: "rgb" | "thermal", files: FileList) => Promise<void>;
  onRun: (m: "rgb" | "thermal") => void;
}) {
  const [rgbFile, setRgbFile] = useState<File | null>(null);
  const [thFile, setThFile] = useState<File | null>(null);

  return (
    <div className="card">
      <h2>Photogrammetry</h2>
      <p>
        Upload raw RGB and thermal drone images. OpenSfM 1.0 builds georeferenced orthophotos.
        Thermal TIFF is used as-is; DJI proprietary formats will use a converter hook.
      </p>
      <div className="row" style={{ marginBottom: "0.75rem" }}>
        <label>
          RGB images{" "}
          <input
            type="file"
            multiple
            onChange={async (e) => {
              if (e.target.files?.length) await props.onUpload("rgb", e.target.files);
            }}
          />
        </label>
        <button disabled={props.busy} onClick={() => props.onRun("rgb")}>
          Run OpenSfM (RGB)
        </button>
      </div>
      <div className="row" style={{ marginBottom: "1rem" }}>
        <label>
          Thermal images{" "}
          <input
            type="file"
            multiple
            onChange={async (e) => {
              if (e.target.files?.length) await props.onUpload("thermal", e.target.files);
            }}
          />
        </label>
        <button disabled={props.busy} onClick={() => props.onRun("thermal")}>
          Run OpenSfM (Thermal)
        </button>
      </div>
      <hr style={{ borderColor: "var(--border)" }} />
      <p style={{ marginTop: "1rem" }}>Already have orthophoto GeoTIFFs? Skip this step:</p>
      <div className="row">
        <label>
          RGB GeoTIFF{" "}
          <input type="file" accept=".tif,.tiff" onChange={(e) => setRgbFile(e.target.files?.[0] ?? null)} />
        </label>
        <label>
          Thermal GeoTIFF{" "}
          <input type="file" accept=".tif,.tiff" onChange={(e) => setThFile(e.target.files?.[0] ?? null)} />
        </label>
        <button
          className="primary"
          disabled={props.busy || !rgbFile || !thFile}
          onClick={() => rgbFile && thFile && props.onSkip(rgbFile, thFile)}
        >
          Import GeoTIFFs & continue
        </button>
      </div>
      {props.log.length > 0 && <div className="log" style={{ marginTop: "1rem" }}>{props.log.join("\n")}</div>}
    </div>
  );
}

function ScaffoldStep({ kind }: { kind: "detection" | "segmentation" | "models" | "outputs" | "classification" }) {
  const [msg, setMsg] = useState("Loading…");
  useEffect(() => {
    const load = async () => {
      if (kind === "detection") setMsg((await api.detection()).message);
      else if (kind === "segmentation") setMsg((await api.segmentation()).message);
      else if (kind === "models" || kind === "classification") setMsg((await api.ml()).message);
      else setMsg(`Exports: ${((await api.exports()).files || []).join(", ") || "none yet"}`);
    };
    load().catch((e) => setMsg(String(e)));
  }, [kind]);
  const title =
    kind === "outputs"
      ? STEP_LABELS.outputs
      : kind === "classification"
        ? STEP_LABELS.classification
        : STEP_LABELS[kind];
  return (
    <div className="card">
      <h2>{title}</h2>
      <p>{msg}</p>
      <p className="muted">This stage is scaffolded for the next development increment.</p>
    </div>
  );
}
