/** @jest-environment node */

import { mergeRelevantPolicyMetadata } from "@/src/features/governance/utils/policyMetadata";
import {
  getGovernanceDisplayLevel,
  getInactivateErrorTypeDisplayLabel,
  getRelevantInactivateErrorType,
} from "@/src/features/governance/utils/policyMetadata";

describe("policyMetadata mergeRelevantPolicyMetadata", () => {
  it("merges inactivate_error_type from trace metadata when turn matches", () => {
    const merged = mergeRelevantPolicyMetadata({
      observationMetadata: {
        turn_index: 3,
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.output.turn_3",
      statusMessage: "normal response",
    });

    expect(merged.inactivate_error_type).toBe(
      "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
    );
  });

  it("does not merge inactivate_error_type when turn and text do not match", () => {
    const merged = mergeRelevantPolicyMetadata({
      observationMetadata: {
        turn_index: 1,
      },
      traceMetadata: {
        turn_index: 2,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.output.turn_1",
      statusMessage: "different output",
    });

    expect(merged.inactivate_error_type).toBeUndefined();
  });

  it("treats matching inactivate_error_type as a warning display level via own metadata", () => {
    const level = getGovernanceDisplayLevel({
      level: "DEFAULT",
      observationMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.output.turn_3",
      statusMessage: "normal response",
    });

    expect(level).toBe("WARNING");
  });

  it("treats matching inactivate_error_type as a warning display level via statusMessage fallback", () => {
    const level = getGovernanceDisplayLevel({
      level: "DEFAULT",
      observationMetadata: {
        turn_index: 3,
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.output.turn_3",
      statusMessage:
        "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
    });

    expect(level).toBe("WARNING");
  });

  it("does not expose inactivate_error_type on non session.output nodes", () => {
    const value = getRelevantInactivateErrorType({
      observationMetadata: {
        turn_index: 3,
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.parser.turn_003.structured_output",
      statusMessage: "normal response",
    });

    expect(value).toBeNull();
  });

  it("does not upgrade non session.output nodes to warning", () => {
    const level = getGovernanceDisplayLevel({
      level: "ERROR",
      observationMetadata: {
        turn_index: 3,
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.parser.turn_003.structured_output",
      statusMessage: "normal response",
    });

    expect(level).toBe("ERROR");
  });

  it("extracts inactivate_error_type from observation's own metadata", () => {
    const value = getRelevantInactivateErrorType({
      observationMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.output.turn_3",
      statusMessage: "normal response",
    });

    expect(value).toBe(
      "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
    );
  });

  it("extracts inactivate_error_type from trace metadata when statusMessage matches", () => {
    const value = getRelevantInactivateErrorType({
      observationMetadata: {
        turn_index: 3,
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.output.turn_3",
      statusMessage:
        "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
    });

    expect(value).toBe(
      "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
    );
  });

  it("does not inherit inactivate_error_type from trace when statusMessage does not match (prevents drift)", () => {
    const value = getRelevantInactivateErrorType({
      observationMetadata: {
        turn_index: 3,
      },
      traceMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern",
      },
      observationName: "session.output.turn_3",
      statusMessage: "normal response",
    });

    expect(value).toBeNull();
  });

  it("does not drift inactivate_error_type from turn_003 to turn_004", () => {
    const traceMetadata = {
      turn_index: 4,
      inactivate_error_type:
        "Response length 1600 exceeded max 500 chars. Truncated to 500.",
    };

    const turn003 = getRelevantInactivateErrorType({
      observationMetadata: {
        turn_index: 3,
        inactivate_error_type:
          "Response length 1600 exceeded max 500 chars. Truncated to 500.",
      },
      traceMetadata,
      observationName: "session.output.turn_003",
      statusMessage:
        "Response length 1600 exceeded max 500 chars. Truncated to 500.",
    });
    expect(turn003).toBe(
      "Response length 1600 exceeded max 500 chars. Truncated to 500.",
    );

    const turn004 = getRelevantInactivateErrorType({
      observationMetadata: {},
      traceMetadata,
      observationName: "session.output.turn_004",
      statusMessage: null,
    });
    expect(turn004).toBeNull();
  });

  it("uses a fixed display label for inactivate_error_type", () => {
    expect(
      getInactivateErrorTypeDisplayLabel(
        "DeletePolicy(inactive) hit: tool=exec | detail=delete-like pattern matched but remained non-blocking",
      ),
    ).toBe("Inactive Policy Warning");
  });
});
