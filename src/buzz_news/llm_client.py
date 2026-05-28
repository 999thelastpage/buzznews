import json
from dataclasses import dataclass

from buzz_news.config import get_settings

settings = get_settings()


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    model: str


@dataclass(frozen=True)
class LLMResult:
    provider: str
    model: str
    data: dict
    input_tokens: int
    output_tokens: int


def estimate_tokens(text: str) -> int:
    return max(1, (len(text or "") + 2) // 3)


def parse_provider_spec(raw: str) -> ProviderSpec:
    if ":" not in raw:
        raise ValueError(f"Provider spec must be provider:model, got {raw!r}")
    provider, model = raw.split(":", 1)
    provider = provider.strip().lower()
    model = model.strip()
    if not provider or not model:
        raise ValueError(f"Provider spec must be provider:model, got {raw!r}")
    return ProviderSpec(provider, model)


def parse_provider_list(raw: str) -> list[ProviderSpec]:
    return [parse_provider_spec(part.strip()) for part in (raw or "").split(",") if part.strip()]


def parse_json_tolerant(raw: str) -> dict:
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        from json_repair import repair_json

        parsed = json.loads(repair_json(raw))
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, dict):
                return item
    raise ValueError("LLM response did not contain a JSON object")


def _openai_compat_settings(provider: str) -> tuple[str, str]:
    if provider == "deepseek":
        return settings.DEEPSEEK_BASE_URL.rstrip("/") + "/v1/chat/completions", settings.DEEPSEEK_API_KEY
    if provider == "cerebras":
        return settings.CEREBRAS_BASE_URL.rstrip("/") + "/chat/completions", settings.CEREBRAS_API_KEY
    if provider == "groq":
        return settings.GROQ_BASE_URL.rstrip("/") + "/chat/completions", settings.GROQ_API_KEY
    raise ValueError(f"Unsupported OpenAI-compatible provider={provider!r}")


def _call_openai_compatible(
    spec: ProviderSpec,
    prompt: str,
    *,
    temperature: float,
    max_tokens: int,
) -> LLMResult:
    import httpx

    url, api_key = _openai_compat_settings(spec.provider)
    if not api_key:
        raise RuntimeError(f"{spec.provider.upper()}_API_KEY not configured")

    payload = {
        "model": spec.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    # Groq's Qwen endpoint can reject strict JSON mode even for JSON prompts;
    # keep the same prompt contract and let the tolerant parser handle it.
    if not (spec.provider == "groq" and spec.model.startswith("qwen/")):
        payload["response_format"] = {"type": "json_object"}

    response = httpx.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=60.0,
    )
    response.raise_for_status()
    payload = response.json()
    content = payload["choices"][0]["message"]["content"]
    usage = payload.get("usage") or {}
    return LLMResult(
        provider=spec.provider,
        model=spec.model,
        data=parse_json_tolerant(content),
        input_tokens=int(usage.get("prompt_tokens") or estimate_tokens(prompt)),
        output_tokens=int(usage.get("completion_tokens") or estimate_tokens(content)),
    )


def _call_gemini(spec: ProviderSpec, prompt: str, *, temperature: float, max_tokens: int) -> LLMResult:
    from google import genai

    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not configured")
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    response = client.models.generate_content(
        model=spec.model,
        contents=prompt,
        config={
            "temperature": temperature,
            "max_output_tokens": max_tokens,
            "response_mime_type": "application/json",
        },
    )
    text = response.text or ""
    return LLMResult(
        provider=spec.provider,
        model=spec.model,
        data=parse_json_tolerant(text),
        input_tokens=estimate_tokens(prompt),
        output_tokens=estimate_tokens(text),
    )


def _call_anthropic(spec: ProviderSpec, prompt: str, *, temperature: float, max_tokens: int) -> LLMResult:
    import anthropic

    if not settings.ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY not configured")
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = client.messages.create(
        model=spec.model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )
    text = response.content[0].text
    return LLMResult(
        provider=spec.provider,
        model=spec.model,
        data=parse_json_tolerant(text),
        input_tokens=estimate_tokens(prompt),
        output_tokens=estimate_tokens(text),
    )


def generate_json(
    spec: ProviderSpec,
    prompt: str,
    *,
    temperature: float = 0.3,
    max_tokens: int = 3000,
) -> LLMResult:
    if spec.provider in {"deepseek", "cerebras", "groq"}:
        return _call_openai_compatible(spec, prompt, temperature=temperature, max_tokens=max_tokens)
    if spec.provider == "gemini":
        return _call_gemini(spec, prompt, temperature=temperature, max_tokens=max_tokens)
    if spec.provider == "anthropic":
        return _call_anthropic(spec, prompt, temperature=temperature, max_tokens=max_tokens)
    raise ValueError(f"Unsupported LLM provider={spec.provider!r}")
