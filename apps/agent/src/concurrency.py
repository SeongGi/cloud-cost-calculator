"""Global cap on concurrent agent runs so local Ollama (and 16 GB of RAM) is never swamped.
Pure ASGI so a streaming response holds its slot until the stream ends."""

import asyncio
import json


class RunLimiter:
    def __init__(self, app, max_running: int = 2, max_waiting: int = 4, path: str = "/"):
        self.app = app
        self.path = path
        self.max_waiting = max_waiting
        self.slots = asyncio.Semaphore(max_running)
        self.waiting = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != self.path:
            return await self.app(scope, receive, send)
        if self.slots.locked() and self.waiting >= self.max_waiting:
            body = json.dumps({"error": "지금 계산 요청이 많습니다. 잠시 후 다시 시도해 주세요."}).encode()
            await send({"type": "http.response.start", "status": 429,
                        "headers": [(b"content-type", b"application/json"), (b"retry-after", b"30")]})
            return await send({"type": "http.response.body", "body": body})
        self.waiting += 1
        try:
            await self.slots.acquire()
        finally:
            self.waiting -= 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.slots.release()
