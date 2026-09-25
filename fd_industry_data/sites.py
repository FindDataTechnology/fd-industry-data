"""Run-site registry: canonical fleet sites for federated crawl dispatch.

`site` in a source manifest declares which site executes it; absent means
the default site (tencent). The registry lives inside the package so the
runner image, the conformance gate, and checkouts share one list — adding
a site is a shared-lib change and ships with the image.
"""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_SITE = "tencent"
_REGISTRY = Path(__file__).resolve().parent / "sites.yaml"


class SiteError(ValueError):
    """A manifest named a site absent from the registry."""


def load_sites(path: Path | None = None) -> dict[str, dict]:
    """Return {site_id: entry} from the registry file."""
    data = yaml.safe_load((path or _REGISTRY).read_text()) or {}
    return {entry["id"]: entry for entry in data.get("sites", [])}


def resolve_site(manifest: dict, path: Path | None = None) -> str:
    """Effective site for a manifest: explicit or the default; must be registered."""
    site = manifest.get("site") or DEFAULT_SITE
    sites = load_sites(path)
    if site not in sites:
        raise SiteError(
            f"site {site!r} is not registered; known sites: " + ", ".join(sorted(sites))
        )
    return site
