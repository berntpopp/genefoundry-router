# GeneFoundry Response-Envelope Standard v1.2

> **Status: ADOPTED v1.2, ratified 2026-11-26.** This document extends the
> [v1 response-envelope contract](RESPONSE-ENVELOPE-STANDARD-v1.md) and its
> [v1.1 amendment](RESPONSE-ENVELOPE-STANDARD-v1.1.md) with one **core rule**: every successful
> result carries its provenance. Rules not amended here remain v1 and v1.1.

## Purpose

A traceable answer names the database, the release and the time of retrieval, next to the record
identifiers. A model can then cite where a value came from, and a reader can check it against the
same release. In v1 `source` was a SHOULD, `data_version` applied "as applies", and a result-level
`retrieved_at` was not defined. v1.2 makes all three mandatory.

## Core rule

Every successful result (`success: true`) that carries upstream data MUST have these three fields
in its envelope `_meta` block (v1 §4), each a non-empty string:

| Field | Meaning |
|---|---|
| `source` | The database or service the data comes from. |
| `data_version` | The release or version of the data served. |
| `retrieved_at` | The time the server fetched the data from the upstream. |

```json
{
  "success": true,
  "result": { "hgnc_id": "HGNC:1097", "approved_symbol": "BRAF" },
  "_meta": {
    "request_id": "…",
    "elapsed_ms": 3,
    "source": "hgnc",
    "data_version": "2026-11-03",
    "retrieved_at": "2026-11-20T08:00:00Z"
  }
}
```

The rule is **core**: a backend that omits a field, or sends it empty, is not conformant.
Error results (`success: false`) MAY carry the fields and are not required to. Tools that orient a
client and return no upstream data (capabilities, health, diagnostics; the ops/meta carve-out of
the Tool-Naming Standard) are outside the rule.

## Field semantics

### `source`

The short, stable, lowercase name of the upstream database or service (`gnomad`, `clinvar`,
`pubtator`). It is normally the backend's namespace. It names the data, not the server: a value
such as `litvar-link` is the server's name and SHOULD be `litvar`. A tool that answers from a
different upstream than the backend's main one names that upstream.

### `data_version`

The identifier under which the upstream publishes the data that was served:

- a named or numbered release (`4.1.1`, `gtex_v8`, `26.09`), or a release date (`2026-11-03`);
- for a local snapshot or mirror, the upstream release the snapshot was taken from, or, when the
  upstream names none, an identifier of the snapshot itself (a commit or a content digest);
- for a live upstream that publishes no release identifier, the upstream API version, or, when
  there is none, the literal `live`. `retrieved_at` then dates the data.

`data_version` is one string. A result that draws on several components names the primary dataset
here and MAY list the others in `_meta.source_versions` (v1 §4). Backend-specific names for the
same fact (`gnomad_release`, `clinvar_release_date`, `hpo_version`, `snapshot_version`,
`corpus_version` and the like) MAY be kept next to it, but do not replace it.

### `retrieved_at`

An RFC 3339 timestamp in UTC (`2026-11-20T08:00:00Z`): the moment the server obtained the data
from the upstream. It is never later than the response.

| How the result was produced | `retrieved_at` is |
|---|---|
| Live upstream call | the time of that call |
| **Cache hit** | **the time of the upstream fetch that filled the cache entry**, not the time of the cache read and not the time of this response |
| Local snapshot or mirror | the time the snapshot was downloaded from the upstream |
| Several upstream fetches | the oldest of them |

A backend therefore stores the fetch time with each cache entry and with each installed snapshot.
A cached result keeps its original `retrieved_at` for as long as the entry lives.

The per-text `provenance.retrieved_at` of an `untrusted_text` object (v1.1) has the same meaning
and stays mandatory there; it does not replace the result-level field.

## Router

The router passes the three fields through unchanged. It MUST NOT add, fill in or alter them: it
does not know when a backend fetched its data (v1.1, "does not sanitize or manufacture backend
provenance").

## Conformance

The behaviour gate ([`docs/conformance/behaviour.py`](conformance/behaviour.py), vendored into
every backend) checks the rule on each tool it can call: the successful result of the call built
from the tool's own `examples` MUST carry non-empty `_meta.source`, `_meta.data_version` and
`_meta.retrieved_at`. A missing or empty field fails the gate and names the field.

## Adoption and compatibility

Adding the fields is additive; a backend ships them in a minor release. Moving a value that a
backend already publishes elsewhere (for example at the top level of the envelope) keeps the old
location for one compatibility release. A backend is v1.2 conformant when the behaviour gate
passes against its container.
