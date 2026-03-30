"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type PropsWithChildren,
} from "react";
import { env } from "@/src/env.mjs";
import {
  DEFAULT_APP_LANGUAGE,
  isAppLanguage,
  LANGUAGE_STORAGE_KEY,
  type AppLanguage,
} from "./constants";
import { messages, type TranslationKey } from "./messages";

type LanguageContextValue = {
  language: AppLanguage;
  setLanguage: (language: AppLanguage) => void;
  toggleLanguage: () => void;
  t: (key: TranslationKey) => string;
};

const LanguageContext = createContext<LanguageContextValue | undefined>(
  undefined,
);

function readStoredLanguage(): AppLanguage | null {
  if (typeof window === "undefined") return null;

  try {
    const stored = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (!stored) return null;
    const parsed = JSON.parse(stored) as unknown;
    return isAppLanguage(parsed) ? parsed : null;
  } catch {
    return null;
  }
}

async function syncLanguageCookie(language: AppLanguage) {
  try {
    await fetch(`${env.NEXT_PUBLIC_BASE_PATH ?? ""}/api/language`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ language }),
    });
  } catch {
    // Best-effort only. UI language remains driven by local state/localStorage.
  }
}

export function LanguageProvider({ children }: PropsWithChildren) {
  const [language, setLanguageState] =
    useState<AppLanguage>(DEFAULT_APP_LANGUAGE);
  const [isHydrated, setIsHydrated] = useState(false);

  useEffect(() => {
    const storedLanguage = readStoredLanguage();
    setLanguageState(storedLanguage ?? DEFAULT_APP_LANGUAGE);
    setIsHydrated(true);
  }, []);

  useEffect(() => {
    if (!isHydrated || typeof window === "undefined") return;

    try {
      localStorage.setItem(LANGUAGE_STORAGE_KEY, JSON.stringify(language));
    } catch {
      // Ignore storage failures and keep runtime state.
    }

    document.documentElement.lang = language;
    void syncLanguageCookie(language);
  }, [isHydrated, language]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleStorage = (event: StorageEvent) => {
      if (event.key !== LANGUAGE_STORAGE_KEY) return;

      try {
        const nextLanguage = event.newValue
          ? (JSON.parse(event.newValue) as unknown)
          : DEFAULT_APP_LANGUAGE;
        if (isAppLanguage(nextLanguage)) {
          setLanguageState(nextLanguage);
        }
      } catch {
        setLanguageState(DEFAULT_APP_LANGUAGE);
      }
    };

    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, []);

  const setLanguage = useCallback((nextLanguage: AppLanguage) => {
    setLanguageState(nextLanguage);
  }, []);

  const toggleLanguage = useCallback(() => {
    setLanguageState((current) => (current === "en" ? "zh-CN" : "en"));
  }, []);

  const t = useCallback(
    (key: TranslationKey) => messages[language][key] ?? messages.en[key],
    [language],
  );

  const value = useMemo(
    () => ({
      language,
      setLanguage,
      toggleLanguage,
      t,
    }),
    [language, setLanguage, t, toggleLanguage],
  );

  return (
    <LanguageContext.Provider value={value}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  const context = useContext(LanguageContext);
  if (!context) {
    throw new Error("useLanguage must be used within a LanguageProvider");
  }
  return context;
}
