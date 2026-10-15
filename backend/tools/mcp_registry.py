"""MCP discovery: what is installed on this PC, what the official registry offers, and install-on-approval.

scan_installed()   -> [{"name","command","args","env_keys","source"}]   (env VALUES are never returned)
search_registry()  -> [{"name","description","install","repo","score"}]  (cached; offline -> cache + seed list)
best_for(need)     -> {"installed": dict|None, "better": dict|None}
request_install(project_id, candidate) -> approval_id   (approval kind "mcp_install")
install(candidate) -> {"ok","name"}   ONLY after approval; appends to data/mcp_servers.json and reconnects
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path

from backend import config

_CACHE = config.CACHE_DIR / "registry"
_CACHE.mkdir(parents=True, exist_ok=True)

# Offline seed so discovery still works with no network (well-known reference servers).
_SEED = [
    {"name": "io.github.modelcontextprotocol/filesystem", "description": "Secure file operations: read, write, search, move files in allowed directories",
     "install": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"]}, "repo": "https://github.com/modelcontextprotocol/servers"},
    {"name": "io.github.modelcontextprotocol/fetch", "description": "Fetch web pages and convert HTML to markdown for LLM use",
     "install": {"command": "uvx", "args": ["mcp-server-fetch"]}, "repo": "https://github.com/modelcontextprotocol/servers"},
    {"name": "io.github.modelcontextprotocol/memory", "description": "Knowledge graph based persistent memory",
     "install": {"command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]}, "repo": "https://github.com/modelcontextprotocol/servers"},
    {"name": "io.github.cursortouch/windows-mcp", "description": "Control Windows desktop apps: UI tree, click, type, shortcuts, launch apps",
     "install": {"command": "uvx", "args": ["windows-mcp"]}, "repo": "https://github.com/CursorTouch/Windows-MCP"},
    {"name": "io.github.microsoft/playwright-mcp", "description": "Browser automation with Playwright: navigate, click, fill forms, screenshots",
     "install": {"command": "npx", "args": ["-y", "@playwright/mcp@latest"]}, "repo": "https://github.com/microsoft/playwright-mcp"},
    {"name": "io.github.negokaz/excel-mcp-server", "description": "Read and write Excel spreadsheets (xlsx) without Office installed",
     "install": {"command": "npx", "args": ["-y", "@negokaz/excel-mcp-server"]}, "repo": "https://github.com/negokaz/excel-mcp-server"},
    {"name": "io.github.github/github-mcp-server", "description": "GitHub issues, pull requests, repositories and code search",
     "install": {"command": "docker", "args": ["run", "-i", "--rm", "ghcr.io/github/github-mcp-server"]}, "repo": "https://github.com/github/github-mcp-server"},
    {"name": "io.github.modelcontextprotocol/sqlite", "description": "Query and analyze SQLite databases",
     "install": {"command": "uvx", "args": ["mcp-server-sqlite"]}, "repo": "https://github.com/modelcontextprotocol/servers"},
]


def _cfg_sources() -> list[tuple[str, Path]]:
    appdata = Path(os.environ.get("APPDATA", ""))
    home = Path.home()
    return [("claude_desktop", appdata / "Claude" / "claude_desktop_config.json"),
            ("cursor", home / ".cursor" / "mcp.json"),
            ("vscode", config.ROOT / ".vscode" / "mcp.json"),
            ("claude_code", home / ".claude.json"),
            ("ours", config.DATA_DIR / "mcp_servers.json")]


def scan_installed() -> list[dict]:
    out = []
    for source, path in _cfg_sources():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        servers = data.get("mcpServers") or data.get("servers") or (data if source == "ours" else {})
        if not isinstance(servers, dict):
            continue
        for name, spec in servers.items():
            if not isinstance(spec, dict) or not (spec.get("command") or spec.get("url")):
                continue
            out.append({"name": name, "command": spec.get("command") or spec.get("url"),
                        "args": [a for a in spec.get("args", []) if isinstance(a, str)][:12],
                        "env_keys": sorted((spec.get("env") or {}).keys()), "source": source})
    from backend.tools.mcp_client import _DEFAULT
    for name, spec in _DEFAULT.items():
        if not any(s["name"] == name for s in out):
            out.append({"name": name, "command": "python", "args": spec["args"], "env_keys": [], "source": "builtin"})
    return out


def _embed(text: str) -> list[float]:
    from backend.kg.rag import embed
    return embed(text)


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / ((math.sqrt(sum(x * x for x in a)) or 1) * (math.sqrt(sum(y * y for y in b)) or 1))


def _normalize_registry(item: dict) -> dict | None:
    s = item.get("server", item)
    name = s.get("name")
    if not name:
        return None
    install = None
    for pkg in s.get("packages") or []:
        ident, rt = pkg.get("identifier"), (pkg.get("registryType") or pkg.get("registry_name") or "").lower()
        if rt == "npm":
            install = {"command": "npx", "args": ["-y", ident]}
        elif rt == "pypi":
            install = {"command": "uvx", "args": [ident]}
        elif rt in ("oci", "docker"):
            install = {"command": "docker", "args": ["run", "-i", "--rm", ident]}
        if install:
            break
    return {"name": name, "description": s.get("description", ""), "install": install,
            "repo": (s.get("repository") or {}).get("url", "")}


def search_registry(need: str, k: int = 5) -> list[dict]:
    key = hashlib.sha1(need.lower().encode()).hexdigest()[:16]
    cache_file = _CACHE / f"{key}.json"
    items: list[dict] = []
    try:
        import requests
        r = requests.get(f"{config.MCP_REGISTRY_URL}/v0/servers", params={"search": need, "limit": 20}, timeout=8)
        r.raise_for_status()
        items = [x for x in (_normalize_registry(i) for i in r.json().get("servers", [])) if x]
        cache_file.write_text(json.dumps(items), encoding="utf-8")
    except Exception:
        if cache_file.exists():
            items = json.loads(cache_file.read_text(encoding="utf-8"))
    by_name = {i["name"]: i for i in items}
    for s in _SEED:
        by_name.setdefault(s["name"], s)
    q = _embed(need)
    scored = [{**i, "score": round(_cos(q, _embed(f"{i['name']} {i['description']}")), 3)} for i in by_name.values()]
    scored.sort(key=lambda x: -x["score"])
    return [s for s in scored if s.get("install")][:k]


def best_for(need: str) -> dict:
    from backend.tools import mcp_client
    q = _embed(need)
    best_inst, best_score = None, 0.0
    for t in mcp_client.list_tools():
        sc = _cos(q, _embed(f"{t['name']} {t['description']}"))
        if sc > best_score:
            best_inst, best_score = {**t, "score": round(sc, 3)}, sc
    installed_names = {s["name"] for s in scan_installed()}
    better = None
    for cand in search_registry(need, k=5):
        short = cand["name"].split("/")[-1]
        if short in installed_names or cand["name"] in installed_names:
            continue
        if cand["score"] > best_score + 0.15 and cand["score"] > 0.2:
            better = cand
        break
    return {"installed": best_inst, "better": better}


def request_install(project_id: str, candidate: dict, run_id: str | None = None) -> str:
    from backend.ledger import db
    cmd = " ".join([candidate["install"]["command"]] + candidate["install"].get("args", []))
    summary = {"server": candidate["name"], "description": candidate.get("description", ""), "repo": candidate.get("repo", ""),
               "install_command": cmd, "candidate": candidate}
    aid = db.create_approval(project_id, run_id or "", "", summary, kind="mcp_install")
    db.audit(project_id, run_id, "mcp_install_requested", {"server": candidate["name"], "command": cmd})
    return aid


def install(candidate: dict) -> dict:
    from backend.tools import mcp_client
    short = re.sub(r"[^a-z0-9_\-]", "_", candidate["name"].split("/")[-1].lower())
    spec = {"command": candidate["install"]["command"], "args": candidate["install"].get("args", []),
            "source": "registry", "registry_name": candidate["name"]}
    mcp_client.save_server(short, spec)
    ok = mcp_client.connect(short, timeout=120)
    return {"ok": bool(ok), "name": short, "status": mcp_client.status().get(short)}


if __name__ == "__main__":
    inst = scan_installed()
    assert any(s["name"] == "office_com" for s in inst), inst
    assert all("env" not in s for s in inst)
    hits = search_registry("control windows desktop applications click and type", k=3)
    assert hits and hits[0]["install"], hits
    print("tools.mcp_registry self-check OK:", [h["name"] for h in hits], "| installed:", [s["name"] for s in inst])
