import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BackgroundMigration } from "@prisma/client";

const mocks = vi.hoisted(() => ({
  findUniqueMock: vi.fn(),
  updateMock: vi.fn(),
  queryClickhouseMock: vi.fn(),
  commandClickhouseMock: vi.fn(),
}));

vi.mock("@langfuse/shared/src/db", () => ({
  prisma: {
    backgroundMigration: {
      findUnique: mocks.findUniqueMock,
      update: mocks.updateMock,
    },
  },
}));

vi.mock("@langfuse/shared/src/server", () => ({
  queryClickhouse: mocks.queryClickhouseMock,
  commandClickhouse: mocks.commandClickhouseMock,
  logger: {
    info: vi.fn(),
    warn: vi.fn(),
    error: vi.fn(),
    debug: vi.fn(),
  },
}));

import BackfillEventsFullFromEvents from "../backgroundMigrations/backfillEventsFullFromEvents";

describe("BackfillEventsFullFromEvents", () => {
  let storedState: Record<string, unknown> = {};

  beforeEach(() => {
    vi.clearAllMocks();
    storedState = {};

    mocks.findUniqueMock.mockResolvedValue({
      state: storedState,
    } satisfies Pick<BackgroundMigration, "state">);

    mocks.updateMock.mockImplementation(async ({ data }: any) => {
      storedState = data.state;
      return {
        id: "3e6be5ee-8f93-4bf4-8eb5-cab5bd01395c",
        state: storedState,
      };
    });

    mocks.queryClickhouseMock.mockImplementation(
      async ({ query }: { query: string }) => {
        if (query.includes("FROM system.tables")) {
          return [
            { name: "events" },
            { name: "events_full" },
            { name: "events_core" },
            { name: "events_full_mv" },
            { name: "events_core_mv" },
          ];
        }

        if (query.includes("SELECT DISTINCT toString(toYYYYMM(start_time))")) {
          return [{ partition: "202601" }, { partition: "202512" }];
        }

        return [];
      },
    );

    mocks.commandClickhouseMock.mockResolvedValue(undefined);
  });

  it("validates prerequisites and backfills all partitions", async () => {
    const migration = new BackfillEventsFullFromEvents();

    const validation = await migration.validate({});
    expect(validation).toEqual({ valid: true, invalidReason: undefined });

    await migration.run({});

    expect(mocks.commandClickhouseMock).toHaveBeenCalledTimes(2);
    expect(mocks.commandClickhouseMock.mock.calls[0][0].params.partition).toBe(
      202601,
    );
    expect(mocks.commandClickhouseMock.mock.calls[1][0].params.partition).toBe(
      202512,
    );

    const firstQuery: string =
      mocks.commandClickhouseMock.mock.calls[0][0].query;
    expect(firstQuery).toContain("metadata_raw_values as metadata_values");
    expect(firstQuery).toContain("FROM events");
    expect(firstQuery).toContain(
      "WHERE toYYYYMM(start_time) = {partition: UInt32}",
    );

    expect(storedState.phase).toBe("completed");
    expect(storedState.totalProcessed).toBe(2);
    expect(
      (storedState.partitions as Array<{ status: string }>).every(
        (p) => p.status === "completed",
      ),
    ).toBe(true);
  });

  it("fails validation when required events tables are missing", async () => {
    mocks.queryClickhouseMock.mockResolvedValueOnce([{ name: "events" }]);
    const migration = new BackfillEventsFullFromEvents();

    const validation = await migration.validate({});

    expect(validation.valid).toBe(false);
    expect(validation.invalidReason).toContain("Missing required ClickHouse");
  });
});
