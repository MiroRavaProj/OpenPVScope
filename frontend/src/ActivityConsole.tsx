import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { api, ConsoleEntry, ConsoleJob, ConsoleSnapshot } from "./api";

export type ConsoleMode = "info" | "verbose";

type ConsoleContextValue = {
  entries: ConsoleEntry[];
  job: ConsoleJob | null;
  mode: ConsoleMode;
  setMode: (m: ConsoleMode) => void;
  expanded: boolean;
  setExpanded: (v: boolean) => void;
  height: number;
  setHeight: (h: number) => void;
  clear: () => void;
  /** Client-side busy hint when server has not started a job yet */
  noteLocal: (title: string, detail?: string) => void;
};

const ConsoleCtx = createContext<ConsoleContextValue | null>(null);

const MIN_H = 120;
const MAX_H = 480;
const DEFAULT_H = 200;

export function ConsoleProvider({ children }: { children: ReactNode }) {
  const [entries, setEntries] = useState<ConsoleEntry[]>([]);
  const [job, setJob] = useState<ConsoleJob | null>(null);
  const [mode, setMode] = useState<ConsoleMode>(() => {
    try {
      return (localStorage.getItem("ops.consoleMode") as ConsoleMode) || "info";
    } catch {
      return "info";
    }
  });
  const [expanded, setExpanded] = useState(false);
  const [height, setHeight] = useState(() => {
    try {
      const n = Number(localStorage.getItem("ops.consoleHeight"));
      return Number.isFinite(n) ? Math.min(MAX_H, Math.max(MIN_H, n)) : DEFAULT_H;
    } catch {
      return DEFAULT_H;
    }
  });
  const seqRef = useRef(0);
  const localJobRef = useRef<ConsoleJob | null>(null);
  const pollStateRef = useRef({ inFlight: false });

  useEffect(() => {
    try {
      localStorage.setItem("ops.consoleMode", mode);
    } catch {
      /* ignore */
    }
  }, [mode]);

  useEffect(() => {
    try {
      localStorage.setItem("ops.consoleHeight", String(height));
    } catch {
      /* ignore */
    }
  }, [height]);

  const applySnapshot = useCallback((snap: ConsoleSnapshot) => {
    seqRef.current = snap.seq;
    if (snap.entries.length) {
      setEntries((prev) => {
        const seen = new Set(prev.map((e) => e.seq));
        const add = snap.entries.filter((e) => !seen.has(e.seq));
        if (!add.length) return prev;
        const merged = [...prev, ...add];
        return merged.length > 600 ? merged.slice(-600) : merged;
      });
    }
    if (snap.job) {
      setJob(snap.job);
      localJobRef.current = null;
    } else if (localJobRef.current) {
      setJob(localJobRef.current);
    } else {
      setJob(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let failStreak = 0;
    // Shared across Strict Mode remounts so overlapping polls cannot duplicate
    const pollState = pollStateRef.current;

    const schedule = (ms: number) => {
      if (cancelled) return;
      timer = window.setTimeout(() => {
        void tick();
      }, ms);
    };

    const tick = async () => {
      if (cancelled || pollState.inFlight) return;
      pollState.inFlight = true;
      try {
        const since = seqRef.current;
        const snap = await api.console(since);
        if (!cancelled) {
          failStreak = 0;
          applySnapshot(snap);
        }
        const busy = Boolean(snap.job && snap.job.status === "running");
        schedule(busy ? 400 : 900);
      } catch {
        failStreak = Math.min(failStreak + 1, 6);
        const delay = Math.min(30_000, 1500 * 2 ** failStreak);
        schedule(delay);
      } finally {
        pollState.inFlight = false;
      }
    };

    void tick();
    return () => {
      cancelled = true;
      if (timer != null) window.clearTimeout(timer);
    };
  }, [applySnapshot]);

  const clear = useCallback(() => {
    void api.consoleClear().catch(() => undefined);
    setEntries([]);
    setJob(null);
    localJobRef.current = null;
    seqRef.current = 0;
  }, []);

  const noteLocal = useCallback((title: string, detail?: string) => {
    const j: ConsoleJob = {
      id: `local-${Date.now()}`,
      title,
      status: "running",
      progress: null,
      detail: detail ?? null,
      started_at: Date.now() / 1000,
      ended_at: null,
    };
    localJobRef.current = j;
    setJob(j);
    setEntries((prev) => [
      ...prev,
      {
        seq: Date.now(),
        ts: Date.now() / 1000,
        level: "info",
        message: title,
        step: "local",
        job_id: j.id,
      },
    ]);
  }, []);

  const value = useMemo(
    () => ({
      entries,
      job,
      mode,
      setMode,
      expanded,
      setExpanded,
      height,
      setHeight,
      clear,
      noteLocal,
    }),
    [entries, job, mode, expanded, height, clear, noteLocal],
  );

  return <ConsoleCtx.Provider value={value}>{children}</ConsoleCtx.Provider>;
}

export function useConsole() {
  const ctx = useContext(ConsoleCtx);
  if (!ctx) throw new Error("useConsole requires ConsoleProvider");
  return ctx;
}

function formatTime(ts: number) {
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString(undefined, { hour12: false });
}

export function ActivityConsole() {
  const { entries, job, mode, setMode, expanded, setExpanded, height, setHeight, clear } =
    useConsole();
  const dragRef = useRef<{ startY: number; startH: number } | null>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const visible = useMemo(() => {
    if (mode === "verbose") return entries;
    return entries.filter((e) => e.level !== "verbose");
  }, [entries, mode]);

  useEffect(() => {
    if (!expanded || !listRef.current) return;
    listRef.current.scrollTop = listRef.current.scrollHeight;
  }, [visible, expanded]);

  const running = job?.status === "running";
  const progress = job?.progress;
  const showPulse = running && (progress == null || Number.isNaN(progress));

  function onDragStart(e: React.PointerEvent) {
    e.preventDefault();
    dragRef.current = { startY: e.clientY, startH: height };
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    if (!expanded) setExpanded(true);
  }

  function onDragMove(e: React.PointerEvent) {
    const d = dragRef.current;
    if (!d) return;
    const delta = d.startY - e.clientY;
    setHeight(Math.min(MAX_H, Math.max(MIN_H, d.startH + delta)));
  }

  function onDragEnd(e: React.PointerEvent) {
    if (!dragRef.current) return;
    dragRef.current = null;
    try {
      (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className={`activity-console ${expanded ? "expanded" : "minimized"} ${running ? "busy" : ""}`}>
      <div
        className="activity-console-grip"
        onPointerDown={onDragStart}
        onPointerMove={onDragMove}
        onPointerUp={onDragEnd}
        title="Drag to resize"
      />
      <div className="activity-progress-strip" aria-hidden>
        {showPulse ? (
          <div className="activity-progress-pulse" />
        ) : (
          <div
            className={`activity-progress-fill ${job?.status === "error" ? "error" : ""} ${job?.status === "done" ? "done" : ""}`}
            style={{
              width: `${running || job?.status === "done" || job?.status === "error" ? progress ?? (running ? 8 : 0) : 0}%`,
            }}
          />
        )}
      </div>

      <div className="activity-console-bar">
        <button
          type="button"
          className="ghost console-toggle"
          onClick={() => setExpanded(!expanded)}
          title={expanded ? "Minimize console" : "Expand console"}
        >
          {expanded ? "▾" : "▴"} Console
        </button>
        <div className="console-status muted">
          {job ? (
            <>
              <span className={`console-job-title ${job.status}`}>{job.title}</span>
              {job.detail ? <span className="console-job-detail"> — {job.detail}</span> : null}
              {typeof progress === "number" ? (
                <span className="console-job-pct"> {Math.round(progress)}%</span>
              ) : running ? (
                <span className="console-job-pct"> …</span>
              ) : null}
            </>
          ) : (
            <span>Idle</span>
          )}
        </div>
        <div className="row console-bar-actions">
          <div className="mode-toggle" role="group" aria-label="Console verbosity">
            <button
              type="button"
              className={mode === "info" ? "active" : ""}
              onClick={() => setMode("info")}
            >
              Info
            </button>
            <button
              type="button"
              className={mode === "verbose" ? "active" : ""}
              onClick={() => setMode("verbose")}
            >
              Verbose
            </button>
          </div>
          <button type="button" className="ghost" onClick={clear}>
            Clear
          </button>
        </div>
      </div>

      {expanded && (
        <div className="activity-console-body" style={{ height }}>
          {running && (
            <div className="console-progress-block">
              <div className="console-progress-track">
                {showPulse ? (
                  <div className="console-progress-indeterminate" />
                ) : (
                  <div
                    className="console-progress-bar"
                    style={{ width: `${Math.max(2, progress ?? 0)}%` }}
                  />
                )}
              </div>
              <div className="muted console-progress-label">
                {job?.detail || job?.title || "Working"}
                {typeof progress === "number" ? ` · ${Math.round(progress)}%` : ""}
              </div>
            </div>
          )}
          <div className="console-log" ref={listRef}>
            {visible.length === 0 ? (
              <div className="muted console-empty">No activity yet.</div>
            ) : (
              visible.map((e) => (
                <div key={e.seq} className={`console-line level-${e.level}`}>
                  <span className="console-time">{formatTime(e.ts)}</span>
                  <span className={`console-level`}>{e.level}</span>
                  {e.step ? <span className="console-step">{e.step}</span> : null}
                  <span className="console-msg">{e.message}</span>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}
