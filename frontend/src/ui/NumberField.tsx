import { useEffect, useRef, useState } from "react";

function clamp(n: number, min?: number, max?: number): number {
  let v = n;
  if (min != null && Number.isFinite(min)) v = Math.max(min, v);
  if (max != null && Number.isFinite(max)) v = Math.min(max, v);
  return v;
}

function formatValue(n: number, step?: number): string {
  if (!Number.isFinite(n)) return "";
  if (step != null && step > 0 && step < 1) {
    const decimals = Math.min(6, (String(step).split(".")[1] || "").length);
    return String(Number(n.toFixed(decimals)));
  }
  if (Number.isInteger(n)) return String(n);
  return String(n);
}

/** Controlled numeric input that allows clearing/editing without snapping to 0. */
export function NumberField(props: {
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  step?: number;
  disabled?: boolean;
  className?: string;
  title?: string;
  id?: string;
}) {
  const [text, setText] = useState(() => formatValue(props.value, props.step));
  const focusedRef = useRef(false);

  useEffect(() => {
    if (!focusedRef.current) {
      setText(formatValue(props.value, props.step));
    }
  }, [props.value, props.step]);

  function commit(raw: string): boolean {
    const trimmed = raw.trim();
    if (trimmed === "" || trimmed === "-" || trimmed === "." || trimmed === "-.") {
      return false;
    }
    const n = Number(trimmed);
    if (!Number.isFinite(n)) return false;
    const next = clamp(n, props.min, props.max);
    props.onChange(next);
    setText(formatValue(next, props.step));
    return true;
  }

  return (
    <input
      id={props.id}
      type="text"
      inputMode="decimal"
      className={props.className}
      title={props.title}
      disabled={props.disabled}
      value={text}
      onFocus={() => {
        focusedRef.current = true;
      }}
      onChange={(e) => {
        const v = e.target.value.replace(",", ".");
        if (v === "" || v === "-" || v === "." || v === "-.") {
          setText(v);
          return;
        }
        if (!/^-?\d*\.?\d*$/.test(v)) return;
        setText(v);
        // Commit only when the draft is a complete finite number (not trailing ".")
        if (!v.endsWith(".") && v !== "-") {
          const n = Number(v);
          if (Number.isFinite(n)) {
            props.onChange(clamp(n, props.min, props.max));
          }
        }
      }}
      onBlur={() => {
        focusedRef.current = false;
        if (!commit(text)) {
          setText(formatValue(props.value, props.step));
        }
      }}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          (e.target as HTMLInputElement).blur();
        }
      }}
    />
  );
}
