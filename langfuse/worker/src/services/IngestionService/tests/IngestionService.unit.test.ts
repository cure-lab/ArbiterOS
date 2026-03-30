import { expect, describe, it, vi } from "vitest";
import { IngestionService } from "../../IngestionService";
import { convertDateToClickhouseDateTime } from "@langfuse/shared/src/server";

describe("IngestionService unit tests", () => {
  it("correctly sorts events in ascending order by timestamp", async () => {
    const firstTrace = { timestamp: 1, type: "observation-create" };
    const secondTrace = { timestamp: 1, type: "observation-update" };
    const thirdTrace = { timestamp: 3, type: "observation-update" };

    const records = [thirdTrace, secondTrace, firstTrace];

    const sortedEventList = (IngestionService as any).toTimeSortedEventList(
      records,
    );

    expect(sortedEventList).toEqual([firstTrace, secondTrace, thirdTrace]);
    expect(sortedEventList).not.toBe(records); // Ensure that the original array is not mutated
  });

  it("correctly convert Date to Clickhouse DateTime", async () => {
    const date = new Date("2024-10-12T12:13:14.123Z");

    const clickhouseDateTime = convertDateToClickhouseDateTime(date);

    expect(clickhouseDateTime).toEqual("2024-10-12 12:13:14.123");
  });

  it("does not treat search result payloads as error status messages", () => {
    const searchResultsStatusMessage =
      "Results for: site:reuters.com United States today politics economy technology Feb 2026";

    const isError = (IngestionService as any).isLikelyErrorStatusMessage(
      searchResultsStatusMessage,
    );

    expect(isError).toBe(false);
  });

  it("treats explicit failure messages as error status messages", () => {
    const failureStatusMessage =
      "Error calling LLM: Rate limit exceeded (HTTP 429)";

    const isError = (IngestionService as any).isLikelyErrorStatusMessage(
      failureStatusMessage,
    );

    expect(isError).toBe(true);
  });
});
