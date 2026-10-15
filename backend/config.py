"""Settings loaded once from the environment and an optional .env at the repo root."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


_DOTENV = ROOT / ".env"
_dotenv_keys: set[str] = set()   # keys that came from .env (may be refreshed when the file changes)
_dotenv_mtime = 0.0


def _load_dotenv(path: Path) -> None:
    global _dotenv_mtime
    if not path.exists():
        return
    _dotenv_mtime = path.stat().st_mtime
    seen = set()
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip('"').strip("'")
        if key and value and (key not in os.environ or key in _dotenv_keys):
            os.environ[key] = value
            _dotenv_keys.add(key)
            seen.add(key)
    for gone in _dotenv_keys - seen:  # removed from .env -> forget it
        os.environ.pop(gone, None)
    _dotenv_keys.intersection_update(seen)


def refresh_dotenv() -> None:
    """Re-read .env if it changed (lets the user rotate API keys without restarting)."""
    try:
        if _DOTENV.exists() and _DOTENV.stat().st_mtime != _dotenv_mtime:
            _load_dotenv(_DOTENV)
    except OSError:
        pass


_load_dotenv(_DOTENV)


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


DATA_DIR = (ROOT / env("DATA_DIR", "data")).resolve() if not Path(env("DATA_DIR", "data")).is_absolute() else Path(env("DATA_DIR"))
PROJECTS_DIR = DATA_DIR / "projects"
CACHE_DIR = DATA_DIR / "cache"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LLM_PROVIDER = env("LLM_PROVIDER", "groq")
LLM_MODEL = env("LLM_MODEL", "openai/gpt-oss-120b")
LLM_FAST_MODEL = env("LLM_FAST_MODEL", "openai/gpt-oss-20b")
LLM_ALT_MODEL = env("LLM_ALT_MODEL", "qwen/qwen3.8-27b")
LLM_VISION_MODEL = env("LLM_VISION_MODEL", "")  # none on this Groq account: screenshots never go to the LLM
GUARD_MODEL = env("GUARD_MODEL", "openai/gpt-oss-safeguard-20b")
PROMPT_GUARD_MODEL = env("PROMPT_GUARD_MODEL", "meta-llama/llama-prompt-guard-2-86m")
LLM_BASE_URL = env("LLM_BASE_URL", "https://api.groq.com/openai/v1")


def llm_api_keys() -> list[str]:
    """All configured Groq keys: GROQ_API_KEY, GROQ_API_KEY1..9, LLM_API_KEY (read at call time)."""
    refresh_dotenv()
    names = ["GROQ_API_KEY"] + [f"GROQ_API_KEY{i}" for i in range(1, 10)] + ["GROQ_API_KEY_" + str(i) for i in range(1, 10)] + ["LLM_API_KEY"]
    keys = []
    for n in names:
        v = env(n).strip()
        if v and v not in keys:
            keys.append(v)
    return keys


def llm_api_key() -> str:
    keys = llm_api_keys()
    return keys[0] if keys else ""


TAVILY_API_KEY = env("TAVILY_API_KEY")
PROTECTED_PATHS = [p for p in env("PROTECTED_PATHS", r"C:\Windows;C:\Program Files;C:\Program Files (x86)").split(";") if p]
MCP_REGISTRY_URL = env("MCP_REGISTRY_URL", "https://registry.modelcontextprotocol.io")
RETRY_BACKOFF_SCALE = float(env("RETRY_BACKOFF_SCALE", "1.0"))
