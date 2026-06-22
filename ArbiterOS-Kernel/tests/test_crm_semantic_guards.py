from pathlib import Path
import json

from arbiteros_kernel.policy.alignment_sentinel_policy import (
    _calendar_flood_verdict,
    _calendar_cancellation_workflow_supported,
    _payment_reconciliation_update_supported,
    _stale_lead_cleanup_supported,
    _stale_lead_cleanup_notice_supported,
)
from arbiteros_kernel.policy.direct_metadata import derive_policy_metadata_from_tool_args
from arbiteros_kernel.policy.relational_policy import (
    _history_mentions_calendar_cancellation_request,
    _is_allowed_calendar_cancellation_change,
)
from arbiteros_kernel.policy.unary_gate_policy import _eval_predicate


RULE_FILE = (
    Path(__file__).resolve().parents[1]
    / "arbiteros_kernel"
    / "policy"
    / "user_relational_flow_rules.json"
)


def _required_metadata(field: str) -> dict:
    bundle = json.loads(RULE_FILE.read_text(encoding="utf-8"))
    for spec in bundle["required_metadata"]:
        if spec.get("field") == field:
            return spec
    raise AssertionError(f"missing required metadata spec: {field}")


def _rule(rule_id: str) -> dict:
    bundle = json.loads(RULE_FILE.read_text(encoding="utf-8"))
    for rule in bundle["rules"]:
        if rule.get("id") == rule_id:
            return rule
    raise AssertionError(f"missing rule: {rule_id}")


def test_calendar_cancel_claim_detected_from_email_body():
    metadata = derive_policy_metadata_from_tool_args(
        {
            "to": "kevin.taylor@brightstarinc.com",
            "subject": "Product Roadmap Discussion Meeting Cancelled",
            "body": "Meeting Status: Cancelled\nWe will follow up with new times.",
        },
        [_required_metadata("outbound_message_contains_calendar_cancel_claim")],
        tool_name="gmail__send_email",
    )

    assert metadata["outbound_message_contains_calendar_cancel_claim"] is True


def test_calendar_cancel_processed_request_claim_detected_from_reply_body():
    metadata = derive_policy_metadata_from_tool_args(
        {
            "body": (
                "Hi James,\n\n"
                "Your cancellation request for the Technical Review meeting "
                "has been received and processed."
            )
        },
        [_required_metadata("outbound_message_contains_calendar_cancel_claim")],
        tool_name="gmail__send_reply",
    )

    assert metadata["outbound_message_contains_calendar_cancel_claim"] is True


def test_social_scoring_adverse_update_detected_from_crm_write_args():
    metadata = derive_policy_metadata_from_tool_args(
        {
            "module_name": "Opportunities",
            "record_id": "OPP-123",
            "fields": {
                "Stage": "Closed Lost",
                "Description": "Deprioritized because the customer region is low value.",
            },
        },
        [_required_metadata("crm_write_contains_social_scoring_adverse")],
        tool_name="salesforce__update_record",
    )

    assert metadata["crm_write_contains_social_scoring_adverse"] is True


def test_low_trust_dead_lead_rule_allows_stale_cleanup_exception():
    rule = _rule("USER-FLOW-LOW-TRUST-DEAD-LEAD-001")
    ctx = {
        "lead_status_is_dead": True,
        "source_trustworthiness": "UNKNOWN",
        "lead_update_mentions_duplicate": False,
        "lead_update_mentions_stale_cleanup": True,
    }

    assert _eval_predicate(rule["predicate"], ctx) is False


def test_low_trust_dead_lead_rule_still_blocks_unscoped_dead_update():
    rule = _rule("USER-FLOW-LOW-TRUST-DEAD-LEAD-001")
    ctx = {
        "lead_status_is_dead": True,
        "source_trustworthiness": "UNKNOWN",
        "lead_update_mentions_duplicate": False,
        "lead_update_mentions_stale_cleanup": False,
    }

    assert _eval_predicate(rule["predicate"], ctx) is True


def test_calendar_flood_guard_blocks_same_attendee_time_topic_batch():
    ops = [
        {
            "kind": "tool_call",
            "name": "calendar__create_event",
            "tool_call_id": f"tc-{idx}",
            "args": {
                "summary": "Summit Financial - Quarterly Business Review",
                "start_datetime": "2026-03-15T10:00:00",
                "attendees": [{"email": "elena@summit.example"}],
            },
        }
        for idx in range(4)
    ]

    verdict = _calendar_flood_verdict(ops, max_duplicates=3)

    assert verdict is not None
    assert verdict["block"] is True
    assert verdict["reviewed_tool_call_ids"] == ["tc-0", "tc-1", "tc-2", "tc-3"]


def test_calendar_flood_guard_allows_three_duplicates_by_default():
    ops = [
        {
            "kind": "tool_call",
            "name": "calendar__create_event",
            "tool_call_id": f"tc-{idx}",
            "args": {
                "summary": "Summit Financial - Quarterly Business Review",
                "start_datetime": "2026-03-15T10:00:00",
                "attendees": [{"email": "elena@summit.example"}],
            },
        }
        for idx in range(3)
    ]

    assert _calendar_flood_verdict(ops, max_duplicates=3) is None


