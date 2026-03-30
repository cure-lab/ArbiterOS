import { env } from "@/src/env.mjs";
import { logger } from "@langfuse/shared/src/server";
import * as crypto from "node:crypto";

let hasLoggedMissingPlainAuthSecret = false;

export const createSupportEmailHash = (email: string): string | undefined => {
  if (!env.PLAIN_AUTHENTICATION_SECRET) {
    // Only relevant if Plain support chat is configured.
    // Avoid spamming logs: this function can be called frequently (session fetches).
    if (!hasLoggedMissingPlainAuthSecret && env.NEXT_PUBLIC_PLAIN_APP_ID) {
      logger.error("PLAIN_AUTHENTICATION_SECRET is not set");
      hasLoggedMissingPlainAuthSecret = true;
    }
    return undefined;
  }
  const hmac = crypto.createHmac("sha256", env.PLAIN_AUTHENTICATION_SECRET);
  hmac.update(email);
  const hash = hmac.digest("hex");
  return hash;
};
