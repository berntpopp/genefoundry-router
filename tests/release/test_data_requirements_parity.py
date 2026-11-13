"""validate-config accepts a data block iff release evidence assembly would seal it.

strchive-link v0.1.0 declared `upstream-live` with no release_tag/digest. The config
gate passed it; assemble-manifest rejected it only AFTER the image was published,
burning the version. These tests run the reusable workflow's real jq projection and the
real assembler, so the two gates cannot drift apart again.
"""

from __future__ import annotations

import copy
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Final

import pytest
from pydantic import ValidationError

from genefoundry_router.release.definitions import (
    DefinitionEvidence,
    capture_definitions,
    verify_definition_contract,
)
from genefoundry_router.release.evidence import (
    EvidenceAssemblyError,
    ScannerIdentity,
    assemble_application_release_manifest,
)
from genefoundry_router.release.models import ReleaseConfig
from tests.release.test_container_release_workflow import (
    REUSABLE,
    _executable_shell,
    _load,
    _step_run,
)
from tests.release.test_evidence import (
    _asset_files,
    _identity,
    _install_definition_assets,
    _tools,
)

_ALLOW = ["opt/venv/lib/python3.14/site-packages/beartype/_check/cls/hint/data/__init__.py"]
_SERVICE = {"name": "x-link", "compose_files": ["docker/docker-compose.yml"]}

# Data blocks copied from real fleet container-release.json files (origin/main).
ROUTER_NONE = {"mode": "none", "image_allowlist": _ALLOW}
CLINGEN = {
    "mode": "external-reference",
    "release_tag": "data-clingen-f0c047cff56d589b",
    "digest": "sha256:a8fdc8af639ac94a44be4efd9163400c7b3c8f9f21c33fc67fd83bae11a52357",
    "schema_compatibility": ["2"],
    "image_allowlist": _ALLOW,
}
GENEREVIEWS = {
    "mode": "restored-database",
    "release_tag": "corpus-data-2026-11-12-r1",
    "schema_compatibility": ["0007_embedding_run_identity"],
    "digest": "sha256:22d7b63a472625d7a373764d3ef177ebccd0c19c18f96c6015d119febf5f4e0b",
}
PUBTATOR = {
    "mode": "restored-database",
    "release_tag": "observed-2026.07.13",
    "digest": "sha256:" + "b" * 64,
}
STRCHIVE_V011 = {
    "mode": "upstream-live",
    "image_allowlist": _ALLOW,
    "egress_allowlist": ["raw.githubusercontent.com"],
    "release_tag": "strchive-e964fa88df3b2189ee478cbd6eaad15bb28e3a0a",
    "digest": "sha256:16301ecb00239f0e3c684913aefc2f48c6f5a7c2a481958aab4db55d282bca86",
    "reproducible_rollback": False,
}


def _without(block: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: value for key, value in block.items() if key not in keys}


