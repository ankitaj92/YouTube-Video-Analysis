"""Industry benchmark content must stay qualitative."""

import pytest

KPI_BEARING = [
    "Leading players settle claims within 5 days.",
    "Fill rate improves for networks that gate returns early.",
    "OTIF is the headline measure for these networks.",
    "Top quartile performers digitise the inspection step.",
    "Costs fall by 20% when returns are gated at the dealer.",
    "Turnaround time is the dealer's main complaint.",
    "The programme targets a cost per claim of 30 EUR.",
]

QUALITATIVE = [
    "Leaders decide returnability at the point of diagnosis.",
    "Inspection outcomes are captured as structured disposition codes.",
    "Status is shared with the dealer from initiation to settlement.",
    "Regional centres concentrate technical inspection authority.",
]


@pytest.mark.parametrize("text", KPI_BEARING)
def test_kpi_language_is_removed(kpi, text):
    assert kpi.matches(text)
    assert kpi.scrub(text).text == ""


@pytest.mark.parametrize("text", QUALITATIVE)
def test_qualitative_practice_survives(kpi, text):
    assert kpi.is_clean(text)


def test_mixed_paragraph_keeps_the_practice_drops_the_number(kpi):
    text = (
        "Leaders gate returns at the dealer. This reduces cost by 20%. "
        "Inspection outcomes are coded."
    )
    result = kpi.scrub(text)
    assert "gate returns at the dealer" in result.text
    assert "Inspection outcomes are coded" in result.text
    assert "20%" not in result.text


def test_validate_output_records_and_warns(kpi, report):
    payload = {"leading_practices": [{"description": "Claims settle within 5 days."}]}
    cleaned = kpi.validate_output(payload, "industry.output", report)
    assert cleaned["leading_practices"][0]["description"] == ""
    assert report.warnings
    assert report.exclusions[0].rule == "kpi"
