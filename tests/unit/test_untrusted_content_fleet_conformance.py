"""Fleet-level v1.1 adoption gate: no untrusted-text backend left unfenced."""

from pathlib import Path

import yaml

INVENTORY = Path("docs/conformance/untrusted-text-inventory.yml")


def _rows() -> list[dict]:
    return yaml.safe_load(INVENTORY.read_text())["backends"]


def test_every_untrusted_text_backend_is_fenced() -> None:
    pending = [
        r["backend"]
        for r in _rows()
        if r["classification"] == "untrusted-text" and r["compatibility"] != "breaking-v1.1"
    ]
    assert not pending, f"untrusted-text backends not yet fenced: {pending}"


def test_every_untrusted_text_row_names_a_test_vector() -> None:
    for r in _rows():
        if r["classification"] == "untrusted-text":
            assert r["test_vector"] and r["test_vector"] != "none", r["backend"]


def test_no_untrusted_text_rows_are_na_with_evidence() -> None:
    for r in _rows():
        if r["classification"] == "no-untrusted-text":
            assert r["compatibility"] == "n/a-no-untrusted-text", r["backend"]
            assert r["evidence"], r["backend"]