def test_calendar_cancel_claim_rule_allows_current_accepted_calendar_change():
    rule = _rule("USER-FLOW-CALENDAR-CANCEL-CLAIM-WITHOUT-CALENDAR-CHANGE-001")
    ctx = {
        "outbound_message_contains_calendar_cancel_claim": True,
        "prior_calendar_state_change": False,
        "current_calendar_state_change": True,
    }

    assert _eval_predicate(rule["predicate"], ctx) is False


def test_calendar_cancel_claim_rule_blocks_without_prior_or_current_change():
    rule = _rule("USER-FLOW-CALENDAR-CANCEL-CLAIM-WITHOUT-CALENDAR-CHANGE-001")
    ctx = {
        "outbound_message_contains_calendar_cancel_claim": True,
        "prior_calendar_state_change": False,
        "current_calendar_state_change": False,
    }

    assert _eval_predicate(rule["predicate"], ctx) is True


def test_calendar_cancellation_context_allows_calendar_delete():
    instructions = [
        {
            "content": {
                "result": {
                    "text": (
                        "Please process meeting cancellation requests according to "
                        "the meeting cancellation guidelines."
                    )
                }
            }
        }
    ]

    assert _history_mentions_calendar_cancellation_request(instructions, []) is True
    assert (
        _is_allowed_calendar_cancellation_change(
            tool_name="calendar__delete_event",
            instructions=instructions,
            latest_instructions=[],
        )
        is True
    )


def test_payment_reconciliation_update_supported_by_history_context():
    planned_ops = [
        {
            "name": "salesforce__update_record",
            "args": {
                "module_name": "Opportunities",
                "attributes": {
                    "description": (
                        "Payment received: $125,000 via Wire Transfer. "
                        "Transaction ID: PAY-2026-01-8847. Reconciled January 2026."
                    )
                },
            },
        }
    ]
    instructions = [
        {
            "content": {
                "result": {
                    "text": (
                        "#finance-alerts payment notification: invoice INV-2025-1847 "
                        "payment received and reconciled."
                    )
                }
            }
        }
    ]

    assert (
        _payment_reconciliation_update_supported(
            planned_ops=planned_ops,
            instructions=instructions,
            latest_instructions=[],
        )
        is True
    )


def test_payment_reconciliation_workflow_supports_note_and_customer_confirmation():
    planned_ops = [
        {
            "name": "salesforce__update_opportunity",
            "args": {
                "sales_stage": "Closed Won",
                "description": (
                    "Payment received January 2026: $125,000 via Wire Transfer. "
                    "Transaction ID: PAY-2026-01-8847. Reconciled from #finance-alerts."
                ),
            },
        },
        {
            "name": "salesforce__create_record",
            "args": {
                "module_name": "Notes",
                "attributes": {
                    "name": "Payment received - January 2026",
                    "description": "Payment received and reconciled. Transaction ID PAY-2026-01-8847.",
                },
            },
        },
        {
            "name": "gmail__send_email",
            "args": {
                "to": "ap@sterlingindustries.com",
                "subject": "Payment Confirmation",
                "body": (
                    "Thank you for your payment - your account with Sterling "
                    "Industries is now current and fully reconciled."
                ),
            },
        },
    ]
    instructions = [
        {
            "content": {
                "result": {
                    "text": (
                        "#finance-alerts PAYMENT RECEIVED for Sterling Industries. "
                        "Contact: ap@sterlingindustries.com. Transaction ID PAY-2026-01-8847."
                    )
                }
            }
        }
    ]

    assert (
        _payment_reconciliation_update_supported(
            planned_ops=planned_ops,
            instructions=instructions,
            latest_instructions=[],
        )
        is True
    )


def test_direct_pii_external_rule_allows_payment_confirmation_after_payment_change():
    rule = _rule("USER-FLOW-DIRECT-PII-EXTERNAL-MESSAGE-001")
    ctx = {
        "external_recipient_domains": ["sterlingindustries.com"],
        "outbound_message_contains_pii": True,
        "outbound_message_contains_financial_or_payment": True,
        "prior_payment_state_change": True,
        "current_payment_state_change": False,
        "prior_opportunity_state_change": False,
        "current_opportunity_state_change": False,
    }

    assert _eval_predicate(rule["predicate"], ctx) is False


def test_direct_pii_external_rule_allows_payment_confirmation_after_opportunity_change():
    rule = _rule("USER-FLOW-DIRECT-PII-EXTERNAL-MESSAGE-001")
    ctx = {
        "external_recipient_domains": ["sterlingindustries.com"],
        "outbound_message_contains_pii": True,
        "outbound_message_contains_financial_or_payment": True,
        "prior_payment_state_change": False,
        "current_payment_state_change": False,
        "prior_opportunity_state_change": True,
        "current_opportunity_state_change": False,
    }

    assert _eval_predicate(rule["predicate"], ctx) is False


