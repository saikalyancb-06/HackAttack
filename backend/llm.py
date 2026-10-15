"""LLM access (Groq, OpenAI-compatible). complete(messages, json_schema=None, images=None) -> str|dict.

Privacy boundary: every message is PII-masked here (project vault placeholders) and re-scanned
before it leaves the machine; screenshots are blurred with guard.visual_pii first. If anything
still looks like PII after masking the call is refused, so raw_pii_to_llm stays 0 by construction.
Responses are unmasked back to real values only for the caller (tools need real addresses).
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from typing import Any

from backend import config
from backend.guard import pii

_state = {"last_error": None, "calls": 0, "key_idx": 0}
_bad_keys: set[str] = set()      # keys Groq rejected (401); never retried until reset()
_lock = threading.Lock()


class LLMUnavailable(RuntimeError):
    pass


def _good_keys() -> list[str]:
    return [k for k in config.llm_api_keys() if k not in _bad_keys]


def available() -> bool:
    return bool(_good_keys()) and config.env("LLM_DISABLED") != "1"


def status() -> dict:
    keys = config.llm_api_keys()
    return {"provider": config.LLM_PROVIDER, "model": config.LLM_MODEL, "configured": bool(keys),
            "keys": len(keys), "keys_ok": len([k for k in keys if k not in _bad_keys]),
            "available": available(), "last_error": _state["last_error"], "calls": _state["calls"]}


def reset() -> None:
    _bad_keys.clear()
    _state["last_error"] = None


_clients: dict = {}


def _current_key() -> str:
    keys = _good_keys()
    if not keys:
        raise LLMUnavailable(_state["last_error"] or "no GROQ_API_KEY configured")
    return keys[_state["key_idx"] % len(keys)]


def _rotate_key() -> bool:
    """Switch to the next key (each Groq key has its own per-minute token budget). False if only one key."""
    with _lock:
        n = len(_good_keys())
        _state["key_idx"] = (_state["key_idx"] + 1) % max(1, n)
        return n > 1


def _get_client():
    from openai import OpenAI
    key = _current_key()
    if key not in _clients:
        _clients[key] = OpenAI(api_key=key, base_url=config.LLM_BASE_URL, timeout=60, max_retries=0)
    return _clients[key]


def _mask_messages(messages: list[dict], project_id: str | None) -> tuple[list[dict], dict, int]:
    from backend.ledger import db
    mapping: dict = db.vault(project_id) if project_id else {}
    before = len(mapping)
    out = []
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str):
            if project_id:
                content = db.mask_text(project_id, content)
            else:
                content, mapping = pii.mask(content, mapping)
            if pii.contains_pii(content):
                raise RuntimeError("refusing LLM call: PII still present after masking")
        out.append({"role": m.get("role", "user"), "content": content})
    if project_id:
        mapping = db.vault(project_id)
    return out, mapping, len(mapping) - before


def _attach_images(messages: list[dict], images: list[bytes], project_id: str | None, run_id: str | None) -> list[dict]:
    from backend.guard.visual_pii import redact_image
    parts = []
    for png in images:
        blurred, regions = redact_image(png)
        if project_id and run_id and regions:
            from backend.ledger import db
            db.bump_privacy(project_id, run_id, "regions_blurred", len(regions))
        parts.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(blurred).decode()}})
    last = messages[-1]
    messages[-1] = {"role": last["role"], "content": [{"type": "text", "text": last["content"]}] + parts}
    return messages


def _parse_json(text: str) -> Any:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", text, re.S)
        if m:
            return json.loads(m.group(1))
        raise


def complete(messages: list[dict], json_schema: dict | None = None, images: list[bytes] | None = None, *,
             project_id: str | None = None, run_id: str | None = None, model: str | None = None,
             temperature: float = 0.2, max_tokens: int = 4096, unmask_output: bool = True,
             reasoning_effort: str = "low") -> Any:
    if not available():
        raise LLMUnavailable(_state["last_error"] or "no GROQ_API_KEY configured")
    msgs, mapping, _ = _mask_messages(messages, project_id)
    if json_schema is not None:
        hint = ("Respond with a single JSON object only (no prose, no markdown) that matches this JSON schema:\n"
                + json.dumps(json_schema)[:6000])
        msgs = [{"role": "system", "content": hint}] + msgs
    if images and not config.LLM_VISION_MODEL:
        images = None  # no vision model configured: never send screenshots, UI tree text only
    models = [model or (config.LLM_VISION_MODEL if images else config.LLM_MODEL)]
    if model is None and not images:
        for alt in (config.LLM_ALT_MODEL, config.LLM_FAST_MODEL):
            if alt and alt not in models:
                models.append(alt)
    if images:
        msgs = _attach_images(msgs, images, project_id, run_id)

    last_err: Exception | None = None
    for m in models:
        for attempt in range(3):
            try:
                kwargs: dict = {"model": m, "messages": msgs, "temperature": temperature, "max_tokens": max_tokens}
                if json_schema is not None:
                    kwargs["response_format"] = {"type": "json_object"}
                if "gpt-oss" in m:
                    kwargs["reasoning_effort"] = reasoning_effort
                with _lock:
                    _state["calls"] += 1
                resp = _get_client().chat.completions.create(**kwargs)
                text = resp.choices[0].message.content or ""
                if project_id:
                    from backend.ledger import db
                    db.audit(project_id, run_id, "llm_call", {"model": m, "json": json_schema is not None,
                                                              "prompt_tokens": getattr(resp.usage, "prompt_tokens", None),
                                                              "completion_tokens": getattr(resp.usage, "completion_tokens", None)})
                out: Any = _parse_json(text) if json_schema is not None else text
                _state["last_error"] = None
                return pii.unmask(out, mapping) if unmask_output else out
            except json.JSONDecodeError as e:
                last_err = e
                msgs = msgs + [{"role": "user", "content": "That was not valid JSON. Reply with the JSON object only."}]
            except Exception as e:
                last_err = e
                name = type(e).__name__
                msg = str(e)
                _state["last_error"] = f"{name}: {msg[:200]}"
                if "AuthenticationError" in name or "invalid_api_key" in msg or "401" in msg:
                    try:
                        _bad_keys.add(_current_key())
                    except LLMUnavailable:
                        pass
                    if available():
                        continue  # another key is still good
                    raise LLMUnavailable(f"Groq rejected every configured API key: {msg[:200]}") from e
                if "413" in msg or "Request too large" in msg:
                    raise RuntimeError(f"prompt too large for the model's token budget: {msg[:160]}") from e
                if "RateLimit" in name or "429" in msg:
                    if attempt < 2 and _rotate_key():
                        continue  # next key has its own budget: retry immediately
                    wait = 2.0 * (attempt + 1)
                    m2 = re.search(r"try again in ([\d.]+)s", msg)
                    if m2:
                        wait = min(20.0, float(m2.group(1)) + 0.5)
                    if attempt == 2:
                        break  # next model
                    time.sleep(wait)
                    continue
                if "NotFound" in name or "model_not_found" in msg or "decommissioned" in msg or "400" in msg and "model" in msg:
                    break  # try the next model
                if attempt < 2:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
    raise RuntimeError(f"LLM call failed: {last_err}")


_SAFETY_POLICY = """You are the safety classifier for an autonomous computer-use agent that can browse the web,
edit files and spreadsheets, draft emails and run shell commands on the user's PC.
Classify the REQUEST (and the optional ACTION) against this policy.
VIOLATION (1) if it seeks to: create or run malware, ransomware, keyloggers or spyware; gain unauthorized access to accounts,
systems or networks; steal or harvest credentials or personal data; commit fraud, phishing, scams or impersonation; harass,
stalk, dox or threaten people; send spam at scale; obtain weapons or cause physical harm; self-harm; destroy, encrypt or wipe
data or systems the user is not clearly entitled to change; evade security controls, licensing or law enforcement.
SAFE (0): normal research, office work, file management of the user's own files, coding, admin of the user's own machine,
email drafting, studying security concepts defensively.
Respond with JSON only: {"violation": 0 or 1, "category": "<short category>", "confidence": 0.0-1.0, "rationale": "<one sentence>"}"""


def guard_check(text: str, project_id: str | None = None) -> dict:
    """Policy safety classifier (gpt-oss-safeguard on Groq).
    Returns {"safe": bool, "category": str, "confidence": float, "rationale": str, "available": bool}."""
    if not available():
        return {"safe": True, "category": "", "confidence": 0.0, "rationale": "", "available": False}
    try:
        out = complete([{"role": "system", "content": _SAFETY_POLICY}, {"role": "user", "content": text[:6000]}],
                       json_schema={"type": "object"}, project_id=project_id, model=config.GUARD_MODEL,
                       temperature=0.0, max_tokens=600)
        return {"safe": not int(out.get("violation", 0)), "category": str(out.get("category", "")),
                "confidence": float(out.get("confidence", 0.8) or 0.8), "rationale": str(out.get("rationale", "")),
                "available": True}
    except Exception as e:
        return {"safe": True, "category": "", "confidence": 0.0, "rationale": "", "available": False, "error": str(e)[:200]}


def prompt_injection_score(text: str) -> float | None:
    """Llama Prompt Guard 2 (86M) on Groq: probability that `text` is a jailbreak / prompt injection."""
    if not available():
        return None
    try:
        masked, _ = pii.mask(text[:2000])
        resp = _get_client().chat.completions.create(model=config.PROMPT_GUARD_MODEL,
                                                     messages=[{"role": "user", "content": masked}])
        return float((resp.choices[0].message.content or "0").strip())
    except Exception:
        return None


if __name__ == "__main__":
    msgs, mapping, n = _mask_messages([{"role": "user", "content": "email me at a@b.com"}], None)
    assert "a@b.com" not in msgs[0]["content"] and n == 1
    assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    print("llm self-check OK; status:", status())
