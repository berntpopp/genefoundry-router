"""ci/fleet-urls.env must define a public URL for exactly the enabled backends."""

import os
import re
from pathlib import Path

from genefoundry_router.config import load_registry


def test_ci_fleet_urls_covers_enabled_backends():
    registry = load_registry("servers.yaml", os.environ)
    enabled = {b.url_env for b in registry if b.enabled}

    text = Path("ci/fleet-urls.env").read_text(encoding="utf-8")
    # Comment lines (``# ...``) are ignored by this regex, so they're free to keep.
    defined = set(re.findall(r"^(GF_[A-Z0-9_]+)=\S+", text, re.MULTILINE))

    # Contract: define a URL for EXACTLY the enabled backends — no more, no less. A URL
    # for a disabled (or unknown) backend is dead weight the probe never reads, so flag it.
    missing = enabled - defined
    extra = defined - enabled
    assert not missing, f"ci/fleet-urls.env missing: {sorted(missing)}"
    assert not extra, f"ci/fleet-urls.env has vars not for enabled backends: {sorted(extra)}"
