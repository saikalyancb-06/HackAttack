"""Visual PII detector using ONNX model and Pillow (A2). CONTRACTS §7."""

import io
import os
from typing import Any
import numpy as np
from PIL import Image, ImageFilter

# confirm with friend
LABELS = [
    "no_pii",
    "email",
    "phone",
    "aadhaar",
    "pan",
    "name",
    "address",
    "credit_card",
    "password",
    "ssn",
    "passport",
    "driver_license",
    "bank_account",
    "dob",
    "face",
    "other_pii",
]
NO_PII_CLASS = 0
BOX_FORMAT = "xyxy"
NORMALIZE = "0-1"

_session = None
_session_model_path = None


def _get_session():
    """Load and cache onnxruntime inference session on CPU."""
    global _session
    if _session is not None:
        return _session

    model_path = os.environ.get("VISUAL_PII_MODEL", "modules/pre-built-vision/visual_pii_detector.onnx")
    if not os.path.isabs(model_path):
        model_path = os.path.abspath(model_path)

    if not os.path.isfile(model_path):
        return None

    try:
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 4
        opts.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        _session = ort.InferenceSession(model_path, sess_options=opts, providers=["CPUExecutionProvider"])
        _session_model_path = model_path
        return _session
    except Exception:
        return None


def _softmax(x: np.ndarray) -> np.ndarray:
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum(axis=-1, keepdims=True)


def _boxes_intersect(b1: list, b2: list) -> bool:
    # box format: [x1, y1, x2, y2, ...]
    return not (b1[2] < b2[0] or b1[0] > b2[2] or b1[3] < b2[1] or b1[1] > b2[3])


