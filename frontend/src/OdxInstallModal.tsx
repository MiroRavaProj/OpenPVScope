import { useEffect, useState } from "react";
import { api, OdxInstallStatus } from "./api";
import { useT } from "./i18n";

type Props = {
  open: boolean;
  onClose: () => void;
  onInstalled: () => void | Promise<void>;
  onSkip: () => void | Promise<void>;
};

export function OdxInstallModal({ open, onClose, onInstalled, onSkip }: Props) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<OdxInstallStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setBusy(false);
      setStatus(null);
      setError(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open || !busy) return;
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await api.installOdxStatus();
        if (cancelled) return;
        setStatus(s);
        if (s.status === "done") {
          setBusy(false);
          await onInstalled();
          onClose();
        } else if (s.status === "error") {
          setBusy(false);
          setError(s.error || t("odxModal.installFailed"));
        }
      } catch (e) {
        if (!cancelled) {
          setBusy(false);
          setError(String(e));
        }
      }
    };
    void tick();
    const id = window.setInterval(() => void tick(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [open, busy, onClose, onInstalled, t]);

  if (!open) return null;

  async function startInstall() {
    setError(null);
    setBusy(true);
    try {
      const s = await api.installOdx();
      setStatus(s);
      if (s.status === "done") {
        setBusy(false);
        await onInstalled();
        onClose();
      } else if (s.status === "error") {
        setBusy(false);
        setError(s.error || t("odxModal.installFailed"));
      }
    } catch (e) {
      setBusy(false);
      setError(String(e));
    }
  }

  async function skip() {
    setError(null);
    try {
      await onSkip();
      onClose();
    } catch (e) {
      setError(String(e));
    }
  }

  const pct =
    status?.progress != null && status.progress >= 0
      ? Math.min(100, Math.round(status.progress * 100))
      : null;

  return (
    <div className="modal-backdrop" onClick={busy ? undefined : onClose}>
      <div className="modal-card settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{t("odxModal.title")}</h2>
          {!busy && (
            <button type="button" className="ghost" onClick={onClose}>
              {t("common.close")}
            </button>
          )}
        </div>
        <div style={{ padding: "1rem", display: "flex", flexDirection: "column", gap: "0.75rem" }}>
          <p>{t("odxModal.body")}</p>
          <p className="muted">{t("odxModal.agpl")}</p>
          {busy && (
            <div>
              <p className="muted">{status?.message || t("odxModal.installing")}</p>
              <div className="console-progress-track" style={{ marginTop: "0.5rem" }}>
                {pct == null ? (
                  <div className="console-progress-indeterminate" />
                ) : (
                  <div className="console-progress-bar" style={{ width: `${pct}%` }} />
                )}
              </div>
            </div>
          )}
          {error && <p className="warn">{error}</p>}
          <div className="row" style={{ gap: "0.5rem", flexWrap: "wrap" }}>
            <button type="button" className="primary" disabled={busy} onClick={() => void startInstall()}>
              {t("odxModal.install")}
            </button>
            <button type="button" className="ghost" disabled={busy} onClick={() => void skip()}>
              {t("odxModal.skip")}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
