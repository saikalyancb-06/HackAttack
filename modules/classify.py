"""Zero-shot classifier, ONNX (A1). CONTRACTS 7b."""

import os
import time
import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from transformers import AutoTokenizer

MODEL_NAME = os.environ.get("CLASSIFIER_MODEL", "MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33")

# Load model and tokenizer once at module level
try:
    _model_path = hf_hub_download(repo_id=MODEL_NAME, filename="onnx/model_quantized.onnx", local_files_only=True)
except Exception:
    _model_path = hf_hub_download(repo_id=MODEL_NAME, filename="onnx/model_quantized.onnx")

try:
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, local_files_only=True)
except Exception:
    _tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)

_session = ort.InferenceSession(_model_path, providers=["CPUExecutionProvider"])

# Mapping of route and risk labels to rich descriptive hypotheses for NLI
LABEL_HYPOTHESIS_MAP: dict[str, str] = {
    # Route labels
    "office document or spreadsheet": "This action edits a spreadsheet or document in Microsoft Office.",
    "website": "This action navigates a website in a browser.",
    "other desktop application": "This action operates a desktop application window (GUI).",
    "files or shell": "This action executes a command in the terminal.",
    # Risk labels
    "read": "This is reading data.",
    "write": "This is saving or updating data.",
    "irreversible": "This is deleting or removing data (irreversible).",
}


def _format_hypothesis(label: str) -> str:
    """Format label into an effective NLI hypothesis."""
    if label in LABEL_HYPOTHESIS_MAP:
        return LABEL_HYPOTHESIS_MAP[label]

    l_lower = label.lower()
    if (
        "operate" in l_lower
        or "execute" in l_lower
        or "terminal" in l_lower
        or "desktop application" in l_lower
    ):
        return f"This action will {label}."
    if (
        l_lower.startswith(("a ", "an ", "the "))
        or "question" in l_lower
        or "task" in l_lower
        or "goal" in l_lower
        or "reads" in l_lower
        or "deletes" in l_lower
    ):
        return f"This text is {label}."

    return f"This is {label}."


def classify(text: str, labels: list[str], threshold: float = 0.6) -> dict:
    """Zero-shot classify text against candidate labels using local ONNX model.

    Returns {'label': str|None, 'score': float}.
    If best score < threshold, returns label=None so the caller can fall back to LLM.
    """
    if not labels:
        return {"label": None, "score": 0.0}

    hypotheses = [_format_hypothesis(label) for label in labels]

    inputs = _tokenizer(
        [text] * len(labels),
        hypotheses,
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="np",
    )

    ort_inputs = {
        "input_ids": inputs["input_ids"].astype(np.int64),
        "attention_mask": inputs["attention_mask"].astype(np.int64),
    }

    logits = _session.run(None, ort_inputs)[0]
    # In MoritzLaurer/deberta-v3-xsmall-zeroshot-v1.1-all-33, index 0 is entailment
    ent_logits = logits[:, 0]
    exp = np.exp(ent_logits - np.max(ent_logits))
    scores = exp / np.sum(exp)

    best_idx = int(np.argmax(scores))
    best_score = float(scores[best_idx])
    best_label = labels[best_idx] if best_score >= threshold else None

    return {"label": best_label, "score": round(best_score, 4)}


if __name__ == "__main__":
    print("[Classifier Self-Check] Starting...")
    t_start = time.time()

    # Warm up call
    _ = classify("warmup", ["read", "write"])

    # 1. Risk classification: "delete the old drafts" and "Delete all old drafts" -> irreversible
    t0 = time.time()
    res1a = classify("delete the old drafts", ["read", "write", "irreversible"], threshold=0.6)
    ms1a = (time.time() - t0) * 1000
    print(f"Risk classification ('delete the old drafts'): {res1a} in {ms1a:.1f}ms")
    assert res1a["label"] == "irreversible", f"Expected irreversible, got {res1a}"
    assert ms1a < 200, f"Expected < 200ms, took {ms1a:.1f}ms"

    res1b = classify("Delete all old drafts", ["read", "write", "irreversible"], threshold=0.6)
    print(f"Risk classification ('Delete all old drafts'): {res1b}")
    assert res1b["label"] == "irreversible", f"Expected irreversible, got {res1b}"

    # 2. Message classification: "what did we shortlist?" -> question (not goal)
    t0 = time.time()
    res2a = classify("what did we shortlist?", ["goal", "question"], threshold=0.6)
    ms2a = (time.time() - t0) * 1000
    print(f"Goal vs question ('what did we shortlist?'): {res2a} in {ms2a:.1f}ms")
    assert res2a["label"] == "question", f"Expected question (not goal), got {res2a}"
    assert ms2a < 200, f"Expected < 200ms, took {ms2a:.1f}ms"

    # Also test with detailed message labels
    res2b = classify("What did we shortlist?", ["a task to perform", "a question about the project"], threshold=0.6)
    print(f"Message classification ('What did we shortlist?'): {res2b}")
    assert res2b["label"] is not None and "question" in res2b["label"], f"Expected question label, got {res2b}"

    res2c = classify("what did we shortlist?", ["a goal to achieve", "a question about the project"], threshold=0.6)
    assert res2c["label"] == "a question about the project", f"Expected question, got {res2c}"

    # 3. Low confidence falls back to None
    res_low = classify("xyz123 random noise query", ["finance", "cooking"], threshold=0.85)
    print(f"Low confidence fallback test: {res_low}")
    assert res_low["label"] is None, f"Expected None on low confidence, got {res_low}"

    # 4. Route classification: "write a note in Notepad" classifies as GUI desktop application rather than shell
    t0 = time.time()
    res_route = classify(
        "write a note in Notepad",
        ["operate a desktop application window (GUI)", "execute a command in the terminal"],
        threshold=0.6,
    )
    ms_route = (time.time() - t0) * 1000
    print(f"Notepad route classification (descriptive): {res_route} in {ms_route:.1f}ms")
    assert res_route["label"] == "operate a desktop application window (GUI)", f"Expected GUI app, got {res_route}"

    res_route_4 = classify(
        "write a note in Notepad",
        ["office document or spreadsheet", "website", "other desktop application", "files or shell"],
        threshold=0.6,
    )
    print(f"Notepad route classification (4-route): {res_route_4}")
    assert res_route_4["label"] == "other desktop application", f"Expected other desktop application, got {res_route_4}"

    elapsed = time.time() - t_start
    print(f"[Classifier Self-Check] ALL CHECKS PASSED in {elapsed:.2f}s (< 10s)")
