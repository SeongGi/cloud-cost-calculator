"""Cloud cost calculator agent: Gemini (when an admin saved a key) with local Ollama fallback,
plus deterministic pricing tools."""

import asyncio
import logging
import os
import warnings
from contextlib import asynccontextmanager

from ag_ui_langgraph import add_langgraph_fastapi_endpoint
from copilotkit import CopilotKitMiddleware, LangGraphAGUIAgent
from dotenv import load_dotenv
from fastapi import FastAPI
from langchain.agents import create_agent

from pricing.paths import refresh_enabled
from pricing.refresh import start_scheduler
from src import settings
from src.admin import api as admin_api
from src.compact import CompactToolResults
from src.concurrency import RunLimiter
from src.cost_tool import estimate_cost
from src.llm import build_model, warm_up
from src.prompt import SYSTEM_PROMPT
from src.guard import ResponseGuard
from src.persistent_saver import PersistentMemorySaver, default_path
from src.router import LLMRouter

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOOLS = [estimate_cost]

model = build_model()
graph = create_agent(
    model=model,
    tools=TOOLS,
    middleware=[CopilotKitMiddleware(), CompactToolResults(), ResponseGuard(), LLMRouter()],
    system_prompt=SYSTEM_PROMPT,
    checkpointer=(memory := PersistentMemorySaver(default_path())),
)



@asynccontextmanager
async def lifespan(_: FastAPI):
    # With Gemini as the primary model the local one loads only when needed (saves memory).
    if os.getenv("OLLAMA_WARMUP", "true") == "true" and not settings.load().gemini_active:
        asyncio.get_running_loop().run_in_executor(None, warm_up, model)
    if refresh_enabled():
        start_scheduler(int(os.getenv("PRICE_REFRESH_HOUR_KST", "4")))
    try:
        yield
    finally:
        memory.close()  # keep the last answers of every conversation across restarts


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RunLimiter, max_running=int(os.getenv("MAX_CONCURRENT_RUNS", "2")),
                   max_waiting=int(os.getenv("MAX_WAITING_RUNS", "4")))


app.include_router(admin_api)


@app.get("/health")
def health():
    return {"status": "ok"}


add_langgraph_fastapi_endpoint(
    app=app,
    agent=LangGraphAGUIAgent(
        name="cost_agent",
        description="클라우드 비용 계산기",
        graph=graph,
    ),
    path="/",
)

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8123")))
