import { useEffect, useState } from "react";
import { api, AppSettings } from "./api";
import { APP_LANGUAGES, AppLanguage, isAppLanguage, useI18n, useT } from "./i18n";

type Props = {
  open: boolean;
  onClose: () => void;
  onSaved?: (s: AppSettings) => void;
};

export function SettingsModal({ open, onClose, onSaved }: Props) {
  const t = useT();
  const { setLanguage } = useI18n();
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [excludeText, setExcludeText] = useState("");

  useEffect(() => {
    if (!open) return;
    setError(null);
    api
      .getSettings()
      .then((s) => {
        setSettings(s);
        setExcludeText((s.opsz_light_exclude || []).join("\n"));
      })
      .catch((e) => setError(String(e)));
  }, [open]);

  if (!open || !settings) {
    if (!open) return null;
    return (
      <div className="modal-backdrop" onClick={onClose}>
        <div className="modal-card settings-modal" onClick={(e) => e.stopPropagation()}>
          <div className="modal-header">
            <h2>{t("settings.title")}</h2>
            <button type="button" className="ghost" onClick={onClose}>
              {t("common.close")}
            </button>
          </div>
          <div style={{ padding: "1rem" }}>{error || t("common.loading")}</div>
        </div>
      </div>
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const lang: AppLanguage = isAppLanguage(settings!.language) ? settings!.language : "en";
      const patch: Parameters<typeof api.putSettings>[0] = {
        history_max_steps: settings!.history_max_steps,
        history_include_rasters: settings!.history_include_rasters,
        default_project_dir: settings!.default_project_dir,
        recent_max: settings!.recent_max,
        opsz_default_mode: settings!.opsz_default_mode,
        language: lang,
        opsz_light_exclude: excludeText
          .split(/\r?\n/)
          .map((l) => l.trim())
          .filter(Boolean),
      };
      if (!settings!.default_project_dir) {
        patch.clear_default_project_dir = true;
        delete patch.default_project_dir;
      }
      const s = await api.putSettings(patch);
      setSettings(s);
      if (isAppLanguage(s.language)) setLanguage(s.language);
      onSaved?.(s);
      onClose();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function clearRecent() {
    setBusy(true);
    try {
      const s = await api.putSettings({ clear_recent: true });
      setSettings(s);
      onSaved?.(s);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function browseDefault() {
    try {
      const { path } = await api.pickDirectory();
      if (path) setSettings({ ...settings!, default_project_dir: path });
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div>
            <h2>{t("settings.title")}</h2>
            <p className="muted" style={{ margin: 0 }}>
              {t("settings.subtitle")}
            </p>
          </div>
          <button type="button" className="ghost" onClick={onClose}>
            {t("common.close")}
          </button>
        </div>
        <div className="settings-body">
          {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

          <label className="settings-field" title={t("settings.languageTitle")}>
            <span>{t("settings.language")}</span>
            <select
              value={isAppLanguage(settings.language) ? settings.language : "en"}
              onChange={(e) => {
                const lang = e.target.value as AppLanguage;
                setSettings({ ...settings, language: lang });
              }}
            >
              {APP_LANGUAGES.map((l) => (
                <option key={l.id} value={l.id}>
                  {l.nativeLabel}
                </option>
              ))}
            </select>
          </label>

          <label className="settings-field" title={t("settings.historyLengthTitle")}>
            <span>{t("settings.historyLength")}</span>
            <input
              type="number"
              min={1}
              max={200}
              value={settings.history_max_steps}
              onChange={(e) =>
                setSettings({ ...settings, history_max_steps: Number(e.target.value) || 1 })
              }
            />
            <span className="muted">{t("settings.historyLengthHint")}</span>
          </label>

          <label className="settings-field row-check" title={t("settings.includeRastersTitle")}>
            <input
              type="checkbox"
              checked={settings.history_include_rasters}
              onChange={(e) =>
                setSettings({ ...settings, history_include_rasters: e.target.checked })
              }
            />
            <span>{t("settings.includeRasters")}</span>
          </label>
          <p className="muted settings-hint">{t("settings.rastersHint")}</p>

          <label className="settings-field" title={t("settings.defaultFolderTitle")}>
            <span>{t("settings.defaultParentFolder")}</span>
            <div className="row">
              <input
                type="text"
                value={settings.default_project_dir ?? ""}
                onChange={(e) =>
                  setSettings({
                    ...settings,
                    default_project_dir: e.target.value.trim() || null,
                  })
                }
                placeholder={t("settings.defaultFolderPlaceholder")}
                style={{ flex: 1, minWidth: 200 }}
              />
              <button type="button" onClick={browseDefault} disabled={busy}>
                {t("common.browse")}
              </button>
              <button
                type="button"
                className="ghost"
                disabled={busy || !settings.default_project_dir}
                onClick={() => setSettings({ ...settings, default_project_dir: null })}
              >
                {t("common.clear")}
              </button>
            </div>
          </label>

          <label className="settings-field" title={t("settings.recentMaxTitle")}>
            <span>{t("settings.recentMax")}</span>
            <input
              type="number"
              min={0}
              max={50}
              value={settings.recent_max}
              onChange={(e) =>
                setSettings({ ...settings, recent_max: Number(e.target.value) || 0 })
              }
            />
          </label>

          <label className="settings-field" title={t("settings.opszDefaultModeTitle")}>
            <span>{t("settings.opszDefaultMode")}</span>
            <select
              value={settings.opsz_default_mode}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  opsz_default_mode: e.target.value as "full" | "light",
                })
              }
            >
              <option value="full">{t("settings.opszFull")}</option>
              <option value="light">{t("settings.opszLight")}</option>
            </select>
          </label>

          <label className="settings-field" title={t("settings.lightExcludeTitle")}>
            <span>{t("settings.lightExclude")}</span>
            <textarea
              rows={4}
              value={excludeText}
              onChange={(e) => setExcludeText(e.target.value)}
              spellCheck={false}
            />
          </label>

          <div className="row" style={{ marginTop: "0.5rem" }}>
            <button type="button" className="ghost" disabled={busy} onClick={clearRecent}>
              {t("settings.clearRecent")}
            </button>
          </div>
        </div>
        <div className="settings-footer">
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            {t("common.cancel")}
          </button>
          <button type="button" className="primary" onClick={save} disabled={busy}>
            {t("settings.save")}
          </button>
        </div>
      </div>
    </div>
  );
}
