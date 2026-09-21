"""The tolerance exclusion is the programme's hardest constraint - test it hardest."""

import pytest

# Tolerance *configuration* - always excluded.
EXCLUDED = [
    "The delivery tolerance is checked before the goods receipt is posted.",
    "Set the tolerance key for the invoice check.",
    "Over-delivery is permitted for this item.",
    "Die Toleranz wird im Wareneingang geprueft.",
    "Unlimited over delivery is switched on for spare parts.",
    "A quantity tolerance applies to the returned line.",
    "The under-delivery tolerance is maintained per plant.",
    "An over-receipt is allowed up to the configured limit.",
]

KEPT = [
    "The dealer raises a return request in the dealer front-end.",
    "The inspector records a structured disposition code.",
    "A credit memo settles the confirmed return.",
    "Goods receipt is posted against the returns delivery.",
]

# The business events themselves are analysable processes, not tolerance
# configuration. Excluding these made it impossible to study a process the
# organisation actually runs, such as "Underdelivery".
KEPT_BUSINESS_EVENTS = [
    "Underdelivery of the returned quantity is handled manually.",
    "The dealer reports an underdelivery and raises a discrepancy.",
    "An over receipt must be approved by the supervisor.",
    "Under-delivery is recorded against the inbound delivery and investigated.",
    "The warehouse confirms an over-delivery to the claims team.",
]


@pytest.mark.parametrize("text", EXCLUDED)
def test_excluded_text_is_detected(tolerance, text):
    assert tolerance.matches(text), f"should have been flagged: {text}"
    assert tolerance.scrub(text).text == ""


@pytest.mark.parametrize("text", KEPT)
def test_legitimate_text_survives(tolerance, text):
    assert tolerance.is_clean(text)
    assert tolerance.scrub(text).text == text


@pytest.mark.parametrize("text", KEPT_BUSINESS_EVENTS)
def test_business_events_are_not_tolerance_configuration(tolerance, text):
    """'Underdelivery' is a process; an under-delivery tolerance is a setting."""
    assert tolerance.is_clean(text), f"wrongly excluded a business event: {text}"


def test_context_does_not_leak_between_sentences(tolerance):
    """A 'limit' in one sentence must not make another sentence tolerance talk."""
    text = (
        "The dealer reports an underdelivery on the parts order. "
        "The roadmap limits phase one to the core flow."
    )
    result = tolerance.scrub(text)
    assert "underdelivery" in result.text.lower()
    assert not result.removed


def test_only_the_offending_sentence_is_removed(tolerance):
    text = (
        "The dealer ships the part. The delivery tolerance is checked at receipt. "
        "The inspector then grades it."
    )
    result = tolerance.scrub(text)
    assert "dealer ships the part" in result.text
    assert "inspector then grades it" in result.text
    assert "tolerance" not in result.text.lower()
    assert len(result.removed) == 1


def test_bullet_lists_keep_their_shape(tolerance):
    text = "- Receive the part\n- Check the delivery tolerance\n- Inspect the part"
    result = tolerance.scrub(text)
    assert "Receive the part" in result.text
    assert "Inspect the part" in result.text
    assert "tolerance" not in result.text.lower()


def test_nested_structures_are_scrubbed(tolerance, report):
    payload = {
        "summary": "Returns are received at the DC.",
        "steps": [
            {"name": "Receive", "description": "Post the goods receipt."},
            {"name": "Check", "description": "Verify the over-delivery limit."},
        ],
        "notes": ["Structured coding helps.", "Tolerance groups are maintained per plant."],
    }
    cleaned = tolerance.scrub_input(payload, "test", report)
    assert cleaned["notes"] == ["Structured coding helps."]
    assert cleaned["steps"][1]["description"] == ""
    assert len(report.exclusions) == 2
    assert all(record.rule == "tolerance" for record in report.exclusions)


def test_post_generation_hit_is_warned_not_silent(tolerance, report):
    tolerance.validate_output({"text": "Maintain the tolerance key."}, "stage", report)
    assert report.warnings, "a post-LLM breach must be visible in the validation log"


def test_final_gate_fails_the_run(tolerance, report):
    tolerance.assert_clean("The tolerance limit is 5 pieces.", "final_document", report)
    assert not report.passed
    assert report.errors


def test_allowance_prevents_false_positive(tolerance):
    assert tolerance.is_clean("The site operates a zero-tolerance safety policy.")
