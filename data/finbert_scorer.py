"""FinBERT (ProsusAI/finbert) sentiment scorer.

Singleton model loaded once at startup via `preload()`. All subsequent calls to
`score_batch()` reuse the same in-memory model. First load downloads ~440 MB
to the HuggingFace cache (`~/.cache/huggingface/hub/`); subsequent runs are
instant.

Usage:
    from data import finbert_scorer
    finbert_scorer.preload()                       # at app startup
    scores = finbert_scorer.score_batch([txt, ...]) # in jobs
"""
from __future__ import annotations

import logging
import os
import threading
from typing import Optional

from dotenv import load_dotenv

import config

load_dotenv()
logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_MODEL = None
_TOKENIZER = None
_DEVICE: Optional[int] = None    # 0 for CUDA, -1 for CPU (transformers convention)

_LABELS = ("positive", "negative", "neutral")


def _use_gpu() -> bool:
    flag = os.environ.get("USE_GPU", "false").strip().lower()
    if flag not in ("1", "true", "yes", "on"):
        return False
    try:
        import torch
        return bool(torch.cuda.is_available())
    except Exception:
        return False


def preload() -> None:
    """Load FinBERT into memory. Idempotent and thread-safe. Blocks while the
    model downloads on first run."""
    global _MODEL, _TOKENIZER, _DEVICE
    if _MODEL is not None:
        return
    with _LOCK:
        if _MODEL is not None:
            return
        logger.info("Loading FinBERT (%s) ... first run may download ~440MB",
                    config.FINBERT_MODEL_NAME)
        try:
            import torch
            from transformers import (AutoModelForSequenceClassification,
                                      AutoTokenizer)
        except Exception:
            logger.exception("transformers/torch not installed — FinBERT disabled")
            return

        _TOKENIZER = AutoTokenizer.from_pretrained(config.FINBERT_MODEL_NAME)
        model = AutoModelForSequenceClassification.from_pretrained(
            config.FINBERT_MODEL_NAME)
        model.eval()
        if _use_gpu():
            try:
                model = model.to("cuda")
                _DEVICE = 0
                logger.info("FinBERT on CUDA")
            except Exception:
                logger.exception("CUDA move failed; staying on CPU")
                _DEVICE = -1
        else:
            _DEVICE = -1
            logger.info("FinBERT on CPU")
        _MODEL = model


def is_loaded() -> bool:
    return _MODEL is not None and _TOKENIZER is not None


def score_batch(texts: list[str], batch_size: int | None = None) -> list[dict]:
    """Run FinBERT on `texts` and return one dict per input:

        {"positive": .., "negative": .., "neutral": ..,
         "label": "positive"|"negative"|"neutral",
         "confidence": max(probabilities)}

    Inputs are truncated to 512 tokens. If the model failed to load (e.g.
    transformers missing in dev environment) returns neutral placeholders so
    callers can still proceed.
    """
    if not texts:
        return []
    if not is_loaded():
        preload()
    if not is_loaded():
        # Hard fallback — never crash a job because of model unavailability.
        logger.warning("FinBERT unavailable; returning neutral placeholders")
        return [_neutral_placeholder() for _ in texts]

    import torch

    bs = batch_size or config.BATCH_SIZE_FINBERT
    out: list[dict] = []

    for i in range(0, len(texts), bs):
        chunk = [t if t else " " for t in texts[i:i + bs]]
        try:
            enc = _TOKENIZER(chunk, return_tensors="pt", padding=True,
                             truncation=True, max_length=512)
            if _DEVICE == 0:
                enc = {k: v.to("cuda") for k, v in enc.items()}
            with torch.no_grad():
                logits = _MODEL(**enc).logits
            probs = torch.nn.functional.softmax(logits, dim=-1).cpu().numpy()
        except Exception:
            logger.exception("FinBERT inference failed for batch starting at %d", i)
            out.extend(_neutral_placeholder() for _ in chunk)
            continue

        # ProsusAI/finbert label order: 0=positive, 1=negative, 2=neutral
        id2label = getattr(_MODEL.config, "id2label", None)
        for row in probs:
            d = _row_to_dict(row, id2label)
            out.append(d)
    return out


def _row_to_dict(row, id2label) -> dict:
    if id2label and all(i in id2label for i in (0, 1, 2)):
        mapping = {id2label[i].lower(): float(row[i]) for i in range(len(row))}
        pos = mapping.get("positive", 0.0)
        neg = mapping.get("negative", 0.0)
        neu = mapping.get("neutral", 0.0)
    else:
        pos, neg, neu = float(row[0]), float(row[1]), float(row[2])
    label_idx = max(range(3), key=lambda i: (pos, neg, neu)[i])
    label = _LABELS[label_idx]
    return {
        "positive": pos,
        "negative": neg,
        "neutral":  neu,
        "label":    label,
        "confidence": float(max(pos, neg, neu)),
    }


def _neutral_placeholder() -> dict:
    return {
        "positive": 0.0, "negative": 0.0, "neutral": 1.0,
        "label": "neutral", "confidence": 0.0,
    }