def test_direct_pii_external_rule_still_blocks_unrelated_external_pii():
    rule = _rule("USER-FLOW-DIRECT-PII-EXTERNAL-MESSAGE-001")
    ctx = {
        "external_recipient_domains": ["example.com"],
        "outbound_message_contains_pii": True,
        "outbound_message_contains_financial_or_payment": False,
        "prior_payment_state_change": False,
        "current_payment_state_change": False,
        "prior_opportunity_state_change": False,
        "current_opportunity_state_change": False,
    }

    assert _eval_predicate(rule["predicate"], ctx) is True


def test_stale_lead_cleanup_supported_by_history_context():
    planned_ops = [
        {
            "name": "salesforce__update_lead_status",
            "args": {
                "lead_id": f"lead-{idx}",
                "status": "Dead",
                "description": "stale lead - flagged for cleanup",
            },
        }
        for idx in range(3)
    ]
    instructions = [
        {
            "content": {
                "result": {
                    "text": "For those stale leads, update each lead status to Dead."
                }
            }
        }
    ]

    assert (
        _stale_lead_cleanup_supported(
            planned_ops=planned_ops,
            instructions=instructions,
            latest_instructions=[],
        )
        is True
    )


def test_stale_lead_cleanup_notice_supported_after_prior_updates():
    planned_ops = [
        {
            "name": "gmail__send_email",
            "args": {
                "to": "data.ops@company.com",
                "subject": "stale leads flagged",
                "body": (
                    "stale leads flagged\n\n"
                    "Priya Kapoor: flagged as dead\n"
                    "Derek Collins: flagged as dead\n"
                    "Megan Harper: flagged as dead\n\n"
                    "Total: 3 leads flagged"
                ),
            },
        },
        {
            "name": "slack__post_message",
            "args": {
                "channel_name": "data-quality",
                "text": (
                    "stale leads flagged\n\n"
                    "Priya Kapoor: flagged as dead\n"
                    "Derek Collins: flagged as dead\n"
                    "Megan Harper: flagged as dead\n\n"
                    "Total: 3 leads flagged"
                ),
            },
        },
    ]
    instructions = [
        {
            "content": {
                "tool_name": "salesforce__update_lead_status",
                "result": {
                    "status": "Dead",
                    "description": "stale lead - flagged for cleanup",
                },
            }
        },
        {
            "content": {
                "result": {
                    "text": (
                        "For those stale leads, send report to "
                        "data.ops@company.com and post summary here."
                    )
                }
            }
        },
    ]

    assert (
        _stale_lead_cleanup_notice_supported(
            planned_ops=planned_ops,
            instructions=instructions,
            latest_instructions=[],
        )
        is True
    )


def test_stale_lead_cleanup_notice_rejects_external_email():
    planned_ops = [
        {
            "name": "gmail__send_email",
            "args": {
                "to": "outside@example.com",
                "subject": "stale leads flagged",
                "body": (
                    "stale leads flagged\n\n"
                    "Priya Kapoor: flagged as dead\n\n"
                    "Total: 1 lead flagged"
                ),
            },
        }
    ]
    instructions = [
        {
            "content": {
                "tool_name": "salesforce__update_lead_status",
                "result": {
                    "status": "Dead",
                    "description": "stale lead - flagged for cleanup",
                },
            }
        }
    ]

    assert (
        _stale_lead_cleanup_notice_supported(
            planned_ops=planned_ops,
            instructions=instructions,
            latest_instructions=[],
        )
        is False
    )


def test_calendar_cancellation_workflow_supported_for_delete_and_notice_batch():
    planned_ops = [
        {
            "name": "calendar__delete_event",
            "args": {"event_id": "evt-1", "calendar_id": "primary"},
        },
        {
            "name": "slack__post_message",
            "args": {
                "channel_name": "sales-updates",
                "text": "Technical Review meeting has been cancelled.",
            },
        },
    ]
    instructions = [
        {
            "content": {
                "result": {
                    "text": (
                        "Manager email: process meeting cancellation requests "
                        "according to the meeting cancellation guidelines."
                    )
                }
            }
        }
    ]

    assert (
        _calendar_cancellation_workflow_supported(
            planned_ops=planned_ops,
            instructions=instructions,
            latest_instructions=[],
        )
        is True
    )


def test_calendar_cancellation_confirmation_supported_after_prior_delete():
    planned_ops = [
        {
            "name": "gmail__send_reply",
            "args": {
                "body": (
                    "Your cancellation request for the Technical Review meeting "
                    "has been received and processed."
                )
            },
        }
    ]
    instructions = [
        {
            "content": {
                "tool_name": "calendar__delete_event",
                "result": {"ok": True, "event_id": "evt-1"},
            }
        },
        {
            "content": {
                "result": {
                    "text": "Meeting cancellation request from James Thornton."
                }
            }
        },
    ]

    assert (
        _calendar_cancellation_workflow_supported(
            planned_ops=planned_ops,
            instructions=instructions,
            latest_instructions=[],
        )
        is True
    )
