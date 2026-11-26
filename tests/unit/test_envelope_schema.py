"""The language-neutral envelope artifact: schema, vectors and sanitize vectors agree."""

import base64
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "envelope/envelope-v1.2.schema.json").read_text())
VECTORS = sorted((ROOT / "envelope/vectors").glob("*/*.json"))
SANITIZE = json.loads((ROOT / "envelope/vectors/sanitize.json").read_text())
TEXT = SCHEMA["$defs"]["untrusted_text"]["properties"]


def test_schema_is_the_released_genefoundry_envelope_file():
    # update together with a genefoundry-envelope release
    digest = hashlib.sha256((ROOT / "envelope/envelope-v1.2.schema.json").read_bytes()).hexdigest()
    assert digest == "c3c47e20d7dd189aac2568fc963b5514d7a5ac2c3e5f21d126f42c96d1ad1ae3"


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)
    assert {p.parent.name for p in VECTORS} == {"valid", "invalid"}


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vector(path: Path):
    """A valid vector has no error; an invalid one breaks exactly one rule."""
    errors = list(Draft202012Validator(SCHEMA).iter_errors(json.loads(path.read_text())))
    assert len(errors) == (0 if path.parent.name == "valid" else 1), [e.message for e in errors]


def test_the_standards_own_example_conforms():
    doc = (ROOT / "docs/RESPONSE-ENVELOPE-STANDARD-v1.2.md").read_text()
    examples = re.findall(r"```json\n(.*?)```", doc, re.S)
    assert examples
    for example in examples:
        Draft202012Validator(SCHEMA).validate(json.loads(example))


@pytest.mark.parametrize("case", SANITIZE, ids=lambda c: c["description"])
def test_sanitize_vector(case: dict[str, str]):
    """v1.1: NFC, then remove the listed code points; the digest is over the raw bytes.

    The router fences nothing itself (v1.1: it does not sanitize), so this recomputes each
    vector from the schema's own `text` pattern instead of calling a router function.
    """
    assert set(case) == {"description", "raw_base64", "text", "raw_sha256"}
    raw = base64.b64decode(case["raw_base64"])
    removed = TEXT["text"]["pattern"].removeprefix("^[^").removesuffix("]*$")
    text = re.sub(f"[{removed}]", "", unicodedata.normalize("NFC", raw.decode()))
    assert case["text"] == text
    assert case["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert re.search(TEXT["text"]["pattern"], case["text"]) is not None
    assert re.search(TEXT["raw_sha256"]["pattern"], case["raw_sha256"])
