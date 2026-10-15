"""Recycle-bin delete (A2). CONTRACTS §7."""

import os
import send2trash


def safe_delete(path: str) -> dict:
    """Move file/directory to Recycle Bin using send2trash.

    Raises FileNotFoundError if path does not exist.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path does not exist: {path}")
    send2trash.send2trash(path)
    return {"recycled": path}


if __name__ == "__main__":
    import tempfile
    import time

    t0 = time.time()
    # 1. Existing file is recycled
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"delete me")
        tmp_name = f.name

    assert os.path.exists(tmp_name)
    res = safe_delete(tmp_name)
    assert res == {"recycled": tmp_name}
    assert not os.path.exists(tmp_name)

    # 2. Non-existent path raises FileNotFoundError
    raised = False
    try:
        safe_delete(tmp_name)
    except FileNotFoundError:
        raised = True
    assert raised

    print(f"guard.safe_delete self-check PASSED in {time.time() - t0:.3f}s")
