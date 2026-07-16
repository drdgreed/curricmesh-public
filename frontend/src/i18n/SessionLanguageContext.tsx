/**
 * SessionLanguageContext (T3b) — the learner's tutor reply-language for THIS
 * session.
 *
 * The learner picks a language at learn-session start; the tutor answers /
 * coaches / grades feedback in it. Scope is the TUTOR's replies only — UI chrome
 * and course content are NOT localized (deferred, out of T3 scope).
 *
 * The choice is held in **client/session state**: React state seeded from (and
 * written through to) ``sessionStorage`` so it survives in-session navigation and
 * reloads but is gone when the tab session ends. There is NO backend call and NO
 * persisted DB preference — it rides each tutor request as a ``language`` param.
 *
 * The value is what the backend puts into "Respond in {language}.": the English
 * default is the token ``"en"`` (so a default request is byte-identical to
 * pre-T3b), every other option is a plain language name (e.g. "Spanish").
 *
 * The context ships a SAFE DEFAULT (``"en"``, no-op setter) so components that
 * read it without a provider (e.g. some unit tests) still work — absent a
 * provider, the session language is simply English.
 */

import {
  createContext,
  useCallback,
  useContext,
  useState,
  type ReactNode,
} from "react";

/** The languages a learner can pick for the tutor. ``value`` is sent verbatim as
 * the ``language`` param; English is the ``"en"`` default token. */
export const TUTOR_LANGUAGES: ReadonlyArray<{ value: string; label: string }> = [
  { value: "en", label: "English" },
  { value: "Spanish", label: "Español" },
  { value: "French", label: "Français" },
  { value: "German", label: "Deutsch" },
  { value: "Portuguese", label: "Português" },
  { value: "Italian", label: "Italiano" },
  { value: "Hindi", label: "हिन्दी" },
  { value: "Chinese", label: "中文" },
  { value: "Japanese", label: "日本語" },
  { value: "Arabic", label: "العربية" },
];

export const DEFAULT_LANGUAGE = "en";
const STORAGE_KEY = "tutor_session_language";

interface SessionLanguageValue {
  /** The active session language (``"en"`` or a plain language name). */
  language: string;
  setLanguage: (language: string) => void;
}

const SessionLanguageContext = createContext<SessionLanguageValue>({
  language: DEFAULT_LANGUAGE,
  setLanguage: () => {},
});

function readInitial(): string {
  try {
    return sessionStorage.getItem(STORAGE_KEY) || DEFAULT_LANGUAGE;
  } catch {
    return DEFAULT_LANGUAGE;
  }
}

export function SessionLanguageProvider({ children }: { children: ReactNode }) {
  const [language, setLanguageState] = useState<string>(readInitial);

  const setLanguage = useCallback((next: string) => {
    setLanguageState(next);
    try {
      sessionStorage.setItem(STORAGE_KEY, next);
    } catch {
      // sessionStorage unavailable (private mode / SSR) — session state still works.
    }
  }, []);

  return (
    <SessionLanguageContext.Provider value={{ language, setLanguage }}>
      {children}
    </SessionLanguageContext.Provider>
  );
}

export function useSessionLanguage(): SessionLanguageValue {
  return useContext(SessionLanguageContext);
}
