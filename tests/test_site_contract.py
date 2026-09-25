"""Manifest site contract (crawl-platform tasks 2.1/2.2): default site,
explicit registered site, registry growth, unknown-site rejection."""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from fd_industry_data.sites import DEFAULT_SITE, SiteError, load_sites, resolve_site


def _registry(tmp_path, extra_id=None):
    p = tmp_path / "sites.yaml"
    sites = [{"id": "tencent", "kind": "k8s"}, {"id": "nbs-workers", "kind": "docker"}]
    if extra_id:
        sites.append({"id": extra_id, "kind": "docker"})
    p.write_text(yaml.safe_dump({"version": "1", "sites": sites}))
    return p


def test_default_site_when_absent(tmp_path):
    assert resolve_site({"name": "bls"}, _registry(tmp_path)) == DEFAULT_SITE


def test_explicit_registered_site(tmp_path):
    manifest = {"name": "x", "site": "nbs-workers"}
    assert resolve_site(manifest, _registry(tmp_path)) == "nbs-workers"


def test_newly_registered_site_becomes_usable(tmp_path):
    manifest = {"name": "x", "site": "lab-top"}
    try:
        resolve_site(manifest, _registry(tmp_path))
    except SiteError:
        pass
    else:
        raise AssertionError("expected SiteError before registration")
    assert resolve_site(manifest, _registry(tmp_path, extra_id="lab-top")) == "lab-top"


def test_unknown_site_rejected(tmp_path):
    try:
        resolve_site({"site": "cheap"}, _registry(tmp_path))
    except SiteError as e:
        assert "cheap" in str(e)
    else:
        raise AssertionError("expected SiteError")


def test_packaged_registry_loads():
    sites = load_sites()
    assert DEFAULT_SITE in sites
    assert all("id" in e for e in sites.values())
