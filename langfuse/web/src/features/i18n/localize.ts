import { type AppLanguage } from "./constants";

export function localize(
  language: AppLanguage,
  english: string,
  simplifiedChinese: string,
) {
  return language === "zh-CN" ? simplifiedChinese : english;
}
