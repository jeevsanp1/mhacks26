from __future__ import annotations

from claw.web_search import duckduckgo_search


def test_duckduckgo_search_empty_query() -> None:
    assert "error" in duckduckgo_search("").lower()


def test_duckduckgo_search_formats_results(monkeypatch) -> None:
    class FakeDDGS:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def text(self, query, max_results=5):
            assert query == "ann arbor weather"
            return [
                {
                    "title": "Weather",
                    "href": "https://example.com/w",
                    "body": "Sunny and 70F",
                }
            ]

    monkeypatch.setattr("ddgs.DDGS", FakeDDGS)
    out = duckduckgo_search("ann arbor weather", max_results=3)
    assert "DuckDuckGo results" in out
    assert "Weather" in out
    assert "https://example.com/w" in out
    assert "Sunny and 70F" in out
