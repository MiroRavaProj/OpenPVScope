import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  OdxOptions,
  PhotoJobPublic,
  PhotoModalities,
  PhotoModality,
  PhotoMode,
  PhotoProductItem,
  PhotoProducts,
  PhotoRunBody,
  PhotoSetup,
  PhotoThermalParams,
  DjiSdkInfo,
  OdxInfo,
} from "./api";
import { useConsole } from "./ActivityConsole";
import { useT } from "./i18n";

type EngineInfo = {
  odx: OdxInfo | null;
  dji_sdk: DjiSdkInfo | null;
};

const DEFAULT_THERMAL: Required<PhotoThermalParams> = {
  emissivity: 0.95,
  distance: 5,
  humidity: 50,
  reflection: 25,
  parametric_fallback: false,
};

const DEFAULT_ODX: OdxOptions = {
  orthophoto_resolution: 2,
  feature_quality: "high",
  pc_quality: "medium",
  fast_orthophoto: false,
  crop: 3,
};

const DEFAULT_PRODUCTS: PhotoProducts = {
  ortho: true,
  dense_pc: false,
  sparse_pc: false,
  dsm: false,
  dtm: false,
};

function ModalityCard(props: {
  modality: PhotoModality;
  title: string;
  fileCount: number;
  busy: boolean;
  odxOk: boolean;
  dragging: boolean;
  onDrag: (over: boolean) => void;
  onFiles: (files: FileList | File[]) => void;
  onRun: () => void;
  orthoReady: boolean;
}) {
  const t = useT();
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div
      className={`photo-modality-card${props.dragging ? " drag-over" : ""}${!props.odxOk ? " photo-modality-disabled" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        if (!props.odxOk) return;
        props.onDrag(true);
      }}
      onDragLeave={() => props.onDrag(false)}
      onDrop={(e) => {
        e.preventDefault();
        props.onDrag(false);
        if (!props.odxOk) return;
        if (e.dataTransfer.files?.length) props.onFiles(e.dataTransfer.files);
      }}
    >
      <div className="photo-modality-head">
        <h3>{props.title}</h3>
        {props.orthoReady && (
          <span className="photo-badge ready">
            {props.modality === "rgb" ? t("photo.badgeRgbReady") : t("photo.badgeThermalReady")}
          </span>
        )}
      </div>
      <p className="muted photo-drop-hint">{t("photo.dropHint")}</p>
      <div className="row photo-modality-actions">
        <button
          type="button"
          className="ghost"
          disabled={props.busy || !props.odxOk}
          onClick={() => inputRef.current?.click()}
        >
          {t("photo.chooseFiles")}
        </button>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept="image/*,.tif,.tiff,.jpg,.jpeg,.png,.dng,.raw"
          hidden
          onChange={(e) => {
            if (e.target.files?.length) props.onFiles(e.target.files);
            e.target.value = "";
          }}
        />
        <button
          type="button"
          className="primary"
          disabled={props.busy || !props.odxOk || props.fileCount < 1}
          onClick={props.onRun}
        >
          {props.modality === "rgb" ? t("photo.runRgb") : t("photo.runThermal")}
        </button>
      </div>
      <p className="muted photo-file-count">
        {t("photo.fileCount", { count: props.fileCount })}
      </p>
    </div>
  );
}

export function PhotogrammetryView(props: {
  onProjectRefresh: () => void | Promise<void>;
  onError: (msg: string | null) => void;
  onOrthosReady: () => void | Promise<void>;
  busy: boolean;
  setBusy: (v: boolean) => void;
  onRequestInstallOdx?: () => void;
  onOdxAvailabilityChange?: (ok: boolean) => void;
  setupReloadToken?: number;
}) {
  const t = useT();
  const { noteLocal } = useConsole();
  const [engine, setEngine] = useState<EngineInfo>({ odx: null, dji_sdk: null });
  const [setup, setSetup] = useState<PhotoSetup | null>(null);
  const [wizardStep, setWizardStep] = useState<1 | 2 | null>(null);
  const [draftMods, setDraftMods] = useState<PhotoModalities>("rgb_and_thermal");
  const [draftMode, setDraftMode] = useState<PhotoMode>("process");

  const [rgbCount, setRgbCount] = useState(0);
  const [thermalCount, setThermalCount] = useState(0);
  const [rgbOrtho, setRgbOrtho] = useState(false);
  const [thermalOrtho, setThermalOrtho] = useState(false);
  const [activeModality, setActiveModality] = useState<PhotoModality | "both" | null>(null);
  const [job, setJob] = useState<PhotoJobPublic | null>(null);
  const [running, setRunning] = useState(false);
  const [thermal, setThermal] = useState<Required<PhotoThermalParams>>({ ...DEFAULT_THERMAL });
  const [odxOpts, setOdxOpts] = useState<OdxOptions>({ ...DEFAULT_ODX });
  const [products, setProducts] = useState<PhotoProducts>({ ...DEFAULT_PRODUCTS });
  const [productLists, setProductLists] = useState<Record<string, PhotoProductItem[]>>({
    rgb: [],
    thermal: [],
  });
  const [dragRgb, setDragRgb] = useState(false);
  const [dragTh, setDragTh] = useState(false);
  const [skipRgb, setSkipRgb] = useState<File | null>(null);
  const [skipTh, setSkipTh] = useState<File | null>(null);
  const pollRef = useRef<number | null>(null);
  const cancelledRef = useRef(false);
  const propsRef = useRef(props);
  propsRef.current = props;

  const stopPoll = useCallback(() => {
    cancelledRef.current = true;
    if (pollRef.current != null) {
      window.clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const applySetup = useCallback((s: PhotoSetup) => {
    setSetup(s);
    setDraftMods(s.modalities);
    setDraftMode(s.mode);
    setOdxOpts({ ...DEFAULT_ODX, ...s.odx });
    setProducts({ ...DEFAULT_PRODUCTS, ...s.products, ortho: true });
    setWizardStep(s.wizard_complete ? null : 1);
  }, []);

  const refreshRaw = useCallback(async () => {
    const [rgb, th] = await Promise.all([api.listRaw("rgb"), api.listRaw("thermal")]);
    setRgbCount(rgb.count);
    setThermalCount(th.count);
  }, []);

  const refreshProducts = useCallback(async () => {
    try {
      const [rgb, th] = await Promise.all([
        api.listPhotoProducts("rgb"),
        api.listPhotoProducts("thermal"),
      ]);
      setProductLists({ rgb: rgb.products, thermal: th.products });
    } catch {
      /* ignore */
    }
  }, []);

  const onOdxAvailabilityChange = props.onOdxAvailabilityChange;
  const loadEngine = useCallback(async () => {
    try {
      const h = await api.health();
      setEngine({ odx: h.odx ?? null, dji_sdk: h.dji_sdk ?? null });
      onOdxAvailabilityChange?.(Boolean(h.odx?.available));
    } catch {
      /* ignore */
    }
  }, [onOdxAvailabilityChange]);

  const [installBusy, setInstallBusy] = useState(false);
  const refreshOrthos = useCallback(async () => {
    try {
      const [rgb, th] = await Promise.all([api.photoStatus("rgb"), api.photoStatus("thermal")]);
      setEngine({ odx: rgb.odx, dji_sdk: rgb.dji_sdk });
      setRgbOrtho(rgb.ortho_exists);
      setThermalOrtho(th.ortho_exists);
      const activeJob =
        rgb.job?.status === "running" ? rgb.job : th.job?.status === "running" ? th.job : rgb.job || th.job;
      setJob(activeJob ?? null);
      if (rgb.running || th.running) {
        setRunning(true);
        if (rgb.job?.status === "running") setActiveModality("rgb");
        else if (th.job?.status === "running") setActiveModality("thermal");
      }
    } catch {
      /* no project yet */
    }
  }, []);

  const pollUntilDone = useCallback(
    (modality: PhotoModality | "both") => {
      stopPoll();
      cancelledRef.current = false;
      const tick = async () => {
        if (cancelledRef.current) return;
        try {
          const [rgb, th] = await Promise.all([api.photoStatus("rgb"), api.photoStatus("thermal")]);
          if (cancelledRef.current) return;
          setEngine({ odx: rgb.odx, dji_sdk: rgb.dji_sdk });
          setRgbOrtho(rgb.ortho_exists);
          setThermalOrtho(th.ortho_exists);
          setRunning(rgb.running);

          const prefer: PhotoModality =
            modality === "thermal"
              ? "thermal"
              : modality === "rgb"
                ? "rgb"
                : th.job?.status === "running"
                  ? "thermal"
                  : "rgb";
          const preferred = prefer === "rgb" ? rgb : th;
          setJob(preferred.job ?? (prefer === "rgb" ? th.job : rgb.job) ?? null);
          if (preferred.job?.status === "running") setActiveModality(prefer);
          else if (rgb.job?.status === "running") setActiveModality("rgb");
          else if (th.job?.status === "running") setActiveModality("thermal");
          else if (modality === "both" && rgb.running) setActiveModality("both");

          if (!rgb.running) {
            propsRef.current.setBusy(false);
            setRunning(false);
            setActiveModality(null);
            await refreshProducts();
            await propsRef.current.onProjectRefresh();
            if (rgb.ortho_exists && th.ortho_exists) await propsRef.current.onOrthosReady();
            return;
          }
        } catch {
          /* ignore transient */
        }
        if (!cancelledRef.current) {
          pollRef.current = window.setTimeout(() => void tick(), 1500);
        }
      };
      void tick();
    },
    [stopPoll, refreshProducts],
  );

  useEffect(() => {
    void loadEngine();
    void refreshRaw().catch(() => undefined);
    void (async () => {
      try {
        const s = await api.getPhotoSetup();
        applySetup(s);
      } catch {
        setWizardStep(1);
      }
      await refreshOrthos();
      await refreshProducts();
      try {
        const rgb = await api.photoStatus("rgb");
        if (rgb.running) {
          propsRef.current.setBusy(true);
          const th = await api.photoStatus("thermal");
          const mode: PhotoModality | "both" =
            rgb.job?.status === "running"
              ? "rgb"
              : th.job?.status === "running"
                ? "thermal"
                : "both";
          setActiveModality(mode);
          pollUntilDone(mode);
        }
      } catch {
        /* ignore */
      }
    })();
    return () => stopPoll();
  }, [
    loadEngine,
    refreshRaw,
    refreshOrthos,
    refreshProducts,
    stopPoll,
    pollUntilDone,
    applySetup,
    props.setupReloadToken,
  ]);

  async function persistSetup(patch: Partial<PhotoSetup>) {
    const body = {
      wizard_complete: patch.wizard_complete ?? setup?.wizard_complete ?? false,
      modalities: patch.modalities ?? setup?.modalities ?? draftMods,
      mode: patch.mode ?? setup?.mode ?? draftMode,
      odx: patch.odx ?? odxOpts,
      products: { ...products, ...(patch.products ?? {}), ortho: true },
    };
    const saved = await api.putPhotoSetup(body);
    applySetup(saved);
    return saved;
  }

  async function finishWizard() {
    props.onError(null);
    const hasOdx = Boolean(engine.odx?.available);
    if (draftMode === "process" && !hasOdx) {
      props.onRequestInstallOdx?.();
      return;
    }
    try {
      await persistSetup({
        wizard_complete: true,
        modalities: draftMods,
        mode: draftMode,
        odx: odxOpts,
        products,
      });
      setWizardStep(null);
    } catch (e) {
      props.onError(String(e));
    }
  }

  async function installOdxFromStrip() {
    props.onError(null);
    if (props.onRequestInstallOdx) {
      props.onRequestInstallOdx();
      return;
    }
    setInstallBusy(true);
    try {
      let s = await api.installOdx();
      while (s.status === "running") {
        await new Promise((r) => setTimeout(r, 1500));
        s = await api.installOdxStatus();
      }
      if (s.status === "error") throw new Error(s.error || t("odxModal.installFailed"));
      await loadEngine();
    } catch (e) {
      props.onError(String(e));
    } finally {
      setInstallBusy(false);
    }
  }

  function runBody(): PhotoRunBody {
    return {
      ...thermal,
      odx: odxOpts,
      products: { ...products, ortho: true },
    };
  }

  async function upload(modality: PhotoModality, files: FileList | File[]) {
    props.setBusy(true);
    props.onError(null);
    noteLocal(t("app.consoleUploadImages"), t("app.consoleSavingFiles", { modality }));
    try {
      await api.uploadRaw(modality, files);
      await refreshRaw();
    } catch (e) {
      props.onError(String(e));
    } finally {
      props.setBusy(false);
    }
  }

  async function runOne(modality: PhotoModality) {
    props.setBusy(true);
    props.onError(null);
    try {
      await persistSetup({ odx: odxOpts, products });
    } catch {
      /* non-fatal */
    }
    noteLocal(t("app.consoleOdx", { modality }), t("app.consoleStartingPhoto"));
    try {
      await api.runPhoto(modality, runBody());
      setActiveModality(modality);
      setRunning(true);
      pollUntilDone(modality);
    } catch (e) {
      props.onError(String(e));
      props.setBusy(false);
    }
  }

  async function runBoth() {
    props.setBusy(true);
    props.onError(null);
    try {
      await persistSetup({ odx: odxOpts, products });
    } catch {
      /* non-fatal */
    }
    noteLocal(t("app.consoleOdx", { modality: "rgb+thermal" }), t("app.consoleStartingPhoto"));
    try {
      await api.runPhotoBoth(runBody());
      setActiveModality("both");
      setRunning(true);
      pollUntilDone("both");
    } catch (e) {
      props.onError(String(e));
      props.setBusy(false);
    }
  }

  async function cancel() {
    const m: PhotoModality =
      activeModality === "thermal" ? "thermal" : activeModality === "rgb" ? "rgb" : "rgb";
    try {
      noteLocal(t("photo.cancelTitle"), t("photo.cancelDetail", { modality: m }));
      await api.cancelPhoto(m);
      if (activeModality === "both") {
        await api.cancelPhoto("thermal").catch(() => undefined);
      }
    } catch (e) {
      props.onError(String(e));
    }
  }

  async function onSkip() {
    if (!skipTh) return;
    const needRgb = (setup?.modalities ?? draftMods) !== "thermal_only";
    if (needRgb && !skipRgb) return;
    props.setBusy(true);
    props.onError(null);
    noteLocal(t("app.consoleImportGeotiffs"), t("app.consoleUploadingOrthos"));
    try {
      const p = await api.skipPhotogrammetry(skipTh, needRgb ? skipRgb : null);
      setRgbOrtho(!p.rgb_ortho_missing);
      setThermalOrtho(Boolean(p.thermal_ortho_ready));
      await props.onProjectRefresh();
      if (p.orthos_ready) await props.onOrthosReady();
    } catch (e) {
      props.onError(String(e));
    } finally {
      props.setBusy(false);
    }
  }

  const stagePct =
    job && job.stage_total > 0
      ? Math.min(100, Math.round((job.stage_index / job.stage_total) * 100))
      : running
        ? null
        : 0;

  const odxRoot = engine.odx?.root ?? null;
  const odxOk = Boolean(engine.odx?.available);
  const thermalOnly = (setup?.modalities ?? draftMods) === "thermal_only";
  const isSkip = (setup?.mode ?? draftMode) === "skip";
  const showRgb = !thermalOnly;

  if (wizardStep === 1) {
    return (
      <div className="photo-workspace">
        <div className="card photo-wizard">
          <h2>{t("photo.wizard.title")}</h2>
          <p className="muted">{t("photo.wizard.q1")}</p>
          <div className="photo-wizard-choices">
            <button
              type="button"
              className={`photo-wizard-choice${draftMods === "rgb_and_thermal" ? " selected" : ""}`}
              onClick={() => setDraftMods("rgb_and_thermal")}
            >
              <strong>{t("photo.wizard.bothTitle")}</strong>
              <span className="muted">{t("photo.wizard.bothHint")}</span>
            </button>
            <button
              type="button"
              className={`photo-wizard-choice${draftMods === "thermal_only" ? " selected" : ""}`}
              onClick={() => setDraftMods("thermal_only")}
            >
              <strong>{t("photo.wizard.thermalTitle")}</strong>
              <span className="muted">{t("photo.wizard.thermalHint")}</span>
            </button>
          </div>
          <div className="row" style={{ marginTop: "1rem" }}>
            <button type="button" className="primary" onClick={() => setWizardStep(2)}>
              {t("photo.wizard.next")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  if (wizardStep === 2) {
    return (
      <div className="photo-workspace">
        <div className="card photo-wizard">
          <h2>{t("photo.wizard.title")}</h2>
          <p className="muted">{t("photo.wizard.q2")}</p>
          <div className="photo-wizard-choices">
            <button
              type="button"
              className={`photo-wizard-choice${draftMode === "process" ? " selected" : ""}`}
              onClick={() => {
                if (!odxOk) {
                  setDraftMode("process");
                  props.onRequestInstallOdx?.();
                  return;
                }
                setDraftMode("process");
              }}
            >
              <strong>{t("photo.wizard.processTitle")}</strong>
              <span className="muted">
                {odxOk ? t("photo.wizard.processHint") : t("photo.odxHint")}
              </span>
            </button>
            <button
              type="button"
              className={`photo-wizard-choice${draftMode === "skip" ? " selected" : ""}`}
              onClick={() => setDraftMode("skip")}
            >
              <strong>{t("photo.wizard.skipTitle")}</strong>
              <span className="muted">{t("photo.wizard.skipHint")}</span>
            </button>
          </div>
          <div className="row" style={{ marginTop: "1rem", gap: "0.5rem" }}>
            <button type="button" className="ghost" onClick={() => setWizardStep(1)}>
              {t("photo.wizard.back")}
            </button>
            <button type="button" className="primary" onClick={() => void finishWizard()}>
              {t("photo.wizard.continue")}
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="photo-workspace">
      <div className="card photo-engine-strip">
        <div className="photo-engine-head-row">
          <h2>{t("photo.title")}</h2>
          <button
            type="button"
            className="ghost"
            disabled={props.busy || running}
            onClick={() => {
              setDraftMods(setup?.modalities ?? "rgb_and_thermal");
              setDraftMode(setup?.mode ?? "process");
              setWizardStep(1);
            }}
          >
            {t("photo.wizard.changeSetup")}
          </button>
        </div>
        <p>{t("photo.blurb")}</p>
        {thermalOnly && (
          <p className="photo-gate-note muted">{t("photo.wizard.thermalOnlyNote")}</p>
        )}
        <div className="photo-engine-grid">
          <div className="photo-engine-item">
            <span className="photo-engine-label">ODX</span>
            {odxOk && odxRoot ? (
              <span className="photo-engine-value" title={odxRoot}>
                {odxRoot}
              </span>
            ) : (
              <span className="photo-engine-value warn">
                {t("photo.odxMissing")}
                <span className="muted"> — {t("photo.odxHint")}</span>
              </span>
            )}
            {!odxOk && (
              <button
                type="button"
                className="primary"
                style={{ marginTop: "0.5rem" }}
                disabled={props.busy || running || installBusy}
                onClick={() => void installOdxFromStrip()}
              >
                {installBusy ? t("photo.odxInstalling") : t("photo.odxInstall")}
              </button>
            )}
          </div>
          <div className="photo-engine-item">
            <span className="photo-engine-label">{t("photo.djiLabel")}</span>
            <span className={`photo-engine-value${engine.dji_sdk?.available ? "" : " warn"}`}>
              {engine.dji_sdk?.available ? t("photo.djiOk") : t("photo.djiMissing")}
            </span>
          </div>
        </div>
        {(rgbOrtho || thermalOrtho) && (
          <div className="photo-ready-row">
            {rgbOrtho && <span className="photo-badge ready">{t("photo.badgeRgbReady")}</span>}
            {thermalOrtho && (
              <span className="photo-badge ready">{t("photo.badgeThermalReady")}</span>
            )}
          </div>
        )}
      </div>

      {isSkip ? (
        <div className="card photo-skip">
          <h3>{t("photo.skipTitle")}</h3>
          <p className="muted">{t("photo.skipIntro")}</p>
          <div className="row">
            {showRgb && (
              <label className="tool-field">
                {t("photo.rgbGeotiff")}
                <input
                  type="file"
                  accept=".tif,.tiff"
                  disabled={props.busy}
                  onChange={(e) => setSkipRgb(e.target.files?.[0] ?? null)}
                />
              </label>
            )}
            <label className="tool-field">
              {t("photo.thermalGeotiff")}
              <input
                type="file"
                accept=".tif,.tiff"
                disabled={props.busy}
                onChange={(e) => setSkipTh(e.target.files?.[0] ?? null)}
              />
            </label>
            <button
              type="button"
              className="primary"
              disabled={props.busy || !skipTh || (showRgb && !skipRgb)}
              onClick={() => void onSkip()}
            >
              {t("photo.importContinue")}
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="photo-modality-grid">
            {showRgb && (
              <ModalityCard
                modality="rgb"
                title={t("photo.rgbTitle")}
                fileCount={rgbCount}
                busy={props.busy}
                odxOk={odxOk}
                dragging={dragRgb}
                onDrag={setDragRgb}
                onFiles={(f) => void upload("rgb", f)}
                onRun={() => void runOne("rgb")}
                orthoReady={rgbOrtho}
              />
            )}
            <ModalityCard
              modality="thermal"
              title={t("photo.thermalTitle")}
              fileCount={thermalCount}
              busy={props.busy}
              odxOk={odxOk}
              dragging={dragTh}
              onDrag={setDragTh}
              onFiles={(f) => void upload("thermal", f)}
              onRun={() => void runOne("thermal")}
              orthoReady={thermalOrtho}
            />
          </div>

          <div className={`card photo-odx-opts${!odxOk ? " photo-odx-opts-disabled" : ""}`}>
            <fieldset disabled={props.busy || !odxOk} style={{ border: 0, margin: 0, padding: 0 }}>
            <h3>{t("photo.odxOpts.title")}</h3>
            <p className="muted">{t("photo.odxOpts.hint")}</p>
            <div className="tool-grid2">
              <label className="tool-field" title={t("photo.odxOpts.resolutionTitle")}>
                {t("photo.odxOpts.resolution")}
                <input
                  type="number"
                  min={0.1}
                  max={100}
                  step={0.1}
                  value={odxOpts.orthophoto_resolution}
                  disabled={props.busy}
                  onChange={(e) =>
                    setOdxOpts((p) => ({
                      ...p,
                      orthophoto_resolution: Number(e.target.value) || 2,
                    }))
                  }
                />
              </label>
              <label className="tool-field" title={t("photo.odxOpts.featureTitle")}>
                {t("photo.odxOpts.feature")}
                <select
                  value={odxOpts.feature_quality}
                  disabled={props.busy}
                  onChange={(e) =>
                    setOdxOpts((p) => ({
                      ...p,
                      feature_quality: e.target.value as OdxOptions["feature_quality"],
                    }))
                  }
                >
                  {(["ultra", "high", "medium", "low", "lowest"] as const).map((q) => (
                    <option key={q} value={q}>
                      {q}
                    </option>
                  ))}
                </select>
              </label>
              <label className="tool-field" title={t("photo.odxOpts.pcTitle")}>
                {t("photo.odxOpts.pc")}
                <select
                  value={odxOpts.pc_quality}
                  disabled={props.busy}
                  onChange={(e) =>
                    setOdxOpts((p) => ({
                      ...p,
                      pc_quality: e.target.value as OdxOptions["pc_quality"],
                    }))
                  }
                >
                  {(["ultra", "high", "medium", "low", "lowest"] as const).map((q) => (
                    <option key={q} value={q}>
                      {q}
                    </option>
                  ))}
                </select>
              </label>
              <label className="tool-field" title={t("photo.odxOpts.cropTitle")}>
                {t("photo.odxOpts.crop")}
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={0.1}
                  value={odxOpts.crop}
                  disabled={props.busy}
                  onChange={(e) =>
                    setOdxOpts((p) => ({ ...p, crop: Number(e.target.value) || 0 }))
                  }
                />
              </label>
            </div>
            <label className="tool-field row-check" title={t("photo.odxOpts.fastTitle")}>
              <input
                type="checkbox"
                checked={odxOpts.fast_orthophoto}
                disabled={props.busy}
                onChange={(e) =>
                  setOdxOpts((p) => ({ ...p, fast_orthophoto: e.target.checked }))
                }
              />
              <span>{t("photo.odxOpts.fast")}</span>
            </label>
            </fieldset>
          </div>

          <div className="card photo-products">
            <fieldset disabled={props.busy || !odxOk} style={{ border: 0, margin: 0, padding: 0 }}>
            <h3>{t("photo.products.title")}</h3>
            <p className="muted">{t("photo.products.hint")}</p>
            <div className="photo-product-toggles">
              <label className="tool-field row-check">
                <input type="checkbox" checked disabled />
                <span>{t("photo.products.ortho")}</span>
              </label>
              {(
                [
                  ["dense_pc", "densePc"],
                  ["sparse_pc", "sparsePc"],
                  ["dsm", "dsm"],
                  ["dtm", "dtm"],
                ] as const
              ).map(([key, i18nKey]) => (
                <label key={key} className="tool-field row-check">
                  <input
                    type="checkbox"
                    checked={products[key]}
                    disabled={props.busy}
                    onChange={(e) =>
                      setProducts((p) => ({ ...p, [key]: e.target.checked, ortho: true }))
                    }
                  />
                  <span>{t(`photo.products.${i18nKey}`)}</span>
                </label>
              ))}
            </div>
            </fieldset>
          </div>

          <div className="card photo-thermal-params">
            <fieldset disabled={props.busy || !odxOk} style={{ border: 0, margin: 0, padding: 0 }}>
            <h3>{t("photo.thermalParams")}</h3>
            <p className="muted">{t("photo.thermalParamsHint")}</p>
            <div className="tool-grid2">
              <label className="tool-field" title={t("photo.emissivityTitle")}>
                {t("photo.emissivity")}
                <input
                  type="number"
                  min={0}
                  max={1}
                  step={0.01}
                  value={thermal.emissivity}
                  disabled={props.busy}
                  onChange={(e) =>
                    setThermal((p) => ({ ...p, emissivity: Number(e.target.value) || 0 }))
                  }
                />
              </label>
              <label className="tool-field" title={t("photo.distanceTitle")}>
                {t("photo.distance")}
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={thermal.distance}
                  disabled={props.busy}
                  onChange={(e) =>
                    setThermal((p) => ({ ...p, distance: Number(e.target.value) || 0 }))
                  }
                />
              </label>
              <label className="tool-field" title={t("photo.humidityTitle")}>
                {t("photo.humidity")}
                <input
                  type="number"
                  min={0}
                  max={100}
                  step={1}
                  value={thermal.humidity}
                  disabled={props.busy}
                  onChange={(e) =>
                    setThermal((p) => ({ ...p, humidity: Number(e.target.value) || 0 }))
                  }
                />
              </label>
              <label className="tool-field" title={t("photo.reflectionTitle")}>
                {t("photo.reflection")}
                <input
                  type="number"
                  step={0.1}
                  value={thermal.reflection}
                  disabled={props.busy}
                  onChange={(e) =>
                    setThermal((p) => ({ ...p, reflection: Number(e.target.value) || 0 }))
                  }
                />
              </label>
            </div>
            <label className="tool-field row-check" title={t("photo.parametricTitle")}>
              <input
                type="checkbox"
                checked={thermal.parametric_fallback}
                disabled={props.busy}
                onChange={(e) =>
                  setThermal((p) => ({ ...p, parametric_fallback: e.target.checked }))
                }
              />
              <span>{t("photo.parametric")}</span>
            </label>
            {showRgb && (
              <div className="row" style={{ marginTop: "0.75rem" }}>
                <button
                  type="button"
                  className="primary"
                  disabled={props.busy || !odxOk || rgbCount < 1 || thermalCount < 1}
                  onClick={() => void runBoth()}
                >
                  {t("photo.runBoth")}
                </button>
              </div>
            )}
            </fieldset>
          </div>

          {(running || job) && (
            <div className="card photo-stages">
              <div className="photo-stages-head">
                <h3>{t("photo.pipeline")}</h3>
                {job?.cancelable && (
                  <button
                    type="button"
                    className="ghost"
                    disabled={!running}
                    onClick={() => void cancel()}
                  >
                    {t("photo.cancel")}
                  </button>
                )}
              </div>
              <p className="muted">
                {activeModality
                  ? t("photo.activeModality", { modality: activeModality })
                  : t("common.emDash")}
                {job?.stage_name ? ` · ${job.stage_name}` : ""}
                {job
                  ? ` · ${t("photo.stageProgress", {
                      index: job.stage_index,
                      total: job.stage_total,
                    })}`
                  : ""}
              </p>
              <div className="console-progress-track photo-progress-track">
                {stagePct == null ? (
                  <div className="console-progress-indeterminate" />
                ) : (
                  <div
                    className={`console-progress-bar${job?.status === "error" ? " error" : ""}`}
                    style={{ width: `${stagePct}%` }}
                  />
                )}
              </div>
              {job?.error && <p style={{ color: "var(--danger)" }}>{job.error}</p>}
            </div>
          )}

          {(productLists.rgb.length > 0 || productLists.thermal.length > 0) && (
            <div className="card photo-outputs">
              <h3>{t("photo.products.outputs")}</h3>
              {(["rgb", "thermal"] as const).map((m) =>
                productLists[m].length ? (
                  <div key={m} className="photo-outputs-block">
                    <strong>{m === "rgb" ? t("photo.rgbTitle") : t("photo.thermalTitle")}</strong>
                    <ul className="photo-outputs-list">
                      {productLists[m].map((p) => (
                        <li key={p.id} title={p.path}>
                          {p.label}: <span className="muted">{p.path}</span>
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null,
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}
