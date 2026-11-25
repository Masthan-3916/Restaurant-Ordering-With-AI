import logging

import httpx
from google import genai
from google.genai import errors, types

from app.config.settings import settings
from app.services.errors import AppError

logger = logging.getLogger("food_ai.gemini")


def gemini_error(exc: errors.APIError | httpx.TransportError, operation: str) -> AppError:
    if isinstance(exc, errors.APIError):
        code = exc.code
        logger.warning("Gemini %s failed with HTTP status %s", operation, code)
        if code == 429:
            return AppError("ai_rate_limited", "Gemini's rate limit or quota was reached. Please try again later.", 429)
        if code in (401, 403):
            return AppError("ai_access_denied", "Gemini denied access. Check the API key and model permissions.", 503)
        if code == 404:
            return AppError("ai_model_not_found", "The configured Gemini model is unavailable. Check the model setting.", 503)
        if code in (500, 502, 503, 504):
            return AppError("ai_unavailable", "Gemini is temporarily unavailable or experiencing high demand. Please try again shortly.", 503)
        return AppError("ai_request_failed", "Gemini rejected the request. Check the server logs and model configuration.", 503)
    logger.warning("Gemini %s failed due to a network error (%s)", operation, type(exc).__name__)
    return AppError("ai_unavailable", "Could not connect to Gemini. Please check the network and try again.", 503)


class GeminiClient:
    def __init__(self) -> None:
        if not settings.gemini_api_key:
            raise AppError("ai_unavailable", "Gemini is not configured. Set GEMINI_API_KEY and try again.", 503)
        self.client = genai.Client(
            api_key=settings.gemini_api_key,
            http_options=types.HttpOptions(
                timeout=60000,
                retry_options=types.HttpRetryOptions(
                    attempts=3,
                    initial_delay=1,
                    max_delay=4,
                    http_status_codes=[408, 429, 500, 502, 503, 504],
                ),
            ),
        )

    def answer(self, prompt: str, system_instruction: str | None = None) -> str:
        try:
            response = self.client.models.generate_content(
                model=settings.gemini_model,
                contents=prompt,
                config=types.GenerateContentConfig(system_instruction=system_instruction, temperature=0.3),
            )
        except (errors.APIError, httpx.TransportError) as exc:
            raise gemini_error(exc, "generation") from exc
        if not response.text:
            raise AppError("ai_empty_response", "Gemini returned an empty response. Please try again.", 503)
        return response.text.strip()

    def embed(self, text: str, task_type: str = "RETRIEVAL_DOCUMENT") -> list[float]:
        try:
            result = self.client.models.embed_content(
                model=settings.gemini_embedding_model,
                contents=text,
                config=types.EmbedContentConfig(task_type=task_type),
            )
        except (errors.APIError, httpx.TransportError) as exc:
            raise gemini_error(exc, "embedding") from exc
        if not result.embeddings or not result.embeddings[0].values:
            raise AppError("embedding_empty_response", "Gemini returned an empty embedding.", 503)
        return list(result.embeddings[0].values)
