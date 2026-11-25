"""The language-neutral envelope artifact: every vector in envelope/tests agrees with the schema."""

import json
import re
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "envelope/envelope-v1.2.schema.json").read_text())
VECTORS = sorted((ROOT / "envelope/tests").glob("*/*.json"))


def test_schema_is_valid_draft_2020_12():
    Draft202012Validator.check_schema(SCHEMA)
    assert {p.parent.name for p in VECTORS} == {"valid", "invalid"}


@pytest.mark.parametrize("path", VECTORS, ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_vector(path: Path):
    conforms = Draft202012Validator(SCHEMA).is_valid(json.loads(path.read_text()))
    assert conforms == (path.parent.name == "valid")


def test_the_standards_own_example_conforms():
    doc = (ROOT / "docs/RESPONSE-ENVELOPE-STANDARD-v1.2.md").read_text()
    examples = re.findall(r"```json\n(.*?)```", doc, re.S)
    assert examples
    for example in examples:
        Draft202012Validator(SCHEMA).validate(json.loads(example))
