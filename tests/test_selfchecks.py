"""Runs every module's `if __name__ == "__main__":` self-check (no network-dependent assertions).

    python -m pytest tests/test_selfchecks.py -q
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULES = [
    "backend.guard.pii", "backend.guard.risk", "backend.guard.safe_delete", "backend.guard.visual_pii",
    "backend.guard.command_guard", "backend.ledger.retry", "backend.ledger.db", "backend.kg.store", "backend.kg.rag",
    "backend.tools.files", "backend.tools.shell", "backend.tools.research", "backend.tools.office",
    "backend.tools.mcp_client", "backend.tools.mcp_registry", "backend.agent.events", "backend.agent.think",
    "backend.agent.planner", "backend.agent.explore", "backend.classify", "backend.llm",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_selfcheck(module: str) -> None:
    env = {**os.environ, "PYTHONUTF8": "1", "LLM_DISABLED": os.environ.get("SELFCHECK_LLM_DISABLED", "1")}
    p = subprocess.run([sys.executable, "-m", module], cwd=ROOT, capture_output=True, text=True, timeout=240, env=env)
    assert p.returncode == 0, f"{module} failed:\n{p.stdout[-2000:]}\n{p.stderr[-3000:]}"
    assert "OK" in p.stdout, p.stdout[-500:]


def test_reference_resolution() -> None:
    from backend.agent.loop import _lift_instruction_refs, resolve
    out = {"s1": {"results": [{"url": "u0"}, {"url": "u1"}]},
           "s3": {"items": [{"idx": 0, "ok": True, "output": {"emails": [{"to": "a"}]}}]}}
    assert resolve("{{s1.results[1].url}}", out) == "u1"
    assert resolve("{{s1.results.url}}", out) == ["u0", "u1"]
    assert resolve("{{s3.emails}}", out) == [{"to": "a"}]
    assert resolve("Dear {{Company}}", out) == "Dear {{Company}}"  # template text is not a reference
    lifted = _lift_instruction_refs({"instruction": "Rank {{s1}}", "inputs": None})
    assert lifted["instruction"] == "Rank [data: s1]" and lifted["inputs"] == {"s1": "{{s1}}"}


def test_command_guard_rules() -> None:
    from backend.guard.command_guard import classify_rules
    assert classify_rules("Get-PSDrive")[0] == "read"
    assert classify_rules("Remove-Item x -Recurse")[0] == "irreversible"


def test_intent_capability_limits() -> None:
    from backend.guard.intent import _rule_feasibility
    needs = {u["need"] for u in _rule_feasibility("buy the laptop and call the shop")["unsupported"]}
    assert {"making payments or purchases", "phone calls or SMS"} <= needs
