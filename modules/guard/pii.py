"""LOCAL SHIM (not part of the C2/A3 deliverable, never zipped): adapts the local reference backend in backend/
to the CONTRACTS.md interface so modules/kg and modules/agent/explore.py can run here. At integration the real
module from C1/A2/A1 replaces this file."""
from backend.guard.pii import mask as _mask, unmask  # noqa: F401


def mask(text):
    return _mask(text)
