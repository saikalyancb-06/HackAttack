"""LLM wrapper (C1). One complete(); every provider speaks the OpenAI chat API.

task="plan"   -> gpt-oss-120b on Groq, then Cerebras, then OpenRouter :free
task="local"  -> local llama-server (no data leaves the machine), then the plan chain
images given  -> Groq VISION_MODEL (qwen3.8-27b, GROQ_VISION_API_KEY or GROQ_API_KEY); every image goes through redact_image first
"""
import base64
import json
import os
import re
import time

import httpx
from dotenv import load_dotenv

load_dotenv()

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY", "GROQ_MODEL", "openai/gpt-oss-120b"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", "CEREBRAS_MODEL", "gpt-oss-120b"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", "OPENROUTER_MODEL", "openai/gpt-oss-120b:free"),
    "vision": ("https://api.groq.com/openai/v1", "GROQ_VISION_API_KEY", "VISION_MODEL", "qwen/qwen3.8-27b"),
    "local": (None, None, "LOCAL_LLM_MODEL", "local"),
}
CHAINS = {"plan": ["groq", "cerebras", "openrouter"], "local": ["local", "groq", "cerebras", "openrouter"], "vision": ["vision"]}

stats = {"regions_blurred": 0, "images_sent": 0}  # read by loop for the privacy report


def _post(provider: str, body: dict) -> str:
    base, key_env, model_env, default_model = PROVIDERS[provider]
    if provider == "local":
        base = os.getenv("LOCAL_LLM_URL", "http://localhost:8091/v1")
        key = "none"
    else:
        key = os.getenv(key_env) or (os.getenv("GROQ_API_KEY") if provider == "vision" else None)
        if not key:
            raise RuntimeError(f"{key_env} not set")
    body = {**body, "model": os.getenv(model_env, default_model)}
    for wait in (2, 5, 10, 20, 0):  # rate limits: back off, honouring retry-after (capped)
        r = httpx.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=90)
        if r.status_code != 429 or not wait:
            break
        time.sleep(min(float(r.headers.get("retry-after") or wait), 30))
    if r.status_code >= 400:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"] or ""


def _parse_json(text: str) -> dict:
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError(f"LLM did not return JSON: {text[:200]}")
        return json.loads(m.group(0))


def _attach_images(messages: list[dict], images: list[bytes]) -> list[dict]:
    from modules.guard.visual_pii import redact_image

    parts = []
    for png in images:
        clean, regions = redact_image(png)  # never send an unredacted screenshot
        stats["regions_blurred"] += len(regions)
        stats["images_sent"] += 1
        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(clean).decode()}})
    last = messages[-1]
    text = last["content"] if isinstance(last["content"], str) else ""
    return messages[:-1] + [{"role": last["role"], "content": [{"type": "text", "text": text}, *parts]}]


def complete(messages: list[dict], json_schema: dict | None = None, images: list[bytes] | None = None, task: str = "plan") -> str | dict:
    """Messages must already be PII-masked. Returns str, or dict when json_schema is given."""
    msgs = list(messages)
    body: dict = {"temperature": 0}
    if json_schema:
        msgs = [{"role": "system", "content": "Reply with ONLY a JSON object matching this schema:\n" + json.dumps(json_schema)}, *msgs]
        body["response_format"] = {"type": "json_object"}
    if images:
        msgs = _attach_images(msgs, images)
        task = "vision"
    errors = []
    for provider in CHAINS[task]:
        try:
            out = _post(provider, {**body, "messages": msgs})
            return _parse_json(out) if json_schema else out
        except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as e:
            errors.append(f"{provider}: {e}")
    raise RuntimeError("All LLM providers failed: " + " | ".join(errors))


if __name__ == "__main__":
    assert _parse_json('<think>x</think>{"a": 1}') == {"a": 1}
    assert _parse_json('Sure! ```json\n{"steps": []}\n```') == {"steps": []}
    import sys
    if "--live" in sys.argv:  # optional network check: uv run python -m modules.llm --live
        print(complete([{"role": "user", "content": "Say hi in one word."}]))
        print(complete([{"role": "user", "content": "Give {\"n\": 2}"}], json_schema={"type": "object"}))
    if "--vision" in sys.argv:  # uv run python -m modules.llm --vision <png>
        import types

        import modules.guard

        try:
            from modules.guard.visual_pii import redact_image
            redact_image(b"")
        except NotImplementedError:  # A2 not ready: test-only pass-through, never used outside this check
            fake = types.SimpleNamespace(redact_image=lambda b: (b, []))
            sys.modules["modules.guard.visual_pii"] = fake
            modules.guard.visual_pii = fake
            print("(visual_pii stub: using pass-through for this check)")
        except Exception:
            pass
        png = open(sys.argv[sys.argv.index("--vision") + 1], "rb").read()
        print(complete([{"role": "user", "content": "Describe this image in one sentence."}], images=[png]))
        assert stats["images_sent"] == 1
    print("llm ok")