def _merge_regions(regions: list[dict]) -> list[dict]:
    """Merge overlapping bounding boxes."""
    if not regions:
        return []

    boxes = [
        [r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"], r["label"], r["score"]]
        for r in regions
    ]

    changed = True
    while changed:
        changed = False
        new_boxes = []
        skip = set()
        for i in range(len(boxes)):
            if i in skip:
                continue
            cur = list(boxes[i])
            for j in range(i + 1, len(boxes)):
                if j in skip:
                    continue
                if _boxes_intersect(cur, boxes[j]):
                    cur[0] = min(cur[0], boxes[j][0])
                    cur[1] = min(cur[1], boxes[j][1])
                    cur[2] = max(cur[2], boxes[j][2])
                    cur[3] = max(cur[3], boxes[j][3])
                    if boxes[j][5] > cur[5]:
                        cur[4] = boxes[j][4]
                        cur[5] = boxes[j][5]
                    skip.add(j)
                    changed = True
            new_boxes.append(cur)
        boxes = new_boxes

    return [
        {
            "x": int(b[0]),
            "y": int(b[1]),
            "w": int(max(1, b[2] - b[0])),
            "h": int(max(1, b[3] - b[1])),
            "label": b[4],
            "score": round(float(b[5]), 4),
        }
        for b in boxes
    ]


def redact_image(png_bytes: bytes) -> tuple[bytes, list[dict]]:
    """Detect PII regions in image with ONNX model and Gaussian-blur them.

    Tiled at 320x320 with stride 240.
    Returns (redacted_png_bytes, regions).
    Fails closed if model is missing: blurs entire image.
    """
    try:
        img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    except Exception:
        # Invalid image bytes, return as is
        return png_bytes, []

    W, H = img.size
    session = _get_session()

    # Fail closed if model is missing
    if session is None:
        blurred = img.filter(ImageFilter.GaussianBlur(radius=12))
        out_io = io.BytesIO()
        blurred.save(out_io, format="PNG")
        return out_io.getvalue(), [{
            "x": 0,
            "y": 0,
            "w": W,
            "h": H,
            "label": "model_missing",
            "score": 1.0,
        }]

    # Tiling: 320x320 crops with stride 240
    xs = list(range(0, W, 240))
    ys = list(range(0, H, 240))

    raw_regions: list[dict] = []

    for y in ys:
        for x in xs:
            crop_w = min(320, W - x)
            crop_h = min(320, H - y)
            crop = img.crop((x, y, x + crop_w, y + crop_h))
            tile = Image.new("RGB", (320, 320), (0, 0, 0))
            tile.paste(crop, (0, 0))

            arr = np.array(tile, dtype=np.float32)
            if NORMALIZE == "0-1":
                arr = arr / 255.0
            elif NORMALIZE == "imagenet":
                mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
                std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
                arr = (arr / 255.0 - mean) / std
            else:
                arr = arr / 255.0

            inp = np.transpose(arr, (2, 0, 1))[np.newaxis, :, :, :]
            outputs = session.run(None, {"input_image": inp})
            boxes_out, logits_out = outputs[0], outputs[1]

            probs = _softmax(logits_out[0])
            pred_class = int(np.argmax(probs))
            max_prob = float(probs[pred_class])

            if pred_class != NO_PII_CLASS and max_prob >= 0.5:
                label = LABELS[pred_class] if pred_class < len(LABELS) else f"pii_{pred_class}"
                b = boxes_out[0]  # [b0, b1, b2, b3]

                if BOX_FORMAT == "xyxy":
                    t_x1 = float(b[0]) * 320.0
                    t_y1 = float(b[1]) * 320.0
                    t_x2 = float(b[2]) * 320.0
                    t_y2 = float(b[3]) * 320.0
                else:  # fallback
                    t_x1 = float(b[0]) * 320.0
                    t_y1 = float(b[1]) * 320.0
                    t_x2 = float(b[2]) * 320.0
                    t_y2 = float(b[3]) * 320.0

                min_x, max_x = min(t_x1, t_x2), max(t_x1, t_x2)
                min_y, max_y = min(t_y1, t_y2), max(t_y1, t_y2)

                img_x1 = max(0, min(W, int(round(x + min_x))))
                img_y1 = max(0, min(H, int(round(y + min_y))))
                img_x2 = max(0, min(W, int(round(x + max_x))))
                img_y2 = max(0, min(H, int(round(y + max_y))))

                rw = max(1, img_x2 - img_x1)
                rh = max(1, img_y2 - img_y1)
                rw = min(rw, W - img_x1)
                rh = min(rh, H - img_y1)

                raw_regions.append({
                    "x": img_x1,
                    "y": img_y1,
                    "w": rw,
                    "h": rh,
                    "label": label,
                    "score": max_prob,
                })

    merged_regions = _merge_regions(raw_regions)

    # Apply Gaussian blur (radius 12) to each detected region
    for r in merged_regions:
        box = (r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"])
        cropped = img.crop(box)
        blurred = cropped.filter(ImageFilter.GaussianBlur(radius=12))
        img.paste(blurred, box)

    out_io = io.BytesIO()
    img.save(out_io, format="PNG")
    return out_io.getvalue(), merged_regions


if __name__ == "__main__":
    import time
    from unittest.mock import MagicMock, patch

    t0 = time.time()

    # 1. Self-check on 1920x1080 synthetic image (< 1.5s benchmark)
    synth_img = Image.new("RGB", (1920, 1080), (240, 240, 240))
    io_synth = io.BytesIO()
    synth_img.save(io_synth, format="PNG")
    synth_bytes = io_synth.getvalue()

    t_start = time.perf_counter()
    out_png, regions = redact_image(synth_bytes)
    t_elapsed = time.perf_counter() - t_start

    assert isinstance(out_png, bytes)
    assert out_png.startswith(b"\x89PNG"), "Must return valid PNG bytes"
    assert t_elapsed < 1.5, f"Must complete in < 1.5s, took {t_elapsed:.3f}s"
    print(f"  [Pass] 1920x1080 screenshot processed in {t_elapsed:.3f}s (< 1.5s)")

    # 2. Self-check with mock detection: verify coordinates clipping, merging, and blur
    mock_session = MagicMock()
    # Return a detected box [0.2, 0.2, 0.8, 0.8] and high probability for class 1 ("email")
    fake_logits = np.zeros((1, 16), dtype=np.float32)
    fake_logits[0, 1] = 10.0  # high prob for class 1
    fake_boxes = np.array([[0.2, 0.2, 0.8, 0.8]], dtype=np.float32)
    mock_session.run.return_value = [fake_boxes, fake_logits]

    globals()["_session"] = mock_session
    globals()["_session_model_path"] = "mock_model"
    try:
        test_img = Image.new("RGB", (640, 480), (100, 150, 200))
        io_test = io.BytesIO()
        test_img.save(io_test, format="PNG")
        redacted_bytes, det_regions = redact_image(io_test.getvalue())

        assert len(det_regions) > 0
        assert det_regions[0]["label"] == "email"
        for reg in det_regions:
            assert reg["x"] >= 0 and reg["y"] >= 0
            assert reg["x"] + reg["w"] <= 640, f"Box width out of bounds: {reg}"
            assert reg["y"] + reg["h"] <= 480, f"Box height out of bounds: {reg}"
        print(f"  [Pass] Mock detection merged and clipped: {len(det_regions)} regions")
    finally:
        globals()["_session"] = None
        globals()["_session_model_path"] = None

    # 3. Fail-closed test: model missing
    orig_env = os.environ.get("VISUAL_PII_MODEL")
    os.environ["VISUAL_PII_MODEL"] = "non_existent_model_file.onnx"
    globals()["_session"] = None
    globals()["_session_model_path"] = None

    try:
        missing_bytes, missing_regions = redact_image(synth_bytes)
        assert len(missing_regions) == 1
        assert missing_regions[0]["label"] == "model_missing"
        assert missing_regions[0]["w"] == 1920
        assert missing_regions[0]["h"] == 1080
        print("  [Pass] Fail-closed verified: missing model blurs full image")
    finally:
        if orig_env is not None:
            os.environ["VISUAL_PII_MODEL"] = orig_env
        else:
            os.environ.pop("VISUAL_PII_MODEL", None)
        globals()["_session"] = None
        globals()["_session_model_path"] = None

    print(f"guard.visual_pii self-check PASSED in {time.time() - t0:.3f}s")
