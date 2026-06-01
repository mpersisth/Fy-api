"""CLIP embedding cosine similarity comparison.

Uses HuggingFace transformers CLIPModel. Loaded lazily;
guarded by clip_available().
"""

from __future__ import annotations

import math
from dataclasses import dataclass

_MODEL_CACHE: dict[str, object] = {}


def clip_available() -> bool:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        from PIL import Image  # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class ClipVerdict:
    prompt: str
    cosine_similarity: float
    threshold: float
    passed: bool


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def _get_model_and_processor():
    if "clip" not in _MODEL_CACHE:
        from transformers import CLIPModel, CLIPProcessor
        model_name = "openai/clip-vit-base-patch32"
        _MODEL_CACHE["clip_model"] = CLIPModel.from_pretrained(model_name)
        _MODEL_CACHE["clip_processor"] = CLIPProcessor.from_pretrained(model_name)
        _MODEL_CACHE["clip"] = True
    return _MODEL_CACHE["clip_model"], _MODEL_CACHE["clip_processor"]


def compute_clip_embedding(image_bytes: bytes) -> list[float]:
    import io
    import torch
    from PIL import Image

    model, processor = _get_model_and_processor()
    image = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    inputs = processor(images=image, return_tensors="pt")
    with torch.no_grad():
        features = model.get_image_features(**inputs)
    embedding = features[0].tolist()
    return embedding


def evaluate_clip(
    *,
    prompt: str,
    gateway_image: bytes,
    vendor_image: bytes,
    threshold: float = 0.90,
) -> ClipVerdict:
    emb_gw = compute_clip_embedding(gateway_image)
    emb_vendor = compute_clip_embedding(vendor_image)
    sim = _cosine(emb_gw, emb_vendor)
    return ClipVerdict(
        prompt=prompt,
        cosine_similarity=sim,
        threshold=threshold,
        passed=sim >= threshold,
    )
