"""run_command(cmd, shell="powershell|cmd", cwd=None, timeout=60) -> {"exit_code","stdout","stderr","truncated"}

Output is capped at 20 KB per stream and PII-masked before it is returned. Approval is decided by the
caller from guard.command_guard.check(); this function only executes.
"""
from __future__ import annotations

import base64
import re
import subprocess

from backend.guard import pii

CAP = 20_000


_WRAPPER = re.compile(r'^\s*(?:powershell|pwsh)(?:\.exe)?\s+(?:-(?:NoProfile|NonInteractive|NoLogo|ExecutionPolicy\s+\w+)\s+)*-(?:Command|c)\s+(.+)$', re.I | re.S)


def unwrap(cmd: str) -> str:
    """'powershell -Command "Get-X | ..."' -> 'Get-X | ...' (a nested shell would expand $_ and break quoting)."""
    m = _WRAPPER.match(cmd.strip())
    if not m:
        return cmd
    inner = m.group(1).strip()
    if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "\"'":
        inner = inner[1:-1]
        inner = inner.replace('\\"', '"')
    return inner


def run_command(cmd: str, shell: str = "powershell", cwd: str | None = None, timeout: int = 60) -> dict:
    if not cmd or not cmd.strip():
        raise ValueError("empty command")
    if shell == "powershell":
        cmd = unwrap(cmd)
        # -EncodedCommand: the script reaches PowerShell byte-for-byte, no quoting / $ expansion surprises
        encoded = base64.b64encode(cmd.encode("utf-16-le")).decode()
        argv = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded]
    elif shell == "cmd":
        argv = ["cmd.exe", "/d", "/c", cmd]
    else:
        raise ValueError(f"unknown shell {shell!r}")
    try:
        p = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout, text=True, encoding="utf-8", errors="replace")
        out, err, code = p.stdout or "", p.stderr or "", p.returncode
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or b"").decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        err, code = f"timed out after {timeout}s", -1
    truncated = len(out) > CAP or len(err) > CAP
    return {"exit_code": code, "stdout": pii.mask(out[:CAP])[0], "stderr": pii.mask(err[:CAP])[0], "truncated": truncated}


if __name__ == "__main__":
    r = run_command("Write-Output 'hello'; Write-Output 'mail a@b.com'")
    assert r["exit_code"] == 0 and "hello" in r["stdout"] and "a@b.com" not in r["stdout"], r
    assert run_command("echo hi", shell="cmd")["stdout"].strip() == "hi"
    nested = ("powershell -NoProfile -Command \"Get-PSDrive -PSProvider FileSystem | Select-Object Name, "
              "@{Name='FreeGB';Expression={[math]::Round($_.Free/1GB,2)}}\"")
    r = run_command(nested)
    assert r["exit_code"] == 0 and "FreeGB" in r["stdout"], r
    print("tools.shell self-check OK")
