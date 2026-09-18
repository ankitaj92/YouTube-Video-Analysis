"""OpenAlex integration: abstract reconstruction and graceful failure."""

from afsgap.research.openalex import reconstruct_abstract, search_works


def test_abstract_reconstruction():
    inverted = {"Returns": [0], "are": [1], "costly": [2], "and": [3], "slow": [4]}
    assert reconstruct_abstract(inverted) == "Returns are costly and slow"


def test_repeated_words_land_in_every_position():
    inverted = {"the": [0, 2], "return": [1], "process": [3]}
    assert reconstruct_abstract(inverted) == "the return the process"


def test_empty_abstract_is_safe():
    assert reconstruct_abstract(None) == ""
    assert reconstruct_abstract({}) == ""


def test_network_failure_returns_no_works(monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("no network")

    monkeypatch.setattr("afsgap.research.openalex.requests.get", explode)
    assert search_works("anything") == []


def test_results_are_mapped_to_scholarly_works(monkeypatch):
    class FakeResponse:
        @staticmethod
        def raise_for_status():
            return None

        @staticmethod
        def json():
            return {
                "results": [
                    {
                        "display_name": "Reverse logistics in automotive networks",
                        "publication_year": 2022,
                        "doi": "https://doi.org/10.1000/abc",
                        "abstract_inverted_index": {"A": [0], "study": [1]},
                        "primary_location": {
                            "source": {"display_name": "Journal of Supply Chains"},
                            "landing_page_url": "https://example.org/a",
                        },
                    }
                ]
            }

    monkeypatch.setattr("afsgap.research.openalex.requests.get", lambda *a, **k: FakeResponse())
    works = search_works("automotive returns")
    assert works[0].title == "Reverse logistics in automotive networks"
    assert works[0].doi == "10.1000/abc"
    assert works[0].venue == "Journal of Supply Chains"
    assert works[0].abstract_excerpt == "A study"
