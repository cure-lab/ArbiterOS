/** @jest-environment node */

import {
  getHumanPolicyConfirmationState,
  hasHumanPolicyConfirmationReply,
} from "./policyConfirmation";

describe("policy confirmation helpers", () => {
  it("detects ArbiterOS-style reply payloads from direct and nested content", () => {
    expect(
      hasHumanPolicyConfirmationReply({
        observationInput: '"yes"',
      }),
    ).toBe(true);
    expect(
      hasHumanPolicyConfirmationReply({
        observationInput: "continue with the deletion",
      }),
    ).toBe(true);
    expect(
      hasHumanPolicyConfirmationReply({
        traceInput: '{"raw_content":"{\\"content\\":\\"nope\\"}"}',
      }),
    ).toBe(true);
    expect(
      hasHumanPolicyConfirmationReply({
        traceMetadata: { text_preview: "no" },
      }),
    ).toBe(true);
  });

  it("ignores longer policy-violation prompts that mention yes/no", () => {
    expect(
      hasHumanPolicyConfirmationReply({
        traceInput:
          '{"content":"policy violation detected, do you want to apply the protection? Please reply Yes/No."}',
      }),
    ).toBe(false);
  });

  it("returns human confirmation states for accepted or rejected reply turns", () => {
    expect(
      getHumanPolicyConfirmationState({
        metadata: { policy_confirmation_state: "accepted" },
        traceInput: '{"content":"yes"}',
      }),
    ).toBe("accepted");
    expect(
      getHumanPolicyConfirmationState({
        metadata: { policy_confirmation_state: "accepted" },
        traceInput: '{"content":"continue with the deletion"}',
      }),
    ).toBe("accepted");
    expect(
      getHumanPolicyConfirmationState({
        metadata: { policy_confirmation_state: "rejected" },
        traceInput: '{"content":"nope, keep the original response"}',
      }),
    ).toBe("rejected");
    expect(
      getHumanPolicyConfirmationState({
        metadata: { policy_confirmation_state: "rejected" },
        traceInput: "   ",
      }),
    ).toBeNull();
    expect(
      getHumanPolicyConfirmationState({
        metadata: { policy_confirmation_state: "rejected" },
        traceMetadata: { text_preview: "no" },
        traceInput:
          '{"content":"two reads are consistent; here is the weekly summary"}',
      }),
    ).toBe("rejected");
    expect(
      getHumanPolicyConfirmationState({
        metadata: { policy_confirmation_state: "ask" },
        traceInput: '{"content":"yes"}',
      }),
    ).toBeNull();
  });
});
