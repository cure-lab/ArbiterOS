import {
  ChatMessageRole,
  ChatMessageType,
  type ChatMessage,
} from "@langfuse/shared";
import { type NextRequest } from "next/server";
import { getCookieName } from "@/src/server/utils/cookies";
import {
  DEFAULT_APP_LANGUAGE,
  isAppLanguage,
  LANGUAGE_COOKIE_BASENAME,
  type AppLanguage,
} from "./constants";

type LanguageInstructionMode = "prose" | "structured";

function getCookieValue(
  cookieHeader: string | string[] | undefined,
  cookieName: string,
) {
  if (!cookieHeader) return undefined;

  const cookieString = Array.isArray(cookieHeader)
    ? cookieHeader.join(";")
    : cookieHeader;

  return cookieString
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(`${cookieName}=`))
    ?.slice(cookieName.length + 1);
}

export function getLanguageFromCookieHeader(
  cookieHeader: string | string[] | undefined,
): AppLanguage {
  const rawLanguage = getCookieValue(
    cookieHeader,
    getCookieName(LANGUAGE_COOKIE_BASENAME),
  );

  return isAppLanguage(rawLanguage) ? rawLanguage : DEFAULT_APP_LANGUAGE;
}

export function getLanguageFromRequest(request: NextRequest): AppLanguage {
  const rawLanguage = request.cookies.get(
    getCookieName(LANGUAGE_COOKIE_BASENAME),
  )?.value;

  return isAppLanguage(rawLanguage) ? rawLanguage : DEFAULT_APP_LANGUAGE;
}

export function getLanguageInstruction(params: {
  language: AppLanguage;
  mode: LanguageInstructionMode;
}) {
  const { language, mode } = params;

  if (mode === "structured") {
    if (language === "en") {
      return "If your output includes free-form text, write that text in English except technical terminology and exact original literals. Keep JSON keys, enum values, schema-required field names, code snippets, and other exact literals unchanged.";
    }

    return "If your output includes free-form text, write that text in Simplified Chinese except technical terminology and exact original literals. Keep JSON keys, enum values, schema-required field names, code snippets, and other exact literals unchanged.";
  }

  if (language === "en") {
    return "Please respond in English except technical terminology and exact original literals.";
  }

  return "Please respond in Simplified Chinese except technical terminology and exact original literals.";
}

export function applyLanguageInstructionToMessages(params: {
  messages: ChatMessage[];
  language: AppLanguage;
  mode: LanguageInstructionMode;
}): ChatMessage[] {
  const instruction = getLanguageInstruction({
    language: params.language,
    mode: params.mode,
  });

  if (!instruction) return params.messages;

  const firstWritableIndex = params.messages.findIndex(
    (message) =>
      message.role === ChatMessageRole.System ||
      message.role === ChatMessageRole.Developer,
  );

  if (firstWritableIndex >= 0) {
    const existingMessage = params.messages[firstWritableIndex];

    if (typeof existingMessage?.content === "string") {
      return params.messages.map((message, index) => {
        if (index !== firstWritableIndex) return message;

        return {
          ...message,
          content: `${message.content}\n\n${instruction}`,
        } as ChatMessage;
      });
    }
  }

  const languageMessage: ChatMessage = {
    type: ChatMessageType.System,
    role: ChatMessageRole.System,
    content: instruction,
  };

  return [languageMessage, ...params.messages];
}
