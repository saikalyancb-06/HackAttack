"""LOCAL SHIM (not part of the C2/A3 deliverable, never zipped): adapts the local reference backend in backend/
to the CONTRACTS.md interface so modules/kg and modules/agent/explore.py can run here. At integration the real
module from C1/A2/A1 replaces this file."""
from backend import llm as _llm


def complete(messages, json_schema=None, images=None, task=None, **kw):
    return _llm.complete(messages, json_schema=json_schema, images=images, **kw)
