"""Query construction: SAP hints are guidance; industry search is broad."""

from afsgap.models import CurrentProcess
from afsgap.research.industry import (
    SCHOLARLY_QUERY_VARIANTS,
    WEB_QUERY_VARIANTS,
    build_industry_queries,
)
from afsgap.research.sap import build_sap_queries

PROCESS = CurrentProcess(process_id="p", process_name="Defective Parts Return")


def test_sap_queries_cover_every_area_hint():
    queries = " | ".join(build_sap_queries(PROCESS)).lower()
    for area in ["sales", "ewm", "claims", "master data", "sales order plus", "aftersales"]:
        assert area in queries, f"no query probes {area}"


def test_sap_queries_include_the_process_itself():
    assert any("Defective Parts Return" in query for query in build_sap_queries(PROCESS))


def test_sap_queries_are_deduplicated():
    queries = build_sap_queries(PROCESS)
    assert len(queries) == len({q.lower() for q in queries})


def test_industry_search_is_not_a_single_narrow_query():
    web, scholarly = build_industry_queries(PROCESS)
    assert len(web) >= 8, "industry research must use several broad framings"
    assert len(scholarly) >= 4
    # most variants must NOT depend on the internal process name
    name_free = [q for q in web if "Defective Parts Return" not in q]
    assert len(name_free) >= len(web) - 2


def test_industry_variants_reframe_rather_than_repeat():
    framings = " ".join(WEB_QUERY_VARIANTS + SCHOLARLY_QUERY_VARIANTS).lower()
    for framing in ["reverse logistics", "operating model", "dealer", "warranty", "distribution network"]:
        assert framing in framings
