from types import SimpleNamespace
from unittest.mock import MagicMock
import pytest

from shelfmark.release_sources.direct_download import (
    DirectDownloadSource,
    SearchUnavailableError,
    _get_source_priority,
    search_books,
)
from shelfmark.core.models import SearchFilters
from shelfmark.release_sources import BrowseRecord


def _fake_config_get(values: dict[str, object]):
    def _get(key: str, default=None, user_id=None):
        del user_id
        return values.get(key, default)
    return _get


def test_anna_toggle_disabled_disables_aa_sources_in_priority(monkeypatch):
    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(
        dd.config,
        "get",
        _fake_config_get(
            {
                "ENABLE_ANNAS_ARCHIVE": False,
                "AA_DONATOR_KEY": "some-key",
                "FAST_SOURCES_DISPLAY": [
                    {"id": "aa-fast", "enabled": True},
                    {"id": "libgen", "enabled": True},
                ],
                "SOURCE_PRIORITY": [
                    {"id": "aa-slow-nowait", "enabled": True},
                    {"id": "aa-slow-wait", "enabled": True},
                    {"id": "zlib", "enabled": True},
                ],
            }
        ),
    )
    monkeypatch.setattr("shelfmark.core.mirrors.has_download_source_mirror_configuration", lambda source_id: True)

    priority = {item["id"]: item["enabled"] for item in _get_source_priority()}

    assert priority["aa-fast"] is False
    assert priority["aa-slow-nowait"] is False
    assert priority["aa-slow-wait"] is False
    assert priority["libgen"] is True
    assert priority["zlib"] is True


def test_anna_toggle_disabled_allows_direct_download_with_zlib_mirror(monkeypatch):
    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(
        dd.config,
        "get",
        _fake_config_get(
            {
                "DIRECT_DOWNLOAD_ENABLED": True,
                "ENABLE_ANNAS_ARCHIVE": False,
            }
        ),
    )
    monkeypatch.setattr("shelfmark.core.mirrors.has_aa_mirror_configuration", lambda: False)
    monkeypatch.setattr("shelfmark.core.mirrors.has_zlib_mirror_configuration", lambda: True)
    monkeypatch.setattr("shelfmark.core.mirrors.has_libgen_mirror_configuration", lambda: False)

    source = DirectDownloadSource()
    assert source.is_available() is True


def test_search_books_delegates_to_zlib_when_anna_disabled(monkeypatch):
    import shelfmark.release_sources.direct_download as dd

    monkeypatch.setattr(
        dd.config,
        "get",
        _fake_config_get(
            {
                "ENABLE_ANNAS_ARCHIVE": False,
            }
        ),
    )

    mock_zlib_results = [
        BrowseRecord(
            id="12345",
            title="Dune",
            source="direct",
            author="Frank Herbert",
            format="epub",
            download_urls=["https://z-library.sk/book/12345"],
        )
    ]
    mock_search = MagicMock(return_value=mock_zlib_results)
    monkeypatch.setattr(dd, "_search_zlib_books", mock_search)

    results = search_books("Dune", SearchFilters())
    assert results == mock_zlib_results
    mock_search.assert_called_once_with("Dune", SearchFilters())


def test_search_zlib_books_parses_bookcards(monkeypatch):
    import shelfmark.release_sources.direct_download as dd

    sample_html = """
    <html>
      <body>
        <z-bookcard id="998877" href="/book/998877/sample" download="/dl/998877/token" extension="epub" filesize="1.5 MB" year="2021" language="Dutch" publisher="SamplePub">
          <span slot="title">Sample Title</span>
          <span slot="author">Sample Author</span>
        </z-bookcard>
      </body>
    </html>
    """
    monkeypatch.setattr("shelfmark.download.http.html_get_page", lambda url, **kwargs: sample_html)
    monkeypatch.setattr("shelfmark.core.mirrors.get_zlib_primary_url", lambda: "https://z-library.sk")

    results = dd._search_zlib_books("Sample Title", SearchFilters(lang=["Dutch"]))
    assert len(results) == 1
    rec = results[0]
    assert rec.id == "998877"
    assert rec.title == "Sample Title"
    assert rec.author == "Sample Author"
    assert rec.format == "epub"
    assert rec.size == "1.5 MB"
    assert rec.year == "2021"
    assert rec.language == "Dutch"
    assert rec.publisher == "SamplePub"
    assert rec.download_urls == ["https://z-library.sk/dl/998877/token"]