def _config(data: dict[str, Any] | None, contract: str, adoption: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {
        "schema_version": 1,
        "service": _SERVICE,
        "definitions": {"contract": contract},
    }
    if data is not None:
        config["data"] = copy.deepcopy(data)
    if adoption is not None:
        config["data_identity_contract"] = adoption
    return config


INDEPENDENT: Final = "data-independent"
BOUND: Final = "data-bound"
# (case id, config, expected verdict) -- every data mode x contract x adoption.
MATRIX = [
    ("none", _config(ROUTER_NONE, INDEPENDENT, None), True),
    ("none-bound", _config(ROUTER_NONE, BOUND, "runtime-v1"), False),
    ("data-omitted-defaults-to-none", _config(None, INDEPENDENT, None), True),
    ("external-ref-runtime-v1", _config(CLINGEN, BOUND, "runtime-v1"), True),
    ("external-ref-unadopted", _config(CLINGEN, BOUND, "unadopted"), True),
    ("external-ref-bound-no-adoption", _config(CLINGEN, BOUND, None), False),
    ("external-ref-independent", _config(CLINGEN, INDEPENDENT, None), True),
    (
        "external-ref-independent-no-identity",
        _config(_without(CLINGEN, "release_tag", "digest"), INDEPENDENT, None),
        False,
    ),
    (
        "external-ref-bound-no-identity",
        _config(_without(CLINGEN, "release_tag", "digest"), BOUND, "runtime-v1"),
        False,
    ),
    (
        "external-ref-null-identity",
        _config(CLINGEN | {"release_tag": None, "digest": None}, INDEPENDENT, None),
        False,
    ),
    (
        "external-ref-tag-only",
        _config(_without(CLINGEN, "digest"), INDEPENDENT, None),
        False,
    ),
    ("restored-db-runtime-v1", _config(GENEREVIEWS, BOUND, "runtime-v1"), True),
    ("restored-db-unadopted", _config(PUBTATOR, BOUND, "unadopted"), True),
    ("restored-db-independent", _config(GENEREVIEWS, INDEPENDENT, None), True),
    (
        "restored-db-no-identity",
        _config(_without(GENEREVIEWS, "release_tag", "digest"), INDEPENDENT, None),
        False,
    ),
    ("upstream-live-strchive-v0.1.1", _config(STRCHIVE_V011, INDEPENDENT, None), True),
    (
        "upstream-live-strchive-v0.1.0",
        _config(_without(STRCHIVE_V011, "release_tag", "digest"), INDEPENDENT, None),
        False,
    ),
    (
        "upstream-live-rollback-omitted",
        _config(_without(STRCHIVE_V011, "reproducible_rollback"), INDEPENDENT, None),
        False,
    ),
    ("upstream-live-runtime-v1", _config(STRCHIVE_V011, BOUND, "runtime-v1"), True),
    ("upstream-live-unadopted", _config(STRCHIVE_V011, BOUND, "unadopted"), True),
]


def _workflow_projection(config: dict[str, Any], tmp_path: Path) -> dict[str, Any] | None:
    """Run the reusable workflow's own jq projection; None when jq fails."""
    capture = _executable_shell(_step_run(_load(REUSABLE)["jobs"]["capture"], "capture_tools()"))
    start = capture.index("jq -e '. as $release")
    program = capture[start + len("jq -e '") : capture.index("' \\\n", start)]
    source = tmp_path / "container-release.json"
    source.write_text(json.dumps(config), encoding="utf-8")
    jq = shutil.which("jq")
    assert jq is not None
    result = subprocess.run(  # noqa: S603 -- jq program read from our own checked-in workflow
        [jq, "-e", program, str(source)], capture_output=True, check=False
    )
    return json.loads(result.stdout) if result.returncode == 0 else None


def _captured_definitions(config: dict[str, Any]) -> DefinitionEvidence:
    """Definition evidence exactly as the capture job derives it from the config."""
    if config["definitions"]["contract"] != BOUND:
        first = capture_definitions(_tools(), context={"fixture": "empty", "rows": 0})
        second = capture_definitions(_tools(), context={"fixture": "populated", "rows": 1})
        return verify_definition_contract(INDEPENDENT, (first, second))
    identity = {"release_tag": config["data"]["release_tag"], "digest": config["data"]["digest"]}
    if config["data_identity_contract"] == "runtime-v1":
        capture = capture_definitions(
            _tools(), context={"runtime": "published"}, observed_identity=identity
        )
        return verify_definition_contract(BOUND, (capture,), observed_identity=identity)
    pinned = {"data_release_tag": identity["release_tag"], "data_digest": identity["digest"]}
    capture = capture_definitions(
        _tools(), context={"runtime": "published"}, adoption="unadopted", **pinned
    )
    return verify_definition_contract(BOUND, (capture,), adoption="unadopted", **pinned)


def _assembly_accepts(config: dict[str, Any], tmp_path: Path) -> bool:
    projection = _workflow_projection(config, tmp_path)
    if projection is None:
        return False
    try:
        definitions = _captured_definitions(config)
    except KeyError, ValueError:
        return False  # the capture job's `jq -er .data.release_tag` would fail too
    assets, _, image_digest = _asset_files(tmp_path)
    _install_definition_assets(tmp_path, definitions)
    try:
        assemble_application_release_manifest(
            identity=_identity(image_digest),
            definitions=definitions,
            scanner=ScannerIdentity(version="0.66.0", database_updated_at="2026-07-13T10:30:00Z"),
            data_requirements=projection,
            assets=assets,
        )
    except EvidenceAssemblyError:
        return False
    return True


def _config_accepts(config: dict[str, Any]) -> bool:
    try:
        ReleaseConfig.model_validate(config)
    except ValidationError:
        return False
    return True


@pytest.mark.parametrize(
    ("config", "expected"),
    [pytest.param(config, expected, id=case) for case, config, expected in MATRIX],
)
def test_validate_config_accepts_iff_evidence_assembly_accepts(
    config: dict[str, Any], expected: bool, tmp_path: Path
) -> None:
    assert _assembly_accepts(config, tmp_path) is expected
    assert _config_accepts(config) is expected


@pytest.mark.parametrize(
    "config",
    [pytest.param(config, id=case) for case, config, expected in MATRIX if expected],
)
def test_sealed_projection_matches_the_workflow_jq(config: dict[str, Any], tmp_path: Path) -> None:
    sealed = ReleaseConfig.model_validate(config).sealed_data_requirements()
    assert json.loads(json.dumps(sealed)) == _workflow_projection(config, tmp_path)


def test_prepare_validates_the_config_unconditionally_before_build() -> None:
    """The overlay gate can be waived; this check must not be."""
    step = _step_run(_load(REUSABLE)["jobs"]["prepare"], "validate-source")
    assert "container_release.py validate-config \\\n--config container-release.json" in (
        "\n".join(line.strip() for line in _executable_shell(step).splitlines())
    )
