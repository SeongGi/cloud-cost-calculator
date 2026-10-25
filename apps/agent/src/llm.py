"""Local-only chat model: Ollama on this machine, never an external LLM API."""

import logging
import os
from urllib.parse import urlparse

from langchain_ollama import ChatOllama

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "qwen3.5:latest"
# Hosts that resolve to this machine. host.docker.internal is the host as seen
# from a container. Anything else could be a remote LLM and is refused.
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


def ollama_base_url() -> str:
    url = os.environ.get("OLLAMA_BASE_URL", DEFAULT_BASE_URL).strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in LOCAL_HOSTS:
        raise ValueError(
            f"OLLAMA_BASE_URL must point at local Ollama ({sorted(LOCAL_HOSTS)}); got {url!r}"
        )
    return url


def ollama_model() -> str:
    """Ollama can forward "*-cloud" / ":cloud" models to ollama.com; only local weights are allowed."""
    name = os.environ.get("OLLAMA_MODEL", DEFAULT_MODEL).strip()
    if "cloud" in name.lower().replace(".", "-").replace(":", "-").split("-"):
        raise ValueError(f"OLLAMA_MODEL {name!r} is a cloud model; use a locally pulled model")
    return name


def build_model() -> ChatOllama:
    return ChatOllama(
        base_url=ollama_base_url(),
        model=ollama_model(),
        temperature=0,
        seed=0,
        reasoning=False,
        num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
        keep_alive=os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
    )


def warm_up(model: ChatOllama) -> None:
    """Load the model into memory so the first visitor does not wait for it."""
    try:
        model.invoke("ok")
        logger.info("Ollama model %s warmed up", model.model)
    except Exception:
        logger.exception("Ollama warm-up failed; the first request will load the model")
