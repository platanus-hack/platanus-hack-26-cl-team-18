"""Serve only offer pages and redemption on the judge's network."""

from __future__ import annotations

import argparse
import re
from contextlib import asynccontextmanager

import httpx
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route

_CODE = re.compile(r"[A-Za-z0-9]{4,12}")


def create_relay(backend: str) -> Starlette:
    @asynccontextmanager
    async def lifespan(app: Starlette):
        async with httpx.AsyncClient(base_url=backend, timeout=3, trust_env=False) as client:
            app.state.backend = client
            yield

    async def forward(request: Request) -> Response:
        code = request.path_params["code"]
        if not _CODE.fullmatch(code):
            return Response(status_code=404)
        target = f"/r/{code}"
        content = bytearray()
        headers: dict[str, str] = {}
        if request.method == "POST":
            target += "/redeem"
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return Response(status_code=415)
            async for chunk in request.stream():
                if len(content) + len(chunk) > 1024:
                    return Response(status_code=413)
                content.extend(chunk)
            headers["content-type"] = "application/json"
        try:
            client: httpx.AsyncClient = request.app.state.backend
            result = await client.request(
                request.method, target, content=bytes(content), headers=headers
            )
        except httpx.RequestError:
            return Response("La pantalla no está disponible. Intenta otra vez.", status_code=503)
        return Response(
            result.content,
            status_code=result.status_code,
            headers={
                "content-type": result.headers.get("content-type", "text/plain"),
                "cache-control": "no-store",
            },
        )

    return Starlette(
        routes=[
            Route("/r/{code}", forward, methods=["GET"]),
            Route("/r/{code}/redeem", forward, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:8018")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8022)
    args = parser.parse_args()
    uvicorn.run(create_relay(args.backend), host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
