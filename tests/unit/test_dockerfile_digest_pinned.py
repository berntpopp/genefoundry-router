"""docker/Dockerfile must pin every external image by digest (Container-Hardening v1 §1.2)."""

import re
from pathlib import Path

DOCKERFILE = Path("docker/Dockerfile")


def _external_image_refs(text: str) -> list[str]:
    """Every `FROM <ref>` and registry-qualified `COPY --from=<ref>` (excludes build-stage names)."""
    refs: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if (m := re.match(r"^FROM\s+(\S+)", s)) and m.group(1) != "scratch":
            refs.append(m.group(1))
        if (m := re.match(r"^COPY\s+--from=(\S+)", s)) and "/" in m.group(1):
            refs.append(m.group(1))  # a registry ref (has "/"), not a stage alias like `builder`
    return refs


def test_no_floating_latest_tag() -> None:
    assert ":latest" not in DOCKERFILE.read_text(encoding="utf-8"), (
        "no :latest base images — mutable tag is a supply-chain hole"
    )


def test_every_external_image_is_digest_pinned() -> None:
    refs = _external_image_refs(DOCKERFILE.read_text(encoding="utf-8"))
    assert refs, "expected at least one FROM / COPY --from image ref"
    unpinned = [r for r in refs if "@sha256:" not in r]
    assert not unpinned, f"digest-pin these (Container-Hardening v1 §1.2): {unpinned}"


def test_production_image_contains_packaged_fleet_baseline() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "from importlib.resources import files" in text
    assert "files('genefoundry_router.data').joinpath('fleet-baseline.json').is_file()" in text


def test_dockerfile_healthcheck_uses_valid_cmd_shell_form() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "CMD-SHELL" not in text
    assert 'CMD curl -f -H "Host: ${GF_HEALTHCHECK_HOST}"' in text
