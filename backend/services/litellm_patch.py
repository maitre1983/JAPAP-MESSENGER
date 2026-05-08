"""
iter237o — Monkey-patch emergentintegrations.LlmChat._execute_completion so the
sync `litellm.completion(...)` call runs in a worker thread, NOT on the asyncio
event loop. Without this, every LLM call (5–15s) freezes uvicorn → /api/health
times out and the entire app appears down while quiz/DCQ pool refreshes run.

This is a strict, additive shim — applied once at import time. The library API
(`await chat.send_message(...)`) is unchanged.
"""
from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

_PATCHED = False


def apply_patch() -> None:
    global _PATCHED
    if _PATCHED:
        return
    try:
        from emergentintegrations.llm import chat as _chat_mod  # type: ignore
        import litellm  # noqa: F401  — ensure imported

        _orig = _chat_mod.LlmChat._execute_completion  # for reference / restore

        async def _execute_completion_async(self, messages):
            # Re-build params exactly like the upstream method does, then run
            # the BLOCKING litellm.completion in a worker thread so the event
            # loop stays free to serve HTTP requests.
            params = {
                "model": f"{self.provider}/{self.model}",
                "messages": messages,
                "api_key": self.api_key,
            }
            try:
                if self._is_emergent_key(self.api_key):
                    from emergentintegrations.llm.chat import get_integration_proxy_url  # type: ignore
                    proxy_url = get_integration_proxy_url()
                    params["api_base"] = proxy_url + "/llm"
                    params["custom_llm_provider"] = "openai"
                    if self.provider == "gemini":
                        params["model"] = f"gemini/{self.model}"
                    else:
                        params["model"] = self.model
                    if getattr(self, "custom_headers", None):
                        params["extra_headers"] = self.custom_headers
            except Exception:  # noqa: BLE001 — be defensive on internal API drift
                pass
            try:
                params.update(getattr(self, "extra_params", {}) or {})
            except Exception:  # noqa: BLE001
                pass

            import litellm as _llm
            return await asyncio.to_thread(_llm.completion, **params)

        _chat_mod.LlmChat._execute_completion = _execute_completion_async  # type: ignore[assignment]
        _PATCHED = True
        logger.info("[iter237o] LlmChat._execute_completion patched (run in thread)")
    except Exception as e:  # noqa: BLE001
        logger.warning("[iter237o] LlmChat patch skipped: %s", e)


# Apply immediately on import.
apply_patch()
