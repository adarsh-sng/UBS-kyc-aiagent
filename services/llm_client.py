"""Pluggable LLM client.

Provider + API key are read from environment variables so they can be
swapped without touching code:

    LLM_PROVIDER=anthropic|openai      (default: none -> template fallback)
    ANTHROPIC_API_KEY=...              (used when LLM_PROVIDER=anthropic)
    OPENAI_API_KEY=...                 (used when LLM_PROVIDER=openai)
    LLM_MODEL=...                      (optional override, provider-specific default otherwise)

If no provider/key is configured (the default for this MVP — no LLM keys
are required to run the demo), `complete()` falls back to a deterministic
template so the Manager/Communication agents always produce a usable
result offline. This keeps the demo reliable while leaving real
provider wiring a one-env-var swap away.
"""
from __future__ import annotations

import os
from typing import Optional

from services import privacy

DEFAULT_MODELS = {
    "anthropic": "claude-sonnet-5",
    "openai": "gpt-4o-mini",
}


def _provider() -> str | None:
    provider = os.environ.get("LLM_PROVIDER", "").strip().lower()
    return provider or None


def is_configured() -> bool:
    provider = _provider()
    if provider == "anthropic":
        return bool(os.environ.get("ANTHROPIC_API_KEY"))
    if provider == "openai":
        return bool(os.environ.get("OPENAI_API_KEY"))
    return False


def _call_anthropic(system: str, prompt: str) -> str:
    import anthropic  # imported lazily so it's only required when actually used

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    model = os.environ.get("LLM_MODEL", DEFAULT_MODELS["anthropic"])
    response = client.messages.create(
        model=model, max_tokens=300, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()


def _call_openai(system: str, prompt: str) -> str:
    import openai  # imported lazily so it's only required when actually used

    client = openai.OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    model = os.environ.get("LLM_MODEL", DEFAULT_MODELS["openai"])
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        max_tokens=300,
    )
    return response.choices[0].message.content.strip()


_PROVIDERS = {"anthropic": _call_anthropic, "openai": _call_openai}


def complete(
    system: str, prompt: str, template_fallback: str, record: Optional[dict] = None,
) -> str:
    """Returns an LLM completion if a provider+key is configured, else the
    caller-supplied deterministic template string. Never raises — falls
    back to the template on any provider error so a flaky/misconfigured
    key can't break the remediation loop.

    `record` (the current CorporateRecord snapshot, if the caller has one in
    scope) activates a defense-in-depth privacy guard: the outbound
    system/prompt and the inbound result are all scanned for the record's
    actual beneficial_owner/tax_id values and redacted if found — see
    services/privacy.py. This exists on top of already-PII-free prompt
    templates so a future template regression is caught, not shipped."""
    system = privacy.build_hardened_system_prompt(system)
    system = privacy.redact_pii(system, record)
    prompt = privacy.redact_pii(prompt, record)

    provider = _provider()
    if provider and is_configured():
        try:
            result = _PROVIDERS[provider](system, prompt)
        except Exception as exc:  # noqa: BLE001 — deliberate broad fallback
            result = f"{template_fallback}\n\n[LLM call failed, showing template fallback: {exc}]"
    else:
        result = template_fallback

    return privacy.redact_pii(result, record)
