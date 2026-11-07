"""DataProvider implementation for fd-industry-data datasource."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fd_open_data_protocol.provider import BaseDataProvider

from .catalog import CATALOG

_SPIDERS = Path(__file__).resolve().parent.parent / "spiders"


def _dashed_unit_entry(slug: str, target: str):
    """Load ``target`` from ``spiders/<slug>/spider.py`` when the dir name has a dash.

    A dash is not a valid module identifier, and the conformance gate forbids
    keeping both ``eurostat-api`` and ``eurostat_api`` (adapter-dash-underscore-pair),
    so such units are loaded by file path instead of by import. Delegates to the
    same loader the golden replayer uses, which names the module
    ``unit_<slug_underscored>`` so repeated loads never collide in sys.modules.
    """
    from .health.guard import load_unit_entry

    return load_unit_entry(_SPIDERS / slug, target)


class IndustryDataProvider(BaseDataProvider):
    """DataProvider interface for industry data (NBS GDP, CPI, PPI)."""

    name = "fd-industry-data"

    def registry(self) -> dict:
        """Return the datasource manifest catalog."""
        return CATALOG

    def run(self, command: str, params: dict) -> Any:
        """Dispatch to real spider implementations.

        Args:
            command: Function name to execute (e.g., "get_gdp_quarterly")
            params: Function parameters as keyword arguments

        Returns:
            List of dicts with indicator data rows

        Raises:
            NotImplementedError: If command is not implemented
        """
        if command == "get_gdp_quarterly":
            from spiders.nbs_gdp.spider import get_gdp_quarterly
            return get_gdp_quarterly(**params)
        elif command == "get_cpi_monthly":
            from spiders.nbs_gdp.spider import get_macro_data
            return get_macro_data(["cpi_monthly"], **params)
        elif command == "get_ppi_monthly":
            from spiders.nbs_gdp.spider import get_macro_data
            return get_macro_data(["ppi_monthly"], **params)
        elif command == "run_education_openalex":
            from spiders.education_openalex.spider import run_education_openalex
            return run_education_openalex(**params)
        elif command == "run_education_openalex_skeleton":
            from spiders.education_openalex.spider import run_education_openalex_skeleton
            return run_education_openalex_skeleton(**params)
        elif command == "run_card_curated_import":
            from spiders.education_card_catalog.spider import run_card_curated_import
            return run_card_curated_import(**params)
        elif command == "run_ocw_catalog":
            from spiders.education_cc_courses.spider import run_ocw_catalog
            return run_ocw_catalog(**params)
        elif command == "run_openedx_catalog":
            from spiders.education_cc_courses.spider import run_openedx_catalog
            return run_openedx_catalog(**params)
        elif command == "run_podcast_seed":
            from spiders.education_podcast.spider import run_podcast_seed
            return run_podcast_seed(**params)
        elif command == "run_podcast_poller":
            from spiders.education_podcast.spider import run_podcast_poller
            return run_podcast_poller(**params)
        elif command == "run_education_oa_fulltext":
            from spiders.education_oa_fulltext.spider import run_education_oa_fulltext
            return run_education_oa_fulltext(**params)
        elif command == "run_gutendex_catalog":
            from spiders.education_oer_aggregators.spider import run_gutendex_catalog
            return run_gutendex_catalog(**params)
        elif command == "run_wikibooks_catalog":
            from spiders.education_oer_aggregators.spider import run_wikibooks_catalog
            return run_wikibooks_catalog(**params)
        elif command == "run_doab_oai_harvest":
            from spiders.education_oer_aggregators.spider import run_doab_oai_harvest
            return run_doab_oai_harvest(**params)
        # hotel-vertical wave 0: the four accommodation/tourism series live in
        # eurostat-api but are deliberately NOT folded into its scheduled
        # wave-C rotation — adding four buckets there would dilute the
        # restaurant/agriculture shares of the same --limit. They get their own
        # command surface instead (the wave-0 extractor calls these by name).
        # eurostat-api is dash-named, so it is loaded by file path.
        elif command == "run_eurostat_i55":
            return _dashed_unit_entry("eurostat-api", command)(**params)
        elif command == "run_eurostat_tour_occ":
            return _dashed_unit_entry("eurostat-api", command)(**params)
        elif command == "run_eurostat_tour_annual":
            return _dashed_unit_entry("eurostat-api", command)(**params)
        elif command == "run_eurostat_tour_cap":
            return _dashed_unit_entry("eurostat-api", command)(**params)
        elif command == "run_eurostat_flower_area":
            # 花卉垂直线（add-flower-vertical 3.3）：EU 花卉主面积。按酒店线先例
            # 走 provider 按需取数，不并入 run_eurostat_api 的 --limit 轮转
            # （轮转会把 8 geo × 12 年截成头 10 行，存成半截序列）。
            return _dashed_unit_entry("eurostat-api", command)(**params)
        else:
            raise NotImplementedError(f"Unknown command: {command}")

    def introspect(self) -> list[dict]:
        """Introspection metadata for this datasource."""
        return [
            {
                "command": "get_gdp_quarterly",
                "description": "Fetch quarterly GDP data from National Bureau of Statistics",
                "parameters": ["start_year"],
            },
            {
                "command": "get_cpi_monthly",
                "description": "Fetch monthly Consumer Price Index data from NBS",
                "parameters": ["start_year"],
            },
            {
                "command": "get_ppi_monthly",
                "description": "Fetch monthly Producer Price Index data from NBS",
                "parameters": ["start_year"],
            },
            {
                "command": "run_education_openalex",
                "description": "Ingest OpenAlex Education subfield works (skeleton/oa_fulltext/incremental phases)",
                "parameters": ["limit", "phase", "education_source"],
            },
            {
                "command": "run_education_openalex_skeleton",
                "description": "Ingest OpenAlex Education subfield works metadata skeleton (legacy alias)",
                "parameters": ["limit", "phase"],
            },
        ]
