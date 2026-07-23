import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  AppSettings,
  PipelineStep,
  ProjectPayload,
  RecentItem,
  STEP_LABELS,
  STEPS,
} from "./api";
import { MapView } from "./MapView";
import { OrthoAlignmentView } from "./AlignmentView";
import { SettingsModal } from "./SettingsModal";
import { ActivityConsole, useConsole } from "./ActivityConsole";
import { PlantWorkspace } from "./PlantWorkspace";

export default function App() {
  const { noteLocal } = useConsole();
  const [project, setProject] = useState<ProjectPayload | null>(null);
  const [step, setStep] = useState<PipelineStep>("photogrammetry");
  const [name, setName] = useState("My PV Plant");
  const [projectDir, setProjectDir] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [openPath, setOpenPath] = useState("");
  const [log, setLog] = useState<string[]>([]);
  const [openclOk, setOpenclOk] = useState<boolean | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [recent, setRecent] = useState<RecentItem[]>([]);
  const [exportMode, setExportMode] = useState<"full" | "light">("full");
  const [exportPrompt, setExportPrompt] = useState(false);
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);

  const refresh = useCallback(async () => {
    try {
      const p = await api.current();
      setProject(p);
    } catch {
      setProject(null);
    }
  }, []);

  const loadWelcomeExtras = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([api.getSettings(), api.recentProjects()]);
      setAppSettings(s);
      setExportMode(s.opsz_default_mode);
      if (s.default_project_dir) setProjectDir((prev) => prev || s.default_project_dir || "");
      setRecent(r.recent);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    api.opencl().then((r) => setOpenclOk(r.available)).catch(() => setOpenclOk(false));
    loadWelcomeExtras();
  }, [loadWelcomeExtras]);

  const activeStep = useMemo(() => {
    if (!project) return "photogrammetry" as PipelineStep;
    const active = STEPS.find((s) => project.workflow[s]?.status === "active");
    return active ?? step;
  }, [project, step]);

  useEffect(() => {
    if (project) setStep(activeStep);
  }, [project, activeStep]);

  const hist = project?.history;

  async function doUndo() {
    setBusy(true);
    setError(null);
    try {
      const p = await api.undo();
      setProject(p);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function doRedo() {
    setBusy(true);
    setError(null);
    try {
      const p = await api.redo();
      setProject(p);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!project) return;
      const mod = e.ctrlKey || e.metaKey;
      if (!mod) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName?.toLowerCase();
      if (tag === "input" || tag === "textarea" || target?.isContentEditable) return;
      if (e.key === "z" && !e.shiftKey) {
        e.preventDefault();
        if (project.history?.can_undo) void doUndo();
      } else if (e.key === "y" || (e.key === "z" && e.shiftKey)) {
        e.preventDefault();
        if (project.history?.can_redo) void doRedo();
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [project]);

  async function browseNewFolder() {
    setError(null);
    try {
      const { path } = await api.pickDirectory();
      if (path) setProjectDir(path);
    } catch (e) {
      setError(String(e));
    }
  }

  async function create() {
    if (!projectDir.trim()) {
      setError("Choose a folder where the project will be saved.");
      return;
    }
    if (!name.trim()) {
      setError("Enter a project name.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const p = await api.createProject(name.trim(), projectDir.trim());
      setProject(p);
      setStep("photogrammetry");
      await loadWelcomeExtras();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function browseOpen() {
    setError(null);
    try {
      const { path } = await api.pickOpsx();
      if (path) {
        setOpenPath(path);
        setBusy(true);
        const p = await api.openProject(path);
        setProject(p);
        await loadWelcomeExtras();
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function openProj(path?: string) {
    const pth = (path ?? openPath).trim();
    if (!pth) {
      setError("Choose an existing .opsx project file.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const p = await api.openProject(pth);
      setProject(p);
      setOpenPath(pth);
      await loadWelcomeExtras();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function runExport(mode: "full" | "light") {
    setExportPrompt(false);
    setBusy(true);
    setError(null);
    try {
      const { path } = await api.pickOpszSave();
      if (!path) return;
      const p = await api.exportOpsz(path, mode);
      setProject(p);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function importArchive() {
    setError(null);
    try {
      const opsz = await api.pickOpszOpen();
      if (!opsz.path) return;
      const dest = await api.pickDirectory();
      if (!dest.path) {
        setError("Choose a destination folder for the imported project.");
        return;
      }
      setBusy(true);
      const p = await api.importOpsz(opsz.path, dest.path);
      setProject(p);
      await loadWelcomeExtras();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function closeProj() {
    setBusy(true);
    try {
      await api.closeProject();
      setProject(null);
      setStep("photogrammetry");
      await loadWelcomeExtras();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onSkipGeotiff(rgb: File, thermal: File) {
    setBusy(true);
    setError(null);
    noteLocal("Import GeoTIFFs", "Uploading orthophotos…");
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
    noteLocal(`OpenSfM (${modality})`, "Starting photogrammetry…");
    try {
      await api.runPhoto(modality);
      let cancelled = false;
      const poll = async () => {
        try {
          const st = await api.photoStatus(modality);
          if (cancelled) return;
          setLog(st.log);
          if (!st.running) {
            setBusy(false);
            await refresh();
            return;
          }
        } catch {
          /* ignore */
        }
        if (!cancelled) window.setTimeout(() => void poll(), 1500);
      };
      void poll();
      // Note: no cleanup handle if user navigates away; poll stops when job ends.
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  const settingsButton = (
    <button type="button" className="ghost" onClick={() => setSettingsOpen(true)} title="Settings">
      Settings
    </button>
  );

  if (!project) {
    return (
      <>
        <div className="app-shell">
          <div className="welcome">
            <div className="card" style={{ maxWidth: 580 }}>
              <div className="row" style={{ justifyContent: "space-between", marginBottom: "0.5rem" }}>
                <h2 style={{ margin: 0 }}>
                  <span style={{ color: "var(--accent)" }}>Open</span>PVScope
                </h2>
                {settingsButton}
              </div>
              <p>
                Start by creating a project on disk (autosaved continuously) or opening an existing{" "}
                <code>.opsx</code> file. Use <code>.opsz</code> only to import/export a portable archive.
              </p>

              {recent.length > 0 && (
                <>
                  <h3 style={{ margin: "1rem 0 0.5rem", fontSize: "1rem" }}>Recent</h3>
                  <ul className="recent-list">
                    {recent.map((r) => (
                      <li key={r.path}>
                        <button
                          type="button"
                          className="recent-item"
                          disabled={busy || !r.exists}
                          title={r.path}
                          onClick={() => openProj(r.path)}
                        >
                          <strong>{r.name}</strong>
                          <span className="muted">{r.exists ? r.path : `${r.path} (missing)`}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}

              <h3 style={{ margin: "1.25rem 0 0.5rem", fontSize: "1rem" }}>New project</h3>
              <div className="row" style={{ marginBottom: "0.5rem" }}>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="Project name"
                />
              </div>
              <div className="row" style={{ marginBottom: "0.75rem" }}>
                <input
                  type="text"
                  value={projectDir}
                  onChange={(e) => setProjectDir(e.target.value)}
                  placeholder="Parent folder (required)"
                  style={{ minWidth: 280, flex: 1 }}
                />
                <button type="button" onClick={browseNewFolder} disabled={busy}>
                  Browse…
                </button>
              </div>
              <button
                className="primary"
                disabled={busy || !name.trim() || !projectDir.trim()}
                onClick={create}
              >
                Create project
              </button>

              <h3 style={{ margin: "1.5rem 0 0.5rem", fontSize: "1rem" }}>Open existing</h3>
              <div className="row" style={{ marginBottom: "0.75rem" }}>
                <input
                  type="text"
                  value={openPath}
                  onChange={(e) => setOpenPath(e.target.value)}
                  placeholder="Path to project.opsx"
                  style={{ minWidth: 280, flex: 1 }}
                />
                <button type="button" onClick={browseOpen} disabled={busy}>
                  Browse…
                </button>
                <button type="button" disabled={busy || !openPath.trim()} onClick={() => openProj()}>
                  Open
                </button>
              </div>

              <h3 style={{ margin: "1.25rem 0 0.5rem", fontSize: "1rem" }}>Portable archive</h3>
              <div className="row">
                <button type="button" disabled={busy} onClick={importArchive}>
                  Import .opsz…
                </button>
              </div>

              {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
            </div>
          </div>
          <ActivityConsole />
        </div>
        <SettingsModal
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          onSaved={(s) => {
            setAppSettings(s);
            setExportMode(s.opsz_default_mode);
            if (s.default_project_dir) setProjectDir(s.default_project_dir);
            void loadWelcomeExtras();
          }}
        />
      </>
    );
  }

  return (
    <div className="app-shell">
      <div className="app">
        <aside className="sidebar">
          <div>
            <div className="brand">
              Open<span>PV</span>Scope
            </div>
            <div className="muted">{project.manifest.name}</div>
            <div className="muted" style={{ fontSize: "0.72rem", marginTop: "0.25rem" }}>
              Autosave on
            </div>
          </div>
          <ul className="steps">
            {STEPS.map((s, i) => {
              const st = project.workflow[s]?.status ?? "pending";
              const prevDone = STEPS.slice(0, i).every((prev) => {
                const ps = project.workflow[prev]?.status;
                return ps === "done" || ps === "skipped";
              });
              const unlocked = prevDone;
              return (
                <li key={s}>
                  <button
                    className={step === s ? "active" : ""}
                    disabled={!unlocked}
                    onClick={() => setStep(s)}
                  >
                    <span className={`badge ${st}`}>{i + 1}</span>
                    {STEP_LABELS[s]}
                  </button>
                </li>
              );
            })}
          </ul>
          <div className="row" style={{ flexWrap: "wrap" }}>
            <button className="ghost" onClick={() => setExportPrompt(true)} disabled={busy}>
              Export .opsz
            </button>
            <button className="ghost" onClick={closeProj} disabled={busy}>
              Close
            </button>
            {settingsButton}
          </div>
        </aside>
        <main className="main">
          <div className="topbar">
            <div>
              <strong>{STEP_LABELS[step]}</strong>
              <div className="muted" title={project.root}>
                {project.opsx_path ?? project.root}
                {openclOk === false && " · OpenCL not detected"}
                {openclOk === true && " · OpenCL OK"}
              </div>
            </div>
            <div className="row topbar-actions">
              <div className="history-nav" title="Undo / redo project changes (Ctrl+Z / Ctrl+Y)">
                <button
                  type="button"
                  className="icon-btn"
                  disabled={busy || !hist?.can_undo}
                  onClick={doUndo}
                  aria-label="Back"
                  title={hist?.undo_label ? `Back: ${hist.undo_label}` : "Back"}
                >
                  ←
                </button>
                <button
                  type="button"
                  className="icon-btn"
                  disabled={busy || !hist?.can_redo}
                  onClick={doRedo}
                  aria-label="Forward"
                  title={hist?.redo_label ? `Forward: ${hist.redo_label}` : "Forward"}
                >
                  →
                </button>
              </div>
              {project.orthos_ready && step !== "alignment" && (
                <button onClick={() => setStep("alignment")}>Ortho alignment / map</button>
              )}
            </div>
          </div>
          <div className={`content${step === "detection" || step === "segmentation" ? " content-plant" : ""}`}>
            {error && <p style={{ color: "var(--danger)" }}>{error}</p>}
            {step === "photogrammetry" && (
              <PhotogrammetryPanel
                busy={busy}
                log={log}
                onSkip={onSkipGeotiff}
                onUpload={async (modality, files) => {
                  noteLocal("Upload images", `Saving ${modality} files…`);
                  await api.uploadRaw(modality, files);
                }}
                onRun={runModality}
              />
            )}
            {step === "alignment" && (
              <OrthoAlignmentView
                project={project}
                onApplied={(p) => {
                  setProject(p);
                }}
                onError={setError}
              />
            )}
            {(step === "detection" || step === "segmentation") && project.orthos_ready && (
              <PlantWorkspace
                step={step}
                onProjectChange={setProject}
                onError={setError}
                refreshProject={refresh}
              />
            )}
            {(step === "detection" || step === "segmentation") && !project.orthos_ready && (
              <div className="card">
                <h2>{STEP_LABELS[step]}</h2>
                <p>Import or build orthophotos and complete alignment before using the plant map.</p>
              </div>
            )}
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

        <SettingsModal
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          onSaved={(s) => {
            setAppSettings(s);
            setExportMode(s.opsz_default_mode);
          }}
        />

        {exportPrompt && (
          <div className="modal-backdrop" onClick={() => setExportPrompt(false)}>
            <div className="modal-card settings-modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <h2>Export .opsz</h2>
                <button type="button" className="ghost" onClick={() => setExportPrompt(false)}>
                  Close
                </button>
              </div>
              <div className="settings-body">
                <p className="muted">
                  Full includes orthophotos and data. Light skips rebuildable folders (
                  {(appSettings?.opsz_light_exclude || ["work/", "photogrammetry/"]).join(", ")}
                  ). Undo history is never exported.
                </p>
                <label className="settings-field row-check">
                  <input
                    type="radio"
                    name="opszMode"
                    checked={exportMode === "full"}
                    onChange={() => setExportMode("full")}
                  />
                  <span>Full archive</span>
                </label>
                <label className="settings-field row-check">
                  <input
                    type="radio"
                    name="opszMode"
                    checked={exportMode === "light"}
                    onChange={() => setExportMode("light")}
                  />
                  <span>Light archive</span>
                </label>
              </div>
              <div className="settings-footer">
                <button type="button" className="ghost" onClick={() => setExportPrompt(false)}>
                  Cancel
                </button>
                <button
                  type="button"
                  className="primary"
                  disabled={busy}
                  onClick={() => runExport(exportMode)}
                >
                  Choose destination…
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
      <ActivityConsole />
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

function ScaffoldStep({ kind }: { kind: "models" | "outputs" | "classification" }) {
  const [msg, setMsg] = useState("Loading…");
  useEffect(() => {
    const load = async () => {
      if (kind === "models" || kind === "classification") setMsg((await api.ml()).message);
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
