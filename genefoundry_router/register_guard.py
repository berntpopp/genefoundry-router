"""Guard the OAuth ``/register`` route against non-JSON bodies (issue #271)."""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


class RejectNonJsonBody:
    """Answer a non-JSON ``/register`` body with RFC 7591 400, not a 500 (issue #271).

    ponytail: mcp<2's handler catches only ValidationError; drop this on mcp 2.x.
    """

    def __init__(self, endpoint: ASGIApp) -> None:
        self._endpoint = endpoint

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("method") != "POST":
            return await self._endpoint(scope, receive, send)
        request = Request(scope, receive)
        body = await request.body()
        try:
            json.loads(body)
        except ValueError:
            error = {"error": "invalid_client_metadata", "error_description": "body is not JSON"}
            return await JSONResponse(error, status_code=400)(scope, receive, send)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self._endpoint(scope, replay, send)
