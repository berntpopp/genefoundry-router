"""servers.yaml must not carry stale 'enable once … deployed' comments for live backends."""

import os
from pathlib import Path

from genefoundry_router.config import load_registry


def test_no_stale_enable_once_comment() -> None:
    text = Path("servers.yaml").read_text(encoding="utf-8")
    assert "enable once" not in text, "remove stale 'enable once … deployed' comment"


def test_hpo_is_live_and_enabled() -> None:
    registry = load_registry("servers.yaml", os.environ)
    hpo = next(b for b in registry if b.namespace == "hpo")
    assert hpo.enabled, "hpo is deployed + baselined; it must stay enabled"
