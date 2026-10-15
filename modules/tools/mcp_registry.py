"""MCP discovery + install (A1). CONTRACTS 7c + CONTRACTS 11."""

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any
import httpx

REGISTRY_URL = os.environ.get("MCP_REGISTRY_URL", "https://registry.modelcontextprotocol.io")
CACHE_DIR = Path("data/cache/registry")
MCP_SERVERS_JSON = Path("data/mcp_servers.json")


def _get_config_sources() -> list[tuple[str, Path]]:
    """Return list of (source_name, path) to inspect for installed MCPs."""
    sources: list[tuple[str, Path]] = []

    appdata = os.environ.get("APPDATA")
    if appdata:
        sources.append(("claude_desktop", Path(appdata) / "Claude" / "claude_desktop_config.json"))

    home = Path.home()
    sources.append(("cursor", home / ".cursor" / "mcp.json"))
    sources.append(("vscode", Path(".vscode") / "mcp.json"))
    sources.append(("data/mcp_servers.json", MCP_SERVERS_JSON))

    return sources


def _parse_config_file(path: Path, source_name: str) -> list[dict]:
    """Parse an MCP config file, extracting server definitions while hiding env values."""
    if not path.is_file():
        return []

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []

    servers = data.get("mcpServers") or data.get("servers") or {}
    if not isinstance(servers, dict):
        return []

    results: list[dict] = []
    for name, srv in servers.items():
        if not isinstance(srv, dict):
            continue
        command = srv.get("command", "")
        args = srv.get("args", [])
        if not isinstance(args, list):
            args = [str(args)]
        env = srv.get("env", {})
        # Non-negotiable security: env VALUES are NEVER returned, only keys
        env_keys = list(env.keys()) if isinstance(env, dict) else []

        results.append({
            "name": name,
            "command": str(command),
            "args": [str(a) for a in args],
            "env_keys": env_keys,
            "source": source_name,
        })

    return results


def scan_installed(extra_paths: list[tuple[str, Path]] | None = None) -> list[dict]:
    """Scan configured locations for installed MCP servers. Env values are never returned."""
    sources = list(extra_paths) if extra_paths else _get_config_sources()
    installed: list[dict] = []
    seen_names: set[str] = set()

    for source_name, path in sources:
        for srv in _parse_config_file(path, source_name):
            if srv["name"] not in seen_names:
                seen_names.add(srv["name"])
                installed.append(srv)

    return installed


def _compute_similarity(query: str, text: str) -> float:
    """Compute text similarity score in [0.0, 1.0] between query and candidate text."""
    q_words = set(re.findall(r"\w+", query.lower()))
    t_words = set(re.findall(r"\w+", text.lower()))
    if not q_words or not t_words:
        return 0.0

    intersection = q_words.intersection(t_words)
    jaccard = len(intersection) / len(q_words.union(t_words))
    coverage = len(intersection) / len(q_words)
    phrase_bonus = 0.2 if query.lower() in text.lower() else 0.0

    return min(1.0, round(0.35 * jaccard + 0.65 * coverage + phrase_bonus, 4))


# Default known MCP registry catalog for offline fallback
_DEFAULT_REGISTRY_CATALOG = [
    {
        "name": "windows-mcp",
        "description": "Control Windows desktop UI, inspect accessibility tree, click, type, and take screenshots.",
        "install": "uvx windows-mcp serve",
        "repo": "https://github.com/modelcontextprotocol/windows-mcp",
    },
    {
        "name": "office-com",
        "description": "Microsoft Office automation for Excel, Word, and Outlook via Windows COM.",
        "install": "uv run python -m modules.mcp_servers.office_com.server",
        "repo": "https://github.com/local/office-com",
    },
    {
        "name": "filesystem",
        "description": "Secure file system operations, reading, writing, and directory listing.",
        "install": "npx -y @modelcontextprotocol/server-filesystem",
        "repo": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "name": "fetch",
        "description": "Web scraping and content fetching tool for extracting web pages.",
        "install": "uvx mcp-server-fetch",
        "repo": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "name": "sqlite",
        "description": "Query and manage SQLite databases with read and write capabilities.",
        "install": "uvx mcp-server-sqlite",
        "repo": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "name": "github",
        "description": "GitHub integration for repository search, issues, pull requests, and file contents.",
        "install": "npx -y @modelcontextprotocol/server-github",
        "repo": "https://github.com/modelcontextprotocol/servers",
    },
    {
        "name": "puppeteer",
        "description": "Browser automation and web interaction using Puppeteer.",
        "install": "npx -y @modelcontextprotocol/server-puppeteer",
        "repo": "https://github.com/modelcontextprotocol/servers",
    },
]


