import asyncio
import base64
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional, Tuple, Union
import litellm
from app.config import settings

logger = logging.getLogger(__name__)

# Suppress litellm noisy telemetry / banner
litellm.telemetry = False
litellm.drop_params = True


class TokenUsage:
    def __init__(self, prompt_tokens: int = 0, completion_tokens: int = 0, total_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens

    def add(self, other: "TokenUsage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.total_tokens += other.total_tokens


def extract_json_from_text(text: str) -> Dict[str, Any]:
    """Robustly extracts JSON from an LLM response string."""
    text = text.strip()
    # Try direct parse
    try:
        return json.loads(text)
    except Exception:
        pass

    # Try markdown json block
    match = re.search(r"```(?:json)?\s*(\{[\s\S]*?\})\s*```", text)
    if match:
        try:
            return json.loads(match.group(1))
        except Exception:
            pass

    # Try finding outermost braces
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        sub = text[start : end + 1]
        try:
            return json.loads(sub)
        except Exception:
            pass

    raise ValueError(f"Could not extract valid JSON from LLM response: {text[:200]}...")


async def call_llm(
    model: str,
    messages: List[Dict[str, Any]],
    temperature: float = 0.0,
    max_tokens: int = 2048,
    response_format: Optional[Dict[str, Any]] = None,
    screenshot_bytes: Optional[bytes] = None,
) -> Tuple[Dict[str, Any], TokenUsage]:
    """Invokes LiteLLM with structured messages, optional vision, and returns parsed JSON + usage."""
    settings.setup_litellm_env()

    # If screenshot is provided and model supports vision, append to last user message
    if screenshot_bytes:
        b64_img = base64.b64encode(screenshot_bytes).decode("utf-8")
        img_url = f"data:image/jpeg;base64,{b64_img}"
        
        # Modify the last message to multipart content
        last_msg = messages[-1]
        text_content = last_msg["content"]
        if isinstance(text_content, str):
            last_msg["content"] = [
                {"type": "text", "text": text_content},
                {"type": "image_url", "image_url": {"url": img_url}},
            ]

    # Gemini 3.8 strongly recommends temperature 1.0 to prevent infinite loops and 503 stalls
    effective_temp = 1.0 if "gemini-3" in model.lower() else temperature

    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": effective_temp,
        "max_tokens": max_tokens,
        "timeout": 45.0,
    }

    if response_format:
        kwargs["response_format"] = response_format

    last_err = None
    for attempt in range(3):
        try:
            response = await litellm.acompletion(**kwargs)
            content = response.choices[0].message.content or ""
            parsed = extract_json_from_text(content)

            usage = TokenUsage()
            if hasattr(response, "usage") and response.usage:
                usage.prompt_tokens = getattr(response.usage, "prompt_tokens", 0) or 0
                usage.completion_tokens = getattr(response.usage, "completion_tokens", 0) or 0
                usage.total_tokens = getattr(response.usage, "total_tokens", 0) or (usage.prompt_tokens + usage.completion_tokens)

            return parsed, usage
        except Exception as e:
            err_str = str(e).lower()
            last_err = e
            
            # 1. Hard daily limit exceeded - cannot recover by waiting seconds
            if "per day" in err_str or "requests per day" in err_str:
                logger.error(f"Daily quota hard limit exceeded on {model}: {e}")
                raise e

            # 2. Per-minute limit or transient 429 - recoverable by waiting retryDelay
            if any(k in err_str for k in ["rate_limit", "ratelimit", "quota", "429", "resource_exhausted"]):
                wait_sec = 14.0
                match = re.search(r"retry in\s+([\d\.]+)\s*s", err_str)
                if not match:
                    match = re.search(r'retrydelay":\s*"(\d+)s"', err_str)
                if match:
                    try:
                        wait_sec = float(match.group(1)) + 2.0
                    except Exception:
                        wait_sec = 14.0
                
                logger.warning(
                    f"Gemini per-minute rate limit hit on {model}. Waiting {wait_sec:.1f}s before retry attempt {attempt + 1}/3..."
                )
                await asyncio.sleep(wait_sec)
                continue

            logger.warning(f"LiteLLM attempt {attempt + 1} failed on {model}: {e}")
            await asyncio.sleep(2.0 * (attempt + 1))

    logger.error(f"LiteLLM completion error on {model} after 3 attempts: {last_err}")
    raise last_err
