"""Provider-independent sentiment review comparison for FAB787 QUANTUM.

This module does not call Claude, ChatGPT, or market-data providers. It validates
structured, independently prepared reviews and reports their agreement honestly.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

PROVIDERS = {"claude": "Claude", "chatgpt": "ChatGPT"}
ALLOWED_SYMBOLS = {"MNQ", "MES", "MGC"}
ALLOWED_STANCES = {"bullish", "bearish", "neutral", "mixed", "unavailable"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
MAX_REVIEW_AGE_SECONDS = 900.0
MAX_FUTURE_SKEW_SECONDS = 60.0
MAX_SUMMARY_LENGTH = 1200


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _validate_assessment(
    raw: Any,
    *,
    now: datetime,
) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(raw, dict):
        return None, "Assessment must be an object."
    provider = raw.get("provider")
    if provider not in PROVIDERS:
        return None, "Provider must be claude or chatgpt."
    analyzed_at = _parse_time(raw.get("analyzed_at"))
    if analyzed_at is None:
        return None, f"{provider}: analyzed_at must be a timezone-aware ISO timestamp."
    age = (now - analyzed_at).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        return None, f"{provider}: assessment timestamp is in the future."
    if age > MAX_REVIEW_AGE_SECONDS:
        return None, f"{provider}: assessment is older than the 15-minute freshness limit."

    sources = raw.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 10:
        return None, f"{provider}: provide 1 to 10 cited sources."
    normalized_sources = []
    for source in sources:
        if not isinstance(source, dict):
            return None, f"{provider}: each source must be an object."
        title = source.get("title")
        url = source.get("url")
        parsed_url = urlparse(url) if isinstance(url, str) else None
        if (
            not isinstance(title, str) or not title.strip()
            or len(title) > 240
            or parsed_url is None
            or parsed_url.scheme != "https"
            or not parsed_url.netloc
        ):
            return None, f"{provider}: every source needs a title and HTTPS URL."
        published_at = source.get("published_at")
        if published_at is not None:
            parsed_published_at = _parse_time(published_at)
            if parsed_published_at is None:
                return None, f"{provider}: source published_at must be a timezone-aware ISO timestamp."
            published_at = parsed_published_at.isoformat()
        normalized_sources.append({
            "title": title.strip(),
            "url": url,
            "published_at": published_at,
        })

    findings = raw.get("findings")
    if not isinstance(findings, list) or not findings:
        return None, f"{provider}: at least one instrument finding is required."
    normalized_findings = []
    seen_symbols = set()
    for finding in findings:
        if not isinstance(finding, dict):
            return None, f"{provider}: each finding must be an object."
        symbol = finding.get("symbol")
        stance = finding.get("stance")
        summary = finding.get("summary")
        confidence = finding.get("confidence", "low")
        if symbol not in ALLOWED_SYMBOLS or symbol in seen_symbols:
            return None, f"{provider}: symbols must be unique members of MNQ, MES, MGC."
        if stance not in ALLOWED_STANCES:
            return None, f"{provider}: stance must be one of {sorted(ALLOWED_STANCES)}."
        if not isinstance(summary, str) or not summary.strip() or len(summary) > MAX_SUMMARY_LENGTH:
            return None, f"{provider}: each finding needs a concise summary (max 1200 characters)."
        if confidence not in ALLOWED_CONFIDENCE:
            return None, f"{provider}: confidence must be low, medium, or high."
        seen_symbols.add(symbol)
        normalized_findings.append({
            "symbol": symbol,
            "stance": stance,
            "summary": summary.strip(),
            "confidence": confidence,
        })

    return {
        "provider": provider,
        "provider_name": PROVIDERS[provider],
        "model": str(raw.get("model") or "unspecified")[:100],
        "analyzed_at": analyzed_at.isoformat(),
        "age_seconds": round(max(0.0, age), 1),
        "findings": normalized_findings,
        "sources": normalized_sources,
    }, None


def compare_sentiment_assessments(
    assessments: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Compare independent Claude and ChatGPT assessments without fetching data."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    current = current.astimezone(timezone.utc)

    if not isinstance(assessments, list):
        assessments = []
    accepted: dict[str, dict[str, Any]] = {}
    rejected: list[str] = []
    for raw in assessments[:10]:
        assessment, error = _validate_assessment(raw, now=current)
        if error:
            rejected.append(error)
            continue
        provider = assessment["provider"]
        if provider in accepted:
            rejected.append(f"{provider}: duplicate assessment rejected.")
            continue
        accepted[provider] = assessment

    symbols = sorted({
        finding["symbol"]
        for assessment in accepted.values()
        for finding in assessment["findings"]
    })
    comparisons = []
    for symbol in symbols:
        reviews = []
        for provider in ("claude", "chatgpt"):
            assessment = accepted.get(provider)
            if not assessment:
                continue
            finding = next(
                (item for item in assessment["findings"] if item["symbol"] == symbol),
                None,
            )
            if finding:
                reviews.append({
                    "provider": provider,
                    "provider_name": PROVIDERS[provider],
                    "model": assessment["model"],
                    "analyzed_at": assessment["analyzed_at"],
                    "age_seconds": assessment["age_seconds"],
                    **finding,
                    "sources": assessment["sources"],
                })
        stances = {review["stance"] for review in reviews if review["stance"] != "unavailable"}
        usable_reviews = [review for review in reviews if review["stance"] != "unavailable"]
        if not usable_reviews:
            status = "unavailable"
        elif len(usable_reviews) < 2:
            status = "single_provider"
        elif len(stances) > 1:
            status = "disagreement"
        else:
            status = "agreement"
        comparisons.append({
            "symbol": symbol,
            "status": status,
            "stance": next(iter(stances)) if len(stances) == 1 else "not_resolved",
            "reviews": reviews,
            "interpretation": (
                "Independent AI reviews agree on the broad stance; this is not a verified market fact or trade signal."
                if status == "agreement" else
                "Independent AI reviews disagree; preserve both views and require human interpretation."
                if status == "disagreement" else
                "Only one provider supplied a usable review; independent confirmation is absent."
                if status == "single_provider" else
                "No usable directional stance was supplied."
            ),
        })

    provider_status = {
        provider: (
            {"status": "review_received", "provider_name": PROVIDERS[provider],
             "analyzed_at": accepted[provider]["analyzed_at"]}
            if provider in accepted else
            {"status": "not_connected_or_no_valid_review", "provider_name": PROVIDERS[provider]}
        )
        for provider in PROVIDERS
    }
    valid_count = sum(len(item["reviews"]) for item in comparisons)
    status = "ready" if comparisons and valid_count else "awaiting_reviews"
    return {
        "specialist_id": "sentiment_analyst",
        "specialist_name": "Sentiment Analyst",
        "status": status,
        "mode": "provider_independent_review_comparison",
        "providers": provider_status,
        "instruments": comparisons,
        "rejected_assessments": rejected,
        "summary": {
            "instruments_compared": len(comparisons),
            "agreement": sum(item["status"] == "agreement" for item in comparisons),
            "disagreement": sum(item["status"] == "disagreement" for item in comparisons),
            "single_provider": sum(item["status"] == "single_provider" for item in comparisons),
        },
        "provider_requests_triggered": 0,
        "market_data_requests_triggered": 0,
        "risk_authority": "none",
        "trade_authority": "none",
        "execution": "disabled",
        "human_approval_required": True,
        "limitations": [
            "Claude and ChatGPT are independent analysis resources; this endpoint does not call either service.",
            "Reviews must cite source material and include a timezone-aware analysis timestamp.",
            "Only fresh reviews (15 minutes or newer) are eligible; stale and malformed reviews are rejected.",
            "Agreement is agreement between model outputs, not proof that a claim is correct.",
            "No sentiment score, market forecast, trade proposal, or order is generated.",
        ],
    }


def sentiment_interface_status() -> dict[str, Any]:
    """Describe the provider boundary without implying connected API access."""
    return {
        "specialist_id": "sentiment_analyst",
        "specialist_name": "Sentiment Analyst",
        "status": "awaiting_reviews",
        "mode": "external_structured_reviews_only",
        "providers": [
            {"id": "claude", "name": "Claude", "connected": False, "api_calls_enabled": False},
            {"id": "chatgpt", "name": "ChatGPT", "connected": False, "api_calls_enabled": False},
        ],
        "accepted_review_schema": {
            "provider": "claude | chatgpt",
            "model": "optional model/version label",
            "analyzed_at": "timezone-aware ISO 8601 timestamp",
            "sources": [{"title": "source title", "url": "https://...", "published_at": "optional timestamp"}],
            "findings": [{
                "symbol": "MNQ | MES | MGC",
                "stance": "bullish | bearish | neutral | mixed | unavailable",
                "summary": "brief source-grounded rationale",
                "confidence": "low | medium | high",
            }],
        },
        "comparison_endpoint": "POST /api/agents/sentiment/compare",
        "provider_requests_triggered": 0,
        "market_data_requests_triggered": 0,
        "risk_authority": "none",
        "trade_authority": "none",
    }
