"""Model provider request shapes.

A provider decides how a request is framed for an upstream family: which session header it needs, whether tool turns
carry reasoning_content back, and whether quiet calls (titles, summaries) switch thinking off. Providers do not decide
thinking strengths; reasoning.toml profiles own those fields, using the same upstream vocabulary.

Every provider here speaks the OpenAI-compatible /chat/completions protocol, so the transport stays shared. A catalog
entry picks a provider with `provider`; per-model fields still override the provider defaults for unusual gateways.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderProfile:
    # Header carrying a stable, hashed per-conversation id. Empty means no such header.
    session_header: str = ""
    # Whether assistant tool turns send reasoning_content back. DeepSeek-family models require it; others reject it.
    echo_reasoning: bool = False
    # Whether quiet calls that start a fresh prefix send thinking.type=disabled. Summary/wrap-up calls keep the Run's
    # level so the provider prefix cache still applies; this only affects calls that do not continue the prefix.
    quiet_disable_thinking: bool = False


PROVIDERS: dict[str, ProviderProfile] = {
    # Generic OpenAI-compatible gateways. No gateway-specific behaviour unless a model opts in explicitly.
    "openai": ProviderProfile(),
    # OpenCode Go routes and caches by a session header; its DeepSeek and Hy4 models accept echoed reasoning and
    # support thinking.type=disabled for quiet calls.
    "opencode-go": ProviderProfile(session_header="x-opencode-session", echo_reasoning=True, quiet_disable_thinking=True),
    # DeepSeek's own API requires reasoning_content on tool turns and supports thinking.type=disabled.
    "deepseek": ProviderProfile(echo_reasoning=True, quiet_disable_thinking=True),
}


def provider_profile(name: str) -> ProviderProfile:
    return PROVIDERS[name]
