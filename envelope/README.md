# GeneFoundry response envelope v1.2

Every GeneFoundry tool returns one JSON object, the **envelope**, as the `structuredContent` of
its MCP tool result. A success carries `success: true`, the payload under `result` (object) or
`results` (array), and a `_meta` block that names where the data came from (`source`), which
release (`data_version`) and when it was fetched (`retrieved_at`, UTC). An error carries
`success: false`, `error_code`, `message` and `retryable`. External free text is fenced as an
`untrusted_text` object.

This directory is the standard in a form any language can load:

- [`envelope-v1.2.schema.json`](envelope-v1.2.schema.json): JSON Schema (draft 2020-12).
- [`tests/valid/`](tests/valid) and [`tests/invalid/`](tests/invalid): test vectors. Each invalid
  file breaks one rule, named by its filename. A validator setup is correct when it accepts every
  valid file and rejects every invalid one.

Copy the schema file into your repository, or fetch it from its `$id`. The prose standard is
[v1](../docs/RESPONSE-ENVELOPE-STANDARD-v1.md), [v1.1](../docs/RESPONSE-ENVELOPE-STANDARD-v1.1.md)
and [v1.2](../docs/RESPONSE-ENVELOPE-STANDARD-v1.2.md); where the schema and the prose differ, the
prose wins.

## Validate a tool result

`result.json` holds the `structuredContent` of one tool result; `result` is the same object in
memory.

**Command line, nothing to install**

```bash
uvx check-jsonschema --schemafile envelope-v1.2.schema.json result.json
npx ajv-cli validate --spec=draft2020 --strict-types=false -s envelope-v1.2.schema.json -d result.json
```

**Python** (`pip install jsonschema`)

```python
jsonschema.validate(result, json.load(open("envelope-v1.2.schema.json")))
```

**JavaScript / TypeScript** (`npm install ajv`)

```js
import Ajv2020 from "ajv/dist/2020.js";
const ok = new Ajv2020({ strictTypes: false }).validate(JSON.parse(fs.readFileSync("envelope-v1.2.schema.json", "utf8")), result);
```

**R** (`install.packages("jsonvalidate")`)

```r
jsonvalidate::json_validate("result.json", "envelope-v1.2.schema.json", engine = "ajv")
```

**Go** (`go get github.com/santhosh-tekuri/jsonschema/v6`)

```go
err := jsonschema.NewCompiler().MustCompile("envelope-v1.2.schema.json").Validate(result)
```

In Go, decode `result` with `jsonschema.UnmarshalJSON`. The schema uses no `format` keyword, so
no validator needs a format plug-in.

## Get `structuredContent` from a tool result

A `tools/call` response is `{"content": [...], "structuredContent": {...}, "isError": false}`.
Take `structuredContent`. If a client hides it, parse the first `content` block of type `text`
as JSON: it mirrors the same object.

```python
envelope = result.get("structuredContent") or json.loads(result["content"][0]["text"])
```

`isError` lives beside the envelope, so the schema cannot see it. It must be `true` exactly when
`success` is `false`.

## A pytest test for a server

Needs `pytest-asyncio` with `asyncio_mode = "auto"`. Any MCP client works the same way.

```python
import json
from pathlib import Path

import jsonschema
import pytest
from fastmcp import Client

from my_server import mcp  # your FastMCP server

SCHEMA = json.loads(Path("tests/envelope-v1.2.schema.json").read_text())


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("get_gene", {"symbol": "BRAF"}), ("get_gene", {"symbol": "NOT_A_GENE"})],
)
async def test_tool_returns_the_envelope(tool, arguments):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments, raise_on_error=False)
    envelope = result.structured_content
    jsonschema.validate(envelope, SCHEMA)
    assert result.is_error == (not envelope["success"])
```

## What the schema does not check

- It applies to tools that return upstream data. Tools that only orient a client (capabilities,
  health, diagnostics) are outside the v1.2 provenance rule and fail it without the three fields.
- `error_code` is any non-empty string: v1 lists six codes and v1.1 allows backend-specific
  limit-breach codes.
- Rules a schema cannot express: `isError`, the `content` mirror, Unicode sanitation, size
  limits, that `retrieved_at` is the upstream fetch time, and that prose is not duplicated outside
  its `untrusted_text` object.
- `retrieved_at` must end in `Z` or `+00:00`.
