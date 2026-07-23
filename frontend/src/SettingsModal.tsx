import { useEffect, useState } from "react";
import { api, AppSettings } from "./api";

type Props = {
  open: boolean;
  onClose: () => void;
  onSaved?: (s: AppSettings) => void;
};

export function SettingsModal({ open, onClose, onSaved }: Props) {
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
            <h2>Settings</h2>
            <button type="button" className="ghost" onClick={onClose}>
              Close
            </button>
          </div>
          <div style={{ padding: "1rem" }}>{error || "Loading…"}</div>
        </div>
      </div>
    );
  }

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const patch: Parameters<typeof api.putSettings>[0] = {
        history_max_steps: settings!.history_max_steps,
        history_include_rasters: settings!.history_include_rasters,
        default_project_dir: settings!.default_project_dir,
        recent_max: settings!.recent_max,
        opsz_default_mode: settings!.opsz_default_mode,
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
            <h2>Settings</h2>
            <p className="muted" style={{ margin: 0 }}>
              App-wide preferences (not tied to a single project).
            </p>
          </div>
          <button type="button" className="ghost" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="settings-body">
          {error && <p style={{ color: "var(--danger)" }}>{error}</p>}

          <label className="settings-field">
            <span>Undo history length</span>
            <input
              type="number"
              min={1}
              max={200}
              value={settings.history_max_steps}
              onChange={(e) =>
                setSettings({ ...settings, history_max_steps: Number(e.target.value) || 1 })
              }
            />
            <span className="muted">How many Back steps to keep (Ctrl+Z).</span>
          </label>

          <label className="settings-field row-check">
            <input
              type="checkbox"
              checked={settings.history_include_rasters}
              onChange={(e) =>
                setSettings({ ...settings, history_include_rasters: e.target.checked })
              }
            />
            <span>Include orthophoto rasters in undo history</span>
          </label>
          <p className="muted settings-hint">
            Needed to undo alignment / GeoTIFF imports. History uses content-addressable
            storage (unchanged files are stored once). Keep{" "}
            <code>.openpvscope_history</code> on the same drive as the project.
          </p>

          <label className="settings-field">
            <span>Default project parent folder</span>
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
                placeholder="Optional — pre-fills New project"
                style={{ flex: 1, minWidth: 200 }}
              />
              <button type="button" onClick={browseDefault} disabled={busy}>
                Browse…
              </button>
              <button
                type="button"
                className="ghost"
                disabled={busy || !settings.default_project_dir}
                onClick={() => setSettings({ ...settings, default_project_dir: null })}
              >
                Clear
              </button>
            </div>
          </label>

          <label className="settings-field">
            <span>Recent projects to keep</span>
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

          <label className="settings-field">
            <span>Default .opsz export mode</span>
            <select
              value={settings.opsz_default_mode}
              onChange={(e) =>
                setSettings({
                  ...settings,
                  opsz_default_mode: e.target.value as "full" | "light",
                })
              }
            >
              <option value="full">Full (everything except undo history)</option>
              <option value="light">Light (skip work/ + photogrammetry/)</option>
            </select>
          </label>

          <label className="settings-field">
            <span>Light export exclude prefixes (one per line)</span>
            <textarea
              rows={4}
              value={excludeText}
              onChange={(e) => setExcludeText(e.target.value)}
              spellCheck={false}
            />
          </label>

          <div className="row" style={{ marginTop: "0.5rem" }}>
            <button type="button" className="ghost" disabled={busy} onClick={clearRecent}>
              Clear recent projects
            </button>
          </div>
        </div>
        <div className="settings-footer">
          <button type="button" className="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="primary" onClick={save} disabled={busy}>
            Save settings
          </button>
        </div>
      </div>
    </div>
  );
}
