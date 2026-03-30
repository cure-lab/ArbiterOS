export const APP_LANGUAGES = ["en", "zh-CN"] as const;

export type AppLanguage = (typeof APP_LANGUAGES)[number];

export const DEFAULT_APP_LANGUAGE: AppLanguage = "en";
export const LANGUAGE_STORAGE_KEY = "langfuse_ui_language";
export const LANGUAGE_COOKIE_BASENAME = "langfuse.ui-language";

export function isAppLanguage(value: unknown): value is AppLanguage {
  return (
    typeof value === "string" &&
    (APP_LANGUAGES as readonly string[]).includes(value)
  );
}
