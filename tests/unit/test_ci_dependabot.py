"""Dependabot must watch the Docker base-image digests, pinned actions, and uv deps."""

from pathlib import Path

import yaml


def test_dependabot_watches_docker_actions_and_uv() -> None:
    cfg = yaml.safe_load(Path(".github/dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {u["package-ecosystem"] for u in cfg["updates"]}
    assert "docker" in ecosystems, "watch docker base-image digests (Container-Hardening v1 §8.28)"
    assert "github-actions" in ecosystems, "watch SHA-pinned actions"
    assert "uv" in ecosystems, "watch uv.lock / pyproject deps"