def _fetch_or_load_registry_raw() -> list[dict]:
    """Fetch registry from official endpoint, cache to disk, or fall back to cache/defaults if offline."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = CACHE_DIR / "servers_cache.json"

    # Always ensure default catalog is part of the catalog base
    catalog_map: dict[str, dict] = {item["name"]: item for item in _DEFAULT_REGISTRY_CATALOG}

    # 1. Try online fetch
    endpoint = f"{REGISTRY_URL.rstrip('/')}/v0/servers"
    try:
        resp = httpx.get(endpoint, timeout=3.0)
        if resp.status_code == 200:
            data = resp.json()
            servers_list = data.get("servers", [])
            for item in servers_list:
                s = item.get("server", item)
                name = s.get("name", "")
                desc = s.get("description", "")
                repo_info = s.get("repository", {})
                repo_url = repo_info.get("url", "") if isinstance(repo_info, dict) else str(repo_info)
                install_cmd = s.get("install") or f"uvx {name.replace('/', '-')}"
                if name and name not in catalog_map:
                    catalog_map[name] = {
                        "name": name,
                        "description": desc,
                        "install": install_cmd,
                        "repo": repo_url,
                    }
            merged = list(catalog_map.values())
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
            return merged
    except Exception:
        pass

    # 2. Try disk cache
    if cache_path.is_file():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cached = json.load(f)
                if isinstance(cached, list) and cached:
                    for item in cached:
                        if isinstance(item, dict) and item.get("name") and item["name"] not in catalog_map:
                            catalog_map[item["name"]] = item
                    return list(catalog_map.values())
        except Exception:
            pass

    # 3. Default fallback
    merged = list(catalog_map.values())
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    return merged


def search_registry(need: str, k: int = 5) -> list[dict]:
    """Search official MCP Registry API (cached to data/cache/registry/; offline -> cache only).
    
    Returns list[{"name", "description", "install", "repo", "score"}].
    """
    catalog = _fetch_or_load_registry_raw()
    scored: list[dict] = []

    for item in catalog:
        name = item.get("name", "")
        desc = item.get("description", "")
        combined = f"{name} {desc}"
        score = _compute_similarity(need, combined)
        scored.append({
            "name": name,
            "description": desc,
            "install": item.get("install", f"uvx {name}"),
            "repo": item.get("repo", ""),
            "score": score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:k]


def best_for(need: str) -> dict:
    """Find best MCP server for need. Returns {'installed': dict|None, 'better': dict|None}.
    
    'better' is a registry candidate that clearly beats installed by a margin (e.g. +0.15).
    """
    installed = scan_installed()
    best_installed: dict | None = None
    installed_score = 0.0

    for srv in installed:
        txt = f"{srv['name']} {srv['command']} {' '.join(srv.get('args', []))}"
        score = _compute_similarity(need, txt)
        if score > installed_score:
            installed_score = score
            best_installed = srv

    registry_hits = search_registry(need, k=5)
    best_registry = registry_hits[0] if registry_hits else None
    registry_score = best_registry["score"] if best_registry else 0.0

    better: dict | None = None
    if best_registry and registry_score >= 0.25:
        if best_installed is None or (registry_score - installed_score) >= 0.15:
            better = best_registry

    return {
        "installed": best_installed,
        "better": better,
    }


def request_install(project_id: str, candidate: dict) -> str:
    """Create an approval request for installing an MCP server. Returns approval_id.
    
    Does NOT emit SSE events (the agent loop handles approvals and events).
    """
    summary = {
        "kind": "mcp_install",
        "name": candidate.get("name", ""),
        "repo": candidate.get("repo", ""),
        "command": candidate.get("install", ""),
        "candidate": candidate,
    }

    try:
        from modules.ledger import db
        if hasattr(db, "create_approval"):
            return db.create_approval(project_id, run_id="", step_id="", summary=summary)
    except Exception:
        pass

    return f"appr_mcp_{uuid.uuid4().hex[:8]}"


def _is_approved(project_id: str, approval_id: str) -> bool:
    """Check if an approval_id has decision == 'approve' via db.get_approval."""
    try:
        from modules.ledger import db
        if hasattr(db, "get_approval"):
            appr = db.get_approval(project_id, approval_id)
            if isinstance(appr, dict):
                decision = appr.get("decision")
                return decision in ("approve", "edit")
    except Exception:
        pass

    return False


def install(candidate: dict, project_id: str, approval_id: str) -> dict:
    """Install candidate MCP into data/mcp_servers.json. ONLY allowed after approval."""
    if not approval_id or not _is_approved(project_id, approval_id):
        raise PermissionError(
            f"Install refused: MCP '{candidate.get('name')}' requires an approved approval id (got: {approval_id})"
        )

    name = candidate.get("name")
    if not name:
        raise ValueError("Candidate missing 'name'")

    MCP_SERVERS_JSON.parent.mkdir(parents=True, exist_ok=True)
    if MCP_SERVERS_JSON.is_file():
        try:
            with open(MCP_SERVERS_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = {"mcpServers": {}}
    else:
        data = {"mcpServers": {}}

    if "mcpServers" not in data:
        data["mcpServers"] = {}

    cmd = candidate.get("command")
    args = candidate.get("args")
    if not cmd and "install" in candidate:
        parts = candidate["install"].split()
        cmd = parts[0]
        args = parts[1:]
    elif not cmd:
        cmd = "uvx"
        args = [name]

    if args is None:
        args = []

    data["mcpServers"][name] = {
        "command": cmd,
        "args": args,
        "env": candidate.get("env", {}),
    }

    with open(MCP_SERVERS_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    # Tell mcp_client to reload if it exists
    try:
        from modules.tools import mcp_client
        if hasattr(mcp_client, "reload"):
            mcp_client.reload()
    except Exception:
        pass

    return {"ok": True, "name": name}


if __name__ == "__main__":
    import tempfile
    import time
    from modules.ledger import db

    print("[MCP Registry Self-Check] Starting...")
    t0 = time.time()

    # 1. Test scan_installed with a fake config file, ensuring env values are never exposed
    temp_dir = Path(tempfile.mkdtemp(prefix="mcp_test_"))
    fake_config = temp_dir / "claude_desktop_config.json"
    secret_val = "SECRET_TOKEN_DO_NOT_LEAK_98765"
    fake_content = {
        "mcpServers": {
            "demo-tool": {
                "command": "python",
                "args": ["-m", "demo"],
                "env": {
                    "API_KEY": secret_val,
                    "DATABASE_URL": "postgresql://user:pass@localhost/db"
                }
            }
        }
    }
    with open(fake_config, "w", encoding="utf-8") as f:
        json.dump(fake_content, f)

    scanned = scan_installed(extra_paths=[("fake_source", fake_config)])
    assert len(scanned) == 1, f"Expected 1 server scanned, got {scanned}"
    item = scanned[0]
    assert item["name"] == "demo-tool"
    assert item["command"] == "python"
    assert item["args"] == ["-m", "demo"]
    assert set(item["env_keys"]) == {"API_KEY", "DATABASE_URL"}
    # Verify raw secret value is NEVER present anywhere in the returned data structure
    dumped = json.dumps(scanned)
    assert secret_val not in dumped, "CRITICAL: PII / secret leak in scan_installed!"
    print("  [Pass] scan_installed detected server and concealed env values.")

    # 2. Test search_registry & best_for
    hits = search_registry("browser automation puppeteer", k=3)
    assert len(hits) > 0, "Expected registry search hits"
    assert any("puppeteer" in h["name"].lower() or "browser" in h["description"].lower() for h in hits)
    print("  [Pass] search_registry returned relevant candidates.")

    comparison = best_for("browser automation with puppeteer")
    assert "installed" in comparison and "better" in comparison
    print("  [Pass] best_for ranked candidates.")

    # 3. Test install permission gate: mock db.get_approval and db.create_approval
    project_id = "test_proj_1"
    candidate = {
        "name": "test-mock-server",
        "description": "A mock MCP server for testing installation",
        "install": "uvx mock-mcp-server",
        "repo": "https://github.com/mock/mcp-server"
    }

    mock_approvals: dict[tuple[str, str], dict[str, Any]] = {}

    def mock_create_approval(pid: str, run_id: str, step_id: str, summary: dict) -> str:
        aid = f"appr_mcp_{uuid.uuid4().hex[:8]}"
        mock_approvals[(pid, aid)] = {
            "id": aid,
            "project_id": pid,
            "decision": "pending",
            "summary": summary
        }
        return aid

    def mock_get_approval(pid: str, aid: str) -> dict | None:
        return mock_approvals.get((pid, aid))

    # Temporarily attach mock functions to db module
    orig_create = getattr(db, "create_approval", None)
    orig_get = getattr(db, "get_approval", None)
    db.create_approval = mock_create_approval
    db.get_approval = mock_get_approval

    try:
        # Attempt 1: Install with unapproved / unknown id -> must raise PermissionError
        refused_no_id = False
        try:
            install(candidate, project_id, "unknown_approval_id")
        except PermissionError:
            refused_no_id = True
        assert refused_no_id, "install must refuse when approval id is unknown!"

        # Attempt 2: Request approval, but try to install while decision is 'pending' -> must raise PermissionError
        appr_id = request_install(project_id, candidate)
        assert appr_id.startswith("appr_mcp_"), f"Unexpected approval id: {appr_id}"

        refused_pending = False
        try:
            install(candidate, project_id, appr_id)
        except PermissionError:
            refused_pending = True
        assert refused_pending, "install must refuse when approval decision is not 'approve'!"

        # Attempt 3: Mark approved in db and install -> must succeed
        mock_approvals[(project_id, appr_id)]["decision"] = "approve"
        res_install = install(candidate, project_id, appr_id)
        assert res_install == {"ok": True, "name": "test-mock-server"}, f"Unexpected install result: {res_install}"
        print("  [Pass] install verified approval decision via db.get_approval.")

        # Verify candidate was added to data/mcp_servers.json
        with open(MCP_SERVERS_JSON, "r", encoding="utf-8") as f:
            stored = json.load(f)
        assert "test-mock-server" in stored.get("mcpServers", {}), "Server not recorded in mcp_servers.json"

        # Clean up test entry from mcp_servers.json
        del stored["mcpServers"]["test-mock-server"]
        with open(MCP_SERVERS_JSON, "w", encoding="utf-8") as f:
            json.dump(stored, f, indent=2)

    finally:
        # Restore db functions
        if orig_create is not None:
            db.create_approval = orig_create
        if orig_get is not None:
            db.get_approval = orig_get
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    elapsed = time.time() - t0
    print(f"[MCP Registry Self-Check] ALL CHECKS PASSED in {elapsed:.2f}s (< 10s)")
