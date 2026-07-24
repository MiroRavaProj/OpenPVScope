import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { de } from "./locales/de";
import { en } from "./locales/en";
import { es } from "./locales/es";
import { fr } from "./locales/fr";
import { it } from "./locales/it";
import {
  AppLanguage,
  Dict,
  Vars,
  getByPath,
  interpolate,
} from "./types";

const LOCALES: Record<AppLanguage, Dict> = { en, it, es, de, fr };

type I18nCtx = {
  language: AppLanguage;
  setLanguage: (lang: AppLanguage) => void;
  t: (key: string, vars?: Vars) => string;
};

const Ctx = createContext<I18nCtx | null>(null);

function translate(lang: AppLanguage, key: string, vars?: Vars): string {
  const primary = getByPath(LOCALES[lang], key);
  const fallback = lang === "en" ? undefined : getByPath(LOCALES.en, key);
  const template = primary ?? fallback ?? key;
  return interpolate(template, vars);
}

export function I18nProvider(props: {
  language: AppLanguage;
  onLanguageChange?: (lang: AppLanguage) => void;
  children: ReactNode;
}) {
  const [language, setLanguageState] = useState<AppLanguage>(props.language);

  useEffect(() => {
    setLanguageState(props.language);
  }, [props.language]);

  useEffect(() => {
    document.documentElement.lang = language;
  }, [language]);

  const setLanguage = useCallback(
    (lang: AppLanguage) => {
      setLanguageState(lang);
      props.onLanguageChange?.(lang);
    },
    [props],
  );

  const t = useCallback((key: string, vars?: Vars) => translate(language, key, vars), [language]);

  const value = useMemo(() => ({ language, setLanguage, t }), [language, setLanguage, t]);

  return <Ctx.Provider value={value}>{props.children}</Ctx.Provider>;
}

export function useI18n(): I18nCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}

export function useT() {
  return useI18n().t;
}

export function isAppLanguage(v: unknown): v is AppLanguage {
  return v === "en" || v === "it" || v === "es" || v === "de" || v === "fr";
}

export type { AppLanguage, Vars };
export { APP_LANGUAGES } from "./types";
