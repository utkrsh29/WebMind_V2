import asyncio
import logging
import re
from typing import Any, Dict, Optional
from app.browser.session import BrowserSession
from app.schemas import ActionSchema

logger = logging.getLogger(__name__)

# Sensitive keywords indicating financial or irreversible transactions
IRREVERSIBLE_ACTION_KEYWORDS = [
    "pay now",
    "place order",
    "confirm payment",
    "buy now",
    "purchase",
    "complete order",
    "authorize payment",
]


class ActionExecutionResult:
    def __init__(
        self,
        success: bool,
        message: str = "",
        extracted_data: Optional[Any] = None,
        ask_user_prompt: Optional[str] = None,
        is_finished: bool = False,
        finish_result: Optional[Any] = None,
    ):
        self.success = success
        self.message = message
        self.extracted_data = extracted_data
        self.ask_user_prompt = ask_user_prompt
        self.is_finished = is_finished
        self.finish_result = finish_result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "extracted_data": self.extracted_data,
            "ask_user_prompt": self.ask_user_prompt,
            "is_finished": self.is_finished,
            "finish_result": self.finish_result,
        }


async def execute_action(
    session: BrowserSession,
    action: ActionSchema,
    extracted_store: Optional[Dict[str, Any]] = None,
) -> ActionExecutionResult:
    """Executes a structured action on the browser session with safety checks."""
    name = action.action

    # 1. Terminal / Dialog actions
    if name == "finish":
        return ActionExecutionResult(
            success=True,
            message="Task finished with final result",
            is_finished=True,
            finish_result=action.result,
        )

    if name == "ask_user":
        return ActionExecutionResult(
            success=True,
            message=f"Waiting for user response: {action.question}",
            ask_user_prompt=action.question or "Please confirm to proceed.",
        )

    page = session.page

    # 2. Navigation actions
    if name == "goto":
        if not action.url:
            return ActionExecutionResult(success=False, message="goto action requires a 'url' parameter.")
        if not session.is_url_allowed(action.url):
            return ActionExecutionResult(
                success=False,
                message=f"Security violation: Navigation to '{action.url}' is blocked.",
            )
        try:
            await page.goto(action.url, wait_until="domcontentloaded", timeout=25000)
            await session.dismiss_cookie_banners()
            return ActionExecutionResult(success=True, message=f"Navigated to {action.url}")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to navigate to {action.url}: {str(e)}")

    if name == "go_back":
        try:
            await page.go_back(timeout=10000)
            return ActionExecutionResult(success=True, message="Navigated back.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to go back: {str(e)}")

    if name == "new_tab":
        try:
            tab_idx = await session.new_tab(action.url)
            return ActionExecutionResult(success=True, message=f"Opened new tab at index {tab_idx}.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to open new tab: {str(e)}")

    if name == "switch_tab":
        if action.tab_index is None:
            return ActionExecutionResult(success=False, message="switch_tab requires 'tab_index'.")
        try:
            await session.switch_tab(action.tab_index)
            return ActionExecutionResult(success=True, message=f"Switched to tab {action.tab_index}.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to switch tab: {str(e)}")

    # 3. Interacting with Elements
    if name == "click":
        if action.element_id is None:
            return ActionExecutionResult(success=False, message="click requires 'element_id'.")
        selector = f"[data-webmind-id='{action.element_id}']"
        locator = page.locator(selector).first

        # Check safety guard for irreversible actions
        try:
            el_text = (await locator.inner_text(timeout=2000) or "").lower()
            if any(keyword in el_text for keyword in IRREVERSIBLE_ACTION_KEYWORDS):
                return ActionExecutionResult(
                    success=True,
                    message="Irreversible action intercepted for safety confirmation.",
                    ask_user_prompt=(
                        f"Safety Check: The agent is about to click '{el_text}'. "
                        "Do you authorize this action?"
                    ),
                )
        except Exception:
            pass

        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.click(timeout=5000)
            await asyncio.sleep(0.5)
            await session.dismiss_cookie_banners()
            return ActionExecutionResult(success=True, message=f"Clicked element [{action.element_id}].")
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                message=f"Failed to click element [{action.element_id}]: {str(e)}",
            )

    if name == "type":
        if action.element_id is None:
            return ActionExecutionResult(success=False, message="type requires 'element_id'.")
        text = action.text or ""

        # Sensitive data prevention check (e.g. credit cards)
        if re.search(r"\b(?:\d[ -]*?){13,16}\b", text):
            return ActionExecutionResult(
                success=False,
                message="Security Policy Error: Entering potential credit card numbers is prohibited.",
            )

        selector = f"[data-webmind-id='{action.element_id}']"
        locator = page.locator(selector).first
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.fill(text, timeout=5000)
            if action.submit:
                await locator.press("Enter")
                await asyncio.sleep(1.0)
            return ActionExecutionResult(
                success=True,
                message=f"Typed '{text}' into element [{action.element_id}]{' (Submitted)' if action.submit else ''}.",
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                message=f"Failed to type into element [{action.element_id}]: {str(e)}",
            )

    if name == "select":
        if action.element_id is None or action.value is None:
            return ActionExecutionResult(success=False, message="select requires 'element_id' and 'value'.")
        selector = f"[data-webmind-id='{action.element_id}']"
        try:
            await page.locator(selector).first.select_option(value=action.value, timeout=5000)
            return ActionExecutionResult(
                success=True,
                message=f"Selected '{action.value}' in element [{action.element_id}].",
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                message=f"Failed to select value on element [{action.element_id}]: {str(e)}",
            )

    if name == "press":
        key = action.key or "Enter"
        try:
            await page.keyboard.press(key)
            return ActionExecutionResult(success=True, message=f"Pressed keyboard key '{key}'.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to press key '{key}': {str(e)}")

    if name == "scroll":
        direction = action.direction or "down"
        delta = 600 if direction == "down" else -600
        try:
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(0.5)
            return ActionExecutionResult(success=True, message=f"Scrolled {direction}.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to scroll {direction}: {str(e)}")

    if name == "wait":
        ms = min(action.ms or 1000, 10000)
        await asyncio.sleep(ms / 1000.0)
        return ActionExecutionResult(success=True, message=f"Waited {ms}ms.")

    if name == "extract":
        desc = action.schema_or_description or "Extract key text data"
        try:
            extracted_text = await page.evaluate("""
                (() => {
                    const headings = Array.from(document.querySelectorAll('h1, h2, h3, .price, .title, table'))
                        .map(h => (h.innerText || '').trim())
                        .filter(t => t.length > 0)
                        .slice(0, 30);
                    return headings;
                })()
            """)
            if extracted_store is not None:
                extracted_store.setdefault("extracted_items", []).extend(extracted_text)
            return ActionExecutionResult(
                success=True,
                message=f"Extracted data according to '{desc}'.",
                extracted_data=extracted_text,
            )
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed extraction: {str(e)}")

    return ActionExecutionResult(success=False, message=f"Unknown action: {name}")
