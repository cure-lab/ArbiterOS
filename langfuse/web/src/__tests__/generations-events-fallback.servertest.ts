import { shouldFallbackToLegacyObservationsTable } from "@/src/server/api/routers/generations/db/getAllGenerationsSqlQuery";

describe("shouldFallbackToLegacyObservationsTable", () => {
  it.each([
    "Unknown table expression identifier 'events_core' in scope SELECT count(*) FROM events_core",
    "DB::Exception: Table default.events_full doesn't exist",
    "UNKNOWN_TABLE: missing events_core backing table",
  ])("matches missing events table errors: %s", (message) => {
    expect(shouldFallbackToLegacyObservationsTable(new Error(message))).toBe(
      true,
    );
  });

  it.each([
    "memory limit exceeded while reading from clickhouse",
    "Unknown table expression identifier 'scores' in scope SELECT count(*) FROM scores",
    "Internal Server Error",
  ])("ignores unrelated errors: %s", (message) => {
    expect(shouldFallbackToLegacyObservationsTable(new Error(message))).toBe(
      false,
    );
  });
});
