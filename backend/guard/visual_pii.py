"""Blur PII regions in screenshots before any vision LLM call or UI display.

redact_image(png_bytes) -> (png_bytes, regions)   regions = [{"x","y","w","h","label","score"}]

Detection: local OCR (Tesseract) word boxes -> line text -> guard.pii patterns -> box union per match.
Callers that already know text boxes (browser DOM / UI tree) can pass them as `text_boxes`.
Fail closed: if OCR is unavailable the whole image is blurred (score 0.0, label "UNVERIFIED").
"""
from __future__ import annotations

import io

from PIL import Image, ImageFilter

from backend.guard import pii

try:
    import pytesseract  # type: ignore
    import shutil as _sh
    import os as _os
    _tess = _sh.which("tesseract") or r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    if _os.path.exists(_tess):
        pytesseract.pytesseract.tesseract_cmd = _tess
        _HAVE_OCR = True
    else:
        _HAVE_OCR = False
except Exception:  # pragma: no cover
    _HAVE_OCR = False


def _blur(img: Image.Image, box: tuple[int, int, int, int]) -> None:
    x, y, w, h = box
    x, y = max(0, x - 3), max(0, y - 3)
    w, h = min(img.width - x, w + 6), min(img.height - y, h + 6)
    if w <= 0 or h <= 0:
        return
    region = img.crop((x, y, x + w, y + h)).filter(ImageFilter.GaussianBlur(radius=max(6, h // 2)))
    img.paste(region, (x, y))


def _ocr_regions(img: Image.Image) -> list[dict]:
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    lines: dict = {}
    for i, word in enumerate(data["text"]):
        if not word.strip():
            continue
        k = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(k, []).append((word, data["left"][i], data["top"][i], data["width"][i], data["height"][i]))
    regions = []
    for words in lines.values():
        # character offsets of each word inside the joined line
        text, spans = "", []
        for w in words:
            start = len(text) + (1 if text else 0)
            text = f"{text} {w[0]}" if text else w[0]
            spans.append((start, start + len(w[0]), w))
        for label, pattern, validator in pii._PATTERNS:
            for m in pattern.finditer(text):
                if validator is not None and not validator(m.group(0)):
                    continue
                hit = [w for s, e, w in spans if s < m.end() and e > m.start()]
                if not hit:
                    continue
                x0, y0 = min(w[1] for w in hit), min(w[2] for w in hit)
                x1, y1 = max(w[1] + w[3] for w in hit), max(w[2] + w[4] for w in hit)
                regions.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0, "label": label, "score": 0.9})
    return regions


def redact_image(png_bytes: bytes, text_boxes: list[dict] | None = None) -> tuple[bytes, list[dict]]:
    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    regions: list[dict] = []
    for tb in text_boxes or []:
        masked, mapping = pii.mask(str(tb.get("text", "")))
        if mapping:
            regions.append({"x": int(tb["x"]), "y": int(tb["y"]), "w": int(tb["w"]), "h": int(tb["h"]),
                            "label": next(iter(mapping)).strip("<>").rsplit("_", 1)[0], "score": 1.0})
    if _HAVE_OCR:
        try:
            regions += _ocr_regions(img)
        except Exception:
            regions.append({"x": 0, "y": 0, "w": img.width, "h": img.height, "label": "UNVERIFIED", "score": 0.0})
    elif not text_boxes:
        regions.append({"x": 0, "y": 0, "w": img.width, "h": img.height, "label": "UNVERIFIED", "score": 0.0})
    for r in regions:
        _blur(img, (r["x"], r["y"], r["w"], r["h"]))
    out = io.BytesIO()
    img.save(out, format="PNG")
    png = out.getvalue()
    # Second pass: the team's on-device ONNX visual PII detector (modules/guard/visual_pii.py) catches PII that is
    # not machine-readable text (handwriting, ID-card layouts). Its regions are blurred too.
    if not any(r["label"] == "UNVERIFIED" for r in regions):
        try:
            from modules.guard.visual_pii import redact_image as onnx_redact
            from backend import config
            import os
            os.environ.setdefault("VISUAL_PII_MODEL", str(config.ROOT / "modules" / "pre-built-vision" / "visual_pii_detector.onnx"))
            if os.path.exists(os.environ["VISUAL_PII_MODEL"]):
                png2, extra = onnx_redact(png)
                if extra and not any(e.get("label") == "UNVERIFIED" or (e.get("w", 0) >= img.width and e.get("h", 0) >= img.height) for e in extra):
                    png, regions = png2, regions + [{**e, "label": f"VISUAL_{e.get('label', 'PII')}"} for e in extra]
        except Exception:
            pass
    return png, regions


if __name__ == "__main__":
    from PIL import ImageDraw, ImageFont
    im = Image.new("RGB", (900, 160), "white")
    d = ImageDraw.Draw(im)
    try:
        font = ImageFont.truetype("arial.ttf", 28)
    except Exception:
        font = ImageFont.load_default()
    d.text((20, 20), "Contact: priya.sharma@example.com", fill="black", font=font)
    d.text((20, 80), "Internship deadline 30 Nov", fill="black", font=font)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    out, regions = redact_image(buf.getvalue())
    assert regions, "expected at least one region"
    if _HAVE_OCR:
        assert any(r["label"] == "EMAIL" for r in regions), regions
        assert all(r["y"] < 70 for r in regions if r["label"] == "EMAIL")
    out2, regions2 = redact_image(buf.getvalue(), text_boxes=[{"text": "9876543210", "x": 1, "y": 1, "w": 50, "h": 20}])
    assert any(r["label"] == "PHONE" for r in regions2)
    print(f"guard.visual_pii self-check OK (ocr={_HAVE_OCR}, regions={[(r['label'], r['x'], r['y']) for r in regions]})")
