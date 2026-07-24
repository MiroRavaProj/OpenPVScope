/** App UI language codes. */
export type AppLanguage = "en" | "it" | "es" | "de" | "fr";

export const APP_LANGUAGES: { id: AppLanguage; nativeLabel: string }[] = [
  { id: "en", nativeLabel: "English" },
  { id: "it", nativeLabel: "Italiano" },
  { id: "es", nativeLabel: "Español" },
  { id: "de", nativeLabel: "Deutsch" },
  { id: "fr", nativeLabel: "Français" },
];

export type Dict = { [key: string]: string | Dict };

export type Vars = Record<string, string | number | boolean | null | undefined>;

export function getByPath(dict: Dict, path: string): string | undefined {
  const parts = path.split(".");
  let cur: string | Dict | undefined = dict;
  for (const p of parts) {
    if (cur == null || typeof cur === "string") return undefined;
    cur = cur[p];
  }
  return typeof cur === "string" ? cur : undefined;
}

export function interpolate(template: string, vars?: Vars): string {
  if (!vars) return template;
  return template.replace(/\{\{(\w+)\}\}/g, (_, key: string) => {
    const v = vars[key];
    if (v == null) return "";
    return String(v);
  });
}
