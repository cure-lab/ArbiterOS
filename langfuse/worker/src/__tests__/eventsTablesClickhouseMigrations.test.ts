import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

const migrationRoot = resolve(
  __dirname,
  "../../../packages/shared/clickhouse/migrations",
);

function readMigration(relativePath: string): string {
  return readFileSync(resolve(migrationRoot, relativePath), "utf8");
}

function assertCreationOrder(sql: string) {
  const createEvents = sql.indexOf("CREATE TABLE IF NOT EXISTS events");
  const createEventsFull = sql.indexOf(
    "CREATE TABLE IF NOT EXISTS events_full",
  );
  const createEventsCore = sql.indexOf(
    "CREATE TABLE IF NOT EXISTS events_core",
  );
  const createEventsCoreMv = sql.indexOf(
    "CREATE MATERIALIZED VIEW IF NOT EXISTS events_core_mv",
  );
  const createEventsFullMv = sql.indexOf(
    "CREATE MATERIALIZED VIEW IF NOT EXISTS events_full_mv",
  );

  expect(createEvents).toBeGreaterThanOrEqual(0);
  expect(createEventsFull).toBeGreaterThan(createEvents);
  expect(createEventsCore).toBeGreaterThan(createEventsFull);
  expect(createEventsCoreMv).toBeGreaterThan(createEventsCore);
  expect(createEventsFullMv).toBeGreaterThan(createEventsCoreMv);
}

describe("events table clickhouse migrations", () => {
  it("keeps required creation order in unclustered migration", () => {
    const sql = readMigration("unclustered/0035_add_events_core_tables.up.sql");
    assertCreationOrder(sql);
    expect(sql).toContain("FROM events_full");
    expect(sql).toContain("FROM events");
  });

  it("keeps required creation order in clustered migration", () => {
    const sql = readMigration("clustered/0035_add_events_core_tables.up.sql");
    assertCreationOrder(sql);
    expect(sql).toContain("ON CLUSTER default");
    expect(sql).toContain("ReplicatedReplacingMergeTree");
  });
});
