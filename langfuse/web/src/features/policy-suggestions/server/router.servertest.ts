/** @jest-environment node */

import { type Observation } from "@langfuse/shared";
import {
  buildSampledTurnContext,
  getRejectedTurnDetails,
  parseStringArray,
  parseStringRecord,
} from "./router";
import { executeQuery } from "../../query/server/queryExecutor";

jest.mock("../../query/server/queryExecutor", () => ({
  executeQuery: jest.fn(),
}));

const mockedExecuteQuery = jest.mocked(executeQuery);

describe("policy suggestion router helpers", () => {
  beforeEach(() => {
    mockedExecuteQuery.mockReset();
  });

  it("parses policy arrays and records from mixed encodings", () => {
    expect(parseStringArray('["PolicyA","PolicyB"]')).toEqual([
      "PolicyA",
      "PolicyB",
    ]);
    expect(parseStringArray("PolicyA, PolicyB")).toEqual([
      "PolicyA",
      "PolicyB",
    ]);
    expect(parseStringRecord('{"PolicyA":"desc-a"}')).toEqual({
      PolicyA: "desc-a",
    });
  });

  it("queries only human rejected confirmations for policy suggestions", async () => {
    mockedExecuteQuery.mockResolvedValue([
      {
        policyName: "PathBudgetPolicy",
        traceId: "trace-1",
        traceName: "trace-name",
        policyConfirmationTurnIndex: "7",
        count_count: 2,
      },
    ]);

    await expect(
      getRejectedTurnDetails({
        projectId: "project-1",
        policyName: "PathBudgetPolicy",
        globalFilterState: [],
        fromTimestamp: new Date("2025-01-01T00:00:00.000Z"),
        toTimestamp: new Date("2025-01-02T00:00:00.000Z"),
        version: "v2",
      }),
    ).resolves.toEqual([
      {
        traceId: "trace-1",
        traceName: "trace-name",
        turnIndex: 7,
        nodeCount: 2,
      },
    ]);

    expect(mockedExecuteQuery).toHaveBeenCalledWith(
      "project-1",
      expect.objectContaining({
        filters: expect.arrayContaining([
          expect.objectContaining({
            column: "humanPolicyConfirmationState",
            operator: "=",
            value: "rejected",
            type: "string",
          }),
        ]),
      }),
      "v2",
      false,
    );
  });

  it("builds sampled turn context from policy-violation turns before the confirmation turn", () => {
    const trace = {
      timestamp: new Date("2025-01-01T00:00:00.000Z"),
      input: "no",
    };

    const policyViolationObservation = {
      id: "obs-policy",
      name: "session.parser.turn_009.pre_read_file.1",
      startTime: new Date("2025-01-01T00:00:01.000Z"),
      level: "POLICY_VIOLATION",
      statusMessage:
        "POLICY_BLOCK tool=read_file reason=path not in allow_prefixes",
      input: null,
      output: null,
      metadata: {
        turn_index: "9",
        policy_names: '["HighRiskAction"]',
        policy_descriptions: '{"HighRiskAction":"Disallow unsafe actions"}',
        policy_sources: '{"HighRiskAction":"policy.md#high-risk-action"}',
        policy_protected:
          "POLICY_BLOCK tool=read_file reason=path not in allow_prefixes",
      },
    } as unknown as Observation;

    const toolCallObservation = {
      id: "obs-tool",
      name: "read_file - kernel.execution_core__tool_call @turn_009",
      startTime: new Date("2025-01-01T00:00:01.100Z"),
      level: "DEFAULT",
      statusMessage: null,
      input: '{"path":"report/week3.md"}',
      output: null,
      metadata: {
        turn_index: "9",
      },
    } as unknown as Observation;

    const confirmationTurnObservation = {
      id: "obs-confirmation",
      name: "session.output.turn_010",
      startTime: new Date("2025-01-01T00:00:02.000Z"),
      level: "ERROR",
      statusMessage: "Error: File not found: report/week3.md",
      input: "no",
      output: null,
      metadata: {
        turn_index: "10",
        policy_confirmation_state: "rejected",
      },
    } as unknown as Observation;

    const result = buildSampledTurnContext({
      policyName: "HighRiskAction",
      detail: {
        traceId: "trace-1",
        traceName: "trace-name",
        turnIndex: 10,
        nodeCount: 3,
      },
      trace,
      observations: [
        confirmationTurnObservation,
        policyViolationObservation,
        toolCallObservation,
      ],
    });

    expect(result).not.toBeNull();
    expect(result?.turnIndex).toBe(10);
    expect(result?.policyTurnIndices).toEqual([9]);
    expect(result?.policyDescription).toBe("Disallow unsafe actions");
    expect(result?.policySource).toBe("policy.md#high-risk-action");
    expect(result?.policyProtected).toContain("allow_prefixes");
    expect(result?.relatedTurns).toHaveLength(1);
    expect(result?.relatedTurns[0]?.turnIndex).toBe(9);
    expect(result?.nodes.length).toBe(2);
    expect(result?.examplePrompt).toContain("report/week3.md");
    expect(result?.examplePrompt).not.toBe("no");
  });

  it("excludes accepted confirmation turns from policy-violation context", () => {
    const trace = {
      timestamp: new Date("2025-01-01T00:00:00.000Z"),
      input: '"no"',
    };

    const acceptedTurnPolicyObservation = {
      id: "obs-accepted-policy",
      name: "session.parser.turn_003.pre_exec.1",
      startTime: new Date("2025-01-01T00:00:01.000Z"),
      level: "DEFAULT",
      statusMessage: "POLICY_BLOCK tool=exec reason=needs approval",
      input: null,
      output: null,
      metadata: {
        turn_index: "3",
        policy_protected: "POLICY_BLOCK tool=exec reason=needs approval",
      },
    } as unknown as Observation;

    const acceptedTurnConfirmationObservation = {
      id: "obs-accepted-confirmation",
      name: "session.output.turn_003",
      startTime: new Date("2025-01-01T00:00:01.100Z"),
      level: "DEFAULT",
      statusMessage: null,
      input: '"yes"',
      output: null,
      metadata: {
        turn_index: "3",
        policy_confirmation_state: "accepted",
        policy_names: '["UnaryGatePolicy"]',
      },
    } as unknown as Observation;

    const rejectedTurnPolicyObservation = {
      id: "obs-rejected-policy",
      name: "session.parser.turn_004.pre_read_file.1",
      startTime: new Date("2025-01-01T00:00:02.000Z"),
      level: "POLICY_VIOLATION",
      statusMessage:
        "POLICY_BLOCK tool=read_file reason=path not in allow_prefixes",
      input: null,
      output: null,
      metadata: {
        turn_index: "4",
        policy_names: '["UnaryGatePolicy"]',
        policy_descriptions: '{"UnaryGatePolicy":"Blocks unsafe reads"}',
        policy_sources: '{"UnaryGatePolicy":"policy/unary_gate.py#allow-deny"}',
        policy_protected:
          "POLICY_BLOCK tool=read_file reason=path not in allow_prefixes",
      },
    } as unknown as Observation;

    const rejectedConfirmationObservation = {
      id: "obs-rejected-confirmation",
      name: "session.output.turn_005",
      startTime: new Date("2025-01-01T00:00:03.000Z"),
      level: "DEFAULT",
      statusMessage: null,
      input: '"no"',
      output: null,
      metadata: {
        turn_index: "5",
        policy_confirmation_state: "rejected",
        policy_names: '["UnaryGatePolicy"]',
      },
    } as unknown as Observation;

    const result = buildSampledTurnContext({
      policyName: "UnaryGatePolicy",
      detail: {
        traceId: "trace-accepted-then-rejected",
        traceName: "trace-name",
        turnIndex: 5,
        nodeCount: 4,
      },
      trace,
      observations: [
        acceptedTurnPolicyObservation,
        acceptedTurnConfirmationObservation,
        rejectedTurnPolicyObservation,
        rejectedConfirmationObservation,
      ],
    });

    expect(result).not.toBeNull();
    expect(result?.policyTurnIndices).toEqual([4]);
    expect(result?.relatedTurns).toHaveLength(1);
    expect(result?.relatedTurns[0]?.turnIndex).toBe(4);
    expect(result?.nodes.map((node) => node.id)).not.toContain(
      "obs-accepted-policy",
    );
  });

  it("falls back to trace policy metadata for output confirmations with empty observation metadata", () => {
    const trace = {
      timestamp: new Date("2025-01-01T00:00:00.000Z"),
      input: "yes",
      metadata: {
        turn_index: "6",
        raw_output_content:
          "policy violation POLICY_BLOCK tool=read_file reason=path not in allow_prefixes",
        policy_names: '["PathBudgetPolicy"]',
        policy_descriptions:
          '{"PathBudgetPolicy":"Blocks reads outside the configured allow prefixes"}',
        policy_sources:
          '{"PathBudgetPolicy":"policy/path_budget_policy.py#enforce"}',
        policy_protected:
          "POLICY_BLOCK tool=read_file reason=path not in allow_prefixes",
      },
    };

    const outputObservation = {
      id: "obs-output",
      name: "session.output.turn_006",
      startTime: new Date("2025-01-01T00:00:01.000Z"),
      level: "POLICY_VIOLATION",
      statusMessage:
        "policy violation POLICY_BLOCK tool=read_file reason=path not in allow_prefixes",
      input: null,
      output: null,
      metadata: {},
    } as unknown as Observation;

    const confirmationObservation = {
      id: "obs-confirmation",
      name: "session.output.turn_007",
      startTime: new Date("2025-01-01T00:00:02.000Z"),
      level: "DEFAULT",
      statusMessage: null,
      input: "yes",
      output: null,
      metadata: {
        turn_index: "7",
        policy_confirmation_state: "rejected",
      },
    } as unknown as Observation;

    const result = buildSampledTurnContext({
      policyName: "PathBudgetPolicy",
      detail: {
        traceId: "trace-2",
        traceName: "trace-name",
        turnIndex: 7,
        nodeCount: 2,
      },
      trace,
      observations: [outputObservation, confirmationObservation],
    });

    expect(result).not.toBeNull();
    expect(result?.policyTurnIndices).toEqual([6]);
    expect(result?.policyDescription).toBe(
      "Blocks reads outside the configured allow prefixes",
    );
    expect(result?.policySource).toBe("policy/path_budget_policy.py#enforce");
    expect(result?.policyProtected).toContain("allow_prefixes");
  });
});
