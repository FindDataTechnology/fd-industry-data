"""DataProvider implementation for fd-industry-data datasource."""

from __future__ import annotations

from typing import Any

from fd_open_data_protocol.provider import BaseDataProvider

from .catalog import CATALOG


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
