"""Headline sentiment for Yahoo items.

Primary: FinBERT, an open-source model that runs locally. No API key, no per-call cost.
Optional: ChatGPT or Claude, used only if listed in HEADLINE_AI_PROVIDERS and enabled
with FAB787_AI_API_CALLS_ENABLED=true. Results are estimates from headline wording only,
labeled as AI estimates with low confidence. No trade, risk, or order authority.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import UTC, datetime
from typing import Any

from mission_control.fab787_ai_providers import AIProviderError, AIProviderManager

# Paid providers stay gated by FAB787_AI_API_CALLS_ENABLED at runtime.
_manager = AIProviderManager(allow_api_calls=True)
_ALLOWED = {"finbert", "chatgpt", "claude"}
PROVIDERS = [
    p.strip().lower()
    for p in os.environ.get("HEADLINE_AI_PROVIDERS", "finbert").split(",")
    if p.strip().lower() in _ALLOWED
] or ["finbert"]

BATCH_SIZE = 15
MAX_NEW_PER_REFRESH = 60
CACHE_TTL_SECONDS = 6 * 60 * 60
VALID_SENTIMENT = {"bullish", "bearish", "neutral"}
VALID_INSTRUMENT = {"MNQ", "MES", "MGC"}
FINBERT_MAP = {"positive": "bullish", "negative": "bearish", "neutral": "neutral"}
INSTRUMENT_KEYWORDS = {
    "MNQ": ["nasdaq", "tech", "software", "semiconductor", "chip", "nvidia", "apple",
            "microsoft", "alphabet", "google", "meta", "amazon", "tesla", "qqq"],
    "MES": ["s&p", "spy", "dow", "stock market", "wall street", "broad market", "equities"],
    "MGC": ["gold", "bullion", "xau", "gld", "precious metal"],
}
SYSTEM = (
    "You label financial news headlines for a research dashboard. For each numbered headline, "
    "decide whether its wording is bullish, bearish, or neutral for US stocks, and which futures "
    "it relates to: MNQ (Nasdaq-100 and tech), MES (S&P 500 and broad US stocks), MGC (gold), "
    "or NONE if unclear. Use only the headline text. If unsure, answer neutral and NONE. "
    "Reply with ONLY a JSON array and no other text. Each element must look like "
    '{"n": 1, "sentiment": "bullish", "instrument": "MNQ"}.'
)

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_finbert_pipe: Any = None
_finbert_error: str | None = None


def _key(item: dict[str, Any]) -> str:
    basis = f"{item.get('id') or ''}|{item.get('title') or ''}".encode("utf-8")
    return hashlib.sha256(basis).hexdigest()[:24]


def _instrument(title: str) -> str | None:
    """Map a headline to one futures contract by keywords. Ties or no match give None."""
    text = f" {title.lower()} "
    scores = {inst: sum(1 for kw in kws if kw in text) for inst, kws in INSTRUMENT_KEYWORDS.items()}
    best = max(scores.values())
    if best == 0:
        return None
    leaders = [inst for inst, score in scores.items() if score == best]
    return leaders[0] if len(leaders) == 1 else None


def _load_finbert() -> Any:
    global _finbert_pipe, _finbert_error
    if _finbert_pipe is not None:
        return _finbert_pipe
    if _finbert_error is not None:
        raise AIProviderError(_finbert_error)
    try:
        from transformers import pipeline
        _finbert_pipe = pipeline("text-classification", model="ProsusAI/finbert", top_k=None)
    except Exception as exc:
        _finbert_error = f"FinBERT unavailable ({type(exc).__name__}). Install: pip install transformers torch"
        raise AIProviderError(_finbert_error) from None
    return _finbert_pipe


def _finbert_read(titles: list[str]) -> dict[int, dict[str, Any]]:
    model = _load_finbert()
    results = model([t[:400] for t in titles], truncation=True)
    parsed: dict[int, dict[str, Any]] = {}
    for number, scores in enumerate(results, 1):
        best = max(scores, key=lambda r: r["score"])
        parsed[number] = {
            "sentiment": FINBERT_MAP.get(str(best["label"]).lower(), "neutral"),
            "confidence_score": round(float(best["score"]), 2),
        }
    return parsed


def _parse_reply(text: str, count: int) -> dict[int, dict[str, Any]]:
    match = re.search(r"\[[\s\S]*\]", text)
    if match is None:
        raise AIProviderError("AI reply did not contain a JSON array.")
    try:
        rows = json.loads(match.group(0))
    except json.JSONDecodeError:
        raise AIProviderError("AI reply JSON could not be parsed.") from None
    parsed: dict[int, dict[str, Any]] = {}
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        try:
            number = int(row.get("n"))
        except (TypeError, ValueError):
            continue
        if not 1 <= number <= count:
            continue
        sentiment = str(row.get("sentiment", "")).strip().lower()
        instrument = str(row.get("instrument", "")).strip().upper()
        parsed[number] = {
            "sentiment": sentiment if sentiment in VALID_SENTIMENT else "neutral",
            "instrument": instrument if instrument in VALID_INSTRUMENT else None,
        }
    return parsed


async def _ask(titles: list[str]) -> tuple[dict[int, dict[str, Any]], str]:
    """Try providers in order. A failure moves to the next provider."""
    errors: list[str] = []
    for provider in PROVIDERS:
        try:
            if provider == "finbert":
                parsed = await asyncio.to_thread(_finbert_read, titles)
                for number, row in parsed.items():
                    row["instrument"] = _instrument(titles[number - 1])
                return parsed, provider
            if not _manager.api_calls_enabled:
                raise AIProviderError("paid AI calls are off; set FAB787_AI_API_CALLS_ENABLED=true")
            prompt = "Headlines:\n" + "\n".join(f"{n}. {t[:300]}" for n, t in enumerate(titles, 1))
            reply = await _manager.complete(provider, prompt, system=SYSTEM, max_output_tokens=800)
            return _parse_reply(reply["text"], len(titles)), provider
        except AIProviderError as exc:
            errors.append(f"{provider}: {exc}")
        except Exception as exc:
            errors.append(f"{provider}: {type(exc).__name__}")
    raise AIProviderError(" | ".join(errors) or "no providers configured")


async def classify_yahoo_headlines(items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Return (labels keyed by headline, status). Each headline is read once per TTL."""
    now = time.time()
    labels: dict[str, dict[str, Any]] = {}
    pending: list[tuple[str, dict[str, Any]]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        key = _key(item)
        cached = _cache.get(key)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            labels[key] = cached[1]
        else:
            pending.append((key, item))

    pending = pending[:MAX_NEW_PER_REFRESH]
    used_providers: set[str] = set()
    batches = 0
    error = None
    for start in range(0, len(pending), BATCH_SIZE):
        batch = pending[start:start + BATCH_SIZE]
        titles = [item["title"] for _, item in batch]
        try:
            parsed, used = await _ask(titles)
        except AIProviderError as exc:
            error = str(exc)
            break
        batches += 1
        used_providers.add(used)
        stamp = datetime.now(UTC).isoformat()
        for number, (key, _) in enumerate(batch, 1):
            row = parsed.get(number)
            if row is None:
                continue
            label = {
                "sentiment": row["sentiment"],
                "instrument": row.get("instrument"),
                "provider": used,
                "reviewed_at": stamp,
            }
            _cache[key] = (now, label)
            labels[key] = label

    return labels, {
        "status": "error" if error else "ready",
        "message": error,
        "provider_order": PROVIDERS,
        "providers_used_this_refresh": sorted(used_providers),
        "batches_this_refresh": batches,
        "new_headlines_read": len(pending),
        "cached_headlines": len(_cache),
    }


def apply_ai_labels(items: list[dict[str, Any]], labels: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Attach model estimates only to headlines that map to one futures instrument."""
    output: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = labels.get(_key(item)) if item.get("title") else None
        if label and label.get("instrument"):
            output.append({
                **item,
                "symbol": label["instrument"],
                "sentiment": label["sentiment"],
                "sentiment_source": "ai_estimate",
            })
        else:
            output.append(item)
    return output
