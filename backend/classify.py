"""classify(text, labels, threshold=0.6) -> {"label": str|None, "score": float}

Zero-shot routing without a model download: cue-word scoring per known label set, falling back to the
fast LLM when cues are ambiguous. label=None below threshold so the caller decides (contract §7b).
"""
from __future__ import annotations

import re

from backend import config, llm

_CUES = {
    "a task to perform": r"^(find|search|create|make|save|write|draft|prepare|compare|book|open|run|delete|move|copy|organi[sz]e|"
                         r"summari[sz]e|list|send|update|generate|build|download|rename|fill|schedule|research|collect|get|put|add|clean)\b|"
                         r"\b(please|can you|could you)\b.*\b(find|create|save|draft|make|write|open|run|organi[sz]e)\b",
    "a question about the project": r"^(what|which|who|when|where|why|how|did|do|does|is|are|was|were|show me what|tell me)\b|\?\s*$",
    "only reads data": r"\b(get|list|read|search|find|fetch|query|show|view|describe)\b",
    "creates or modifies data": r"\b(create|write|update|insert|upsert|save|edit|set|add|rename|move|copy)\b",
    "deletes, sends, submits or pays": r"\b(delete|remove|send|submit|pay|purchase|transfer|publish|post|drop)\b",
    "office document or spreadsheet": r"\b(excel|spreadsheet|sheet|xlsx|word|docx|document|outlook|email|draft)\b",
    "website": r"\b(web|website|search|online|internet|url|http|site|browse)\b",
    "other desktop application": r"\b(notepad|paint|calculator|settings|app|application|packet tracer|window)\b",
    "files or shell": r"\b(file|folder|directory|command|powershell|cmd|shell|terminal|script)\b",
    "needs to see the screen": r"\b(color|colour|draw|image|picture|icon|visual|layout|canvas)\b",
    "can be done without seeing the screen": r"\b(type|text|menu|button|save|open|click|setting)\b",
}


def classify(text: str, labels: list[str], threshold: float = 0.6) -> dict:
    t = (text or "").strip().lower()
    scores = {}
    for lab in labels:
        pat = _CUES.get(lab)
        scores[lab] = len(re.findall(pat, t)) if pat else 0
    total = sum(scores.values())
    if total:
        best = max(scores, key=scores.get)
        conf = scores[best] / total
        if conf >= threshold:
            return {"label": best, "score": round(conf, 3)}
    if llm.available():
        try:
            out = llm.complete([{"role": "system", "content": "Classify the text into exactly one of the labels. Respond JSON {\"label\": <label>, \"score\": 0..1}."},
                                {"role": "user", "content": f"Labels: {labels}\nText: {text[:1500]}"}],
                               json_schema={"type": "object"}, model=config.LLM_FAST_MODEL, max_tokens=300)
            if out.get("label") in labels and float(out.get("score", 0)) >= threshold:
                return {"label": out["label"], "score": float(out["score"])}
        except Exception:
            pass
    return {"label": None, "score": 0.0}


if __name__ == "__main__":
    L = ["a task to perform", "a question about the project"]
    assert classify("Find three internships and save them to Excel", L)["label"] == "a task to perform"
    assert classify("What deadlines did we find?", L)["label"] == "a question about the project"
    print("classify self-check OK")
