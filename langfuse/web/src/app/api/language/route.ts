import { NextResponse, type NextRequest } from "next/server";
import { z } from "zod/v4";
import {
  DEFAULT_APP_LANGUAGE,
  isAppLanguage,
  LANGUAGE_COOKIE_BASENAME,
} from "@/src/features/i18n/constants";
import { getCookieName, getCookieOptions } from "@/src/server/utils/cookies";

export const dynamic = "force-dynamic";

const LanguagePayloadSchema = z.object({
  language: z.string(),
});

export async function POST(request: NextRequest) {
  const body = LanguagePayloadSchema.safeParse(await request.json());
  if (!body.success || !isAppLanguage(body.data.language)) {
    return NextResponse.json(
      { message: "Invalid language selection." },
      { status: 400 },
    );
  }

  const response = NextResponse.json({ language: body.data.language });
  response.cookies.set({
    name: getCookieName(LANGUAGE_COOKIE_BASENAME),
    value: body.data.language,
    ...getCookieOptions(),
    httpOnly: false,
    maxAge: 60 * 60 * 24 * 365,
  });

  return response;
}

export async function GET(request: NextRequest) {
  const cookieValue = request.cookies.get(
    getCookieName(LANGUAGE_COOKIE_BASENAME),
  )?.value;
  const language = isAppLanguage(cookieValue)
    ? cookieValue
    : DEFAULT_APP_LANGUAGE;

  return NextResponse.json({ language });
}
