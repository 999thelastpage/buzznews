import logging
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from buzz_news.config import get_settings

settings = get_settings()
log = logging.getLogger("buzz_news.alerts")


def _buzz_compatible_payload(kind: str, payload: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    cluster_id = payload.get("cluster_id") or 0
    message = payload.get("message") or payload.get("note") or kind
    return {
        "cluster_id": cluster_id,
        "fired_at": payload.get("fired_at") or now,
        "headline_guess": payload.get("headline_guess") or f"BuzzNews alert: {kind}",
        "sources": payload.get("sources") or [{"name": "BuzzNews", "url": ""}],
        "velocity": payload.get("velocity") or 0,
        "distinct_authoritative": payload.get("distinct_authoritative") or 0,
        "composite": payload.get("composite") or 0,
        "category": payload.get("category") or "ops",
        "region": payload.get("region") or "GLOBAL",
        "kind": kind,
        "alert_type": kind,
        "message": message,
        **payload,
    }


def _alert_text(payload: dict) -> str:
    return (
        f"BuzzNews alert: {payload.get('kind') or payload.get('alert_type')}\n"
        f"provider={payload.get('provider')} model={payload.get('model')}\n"
        f"cluster_id={payload.get('cluster_id')} article_id={payload.get('article_id')}\n"
        f"estimated_input_tokens={payload.get('estimated_input_tokens')}\n"
        f"message={payload.get('message', '')}"
    )


def _telegram_request(url: str, payload: dict) -> tuple[str, dict] | None:
    parsed = urlparse(url)
    if parsed.netloc != "api.telegram.org" or not parsed.path.endswith("/sendMessage"):
        return None
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    data = {
        "chat_id": query.get("chat_id", ""),
        "text": _alert_text(payload),
    }
    clean_url = urlunparse(parsed._replace(query=urlencode({k: v for k, v in query.items() if k not in data})))
    return clean_url, data


async def emit_alert(kind: str, payload: dict) -> bool:
    alert_payload = _buzz_compatible_payload(kind, payload)
    log.warning("ALERT_%s %s", kind.upper(), alert_payload)

    if not settings.BUZZ_WEBHOOK_URL:
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            telegram = _telegram_request(settings.BUZZ_WEBHOOK_URL, alert_payload)
            if telegram:
                url, data = telegram
                response = await client.post(url, data=data)
            else:
                response = await client.post(settings.BUZZ_WEBHOOK_URL, json=alert_payload)
            if response.status_code < 300:
                log.info("Alert webhook delivered kind=%s", kind)
                return True
            log.warning("Alert webhook failed kind=%s status=%s", kind, response.status_code)
            return False
    except Exception as exc:
        log.error("Alert webhook error kind=%s error=%s", kind, exc)
        return False
