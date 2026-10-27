import asyncio
import base64
import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse
from app.browser.session import BrowserSession
from app.schemas import ActionSchema

logger = logging.getLogger(__name__)

# Sensitive keywords indicating financial or irreversible transactions
IRREVERSIBLE_ACTION_KEYWORDS = [
    "book now",
    "reserve",
    "finish booking",
    "complete your booking",
    "reserve now",
    "confirm booking",
    "complete booking",
    "confirm reservation",
    "complete reservation",
    "pay now",
    "place order",
    "confirm payment",
    "buy now",
    "purchase",
    "complete order",
    "authorize payment",
    "confirm purchase",
    "checkout",
]

# Rich extraction JS — pulls structured data from the current page
RICH_EXTRACT_JS = r"""
(() => {
    const out = {};

    // Headings
    const headings = Array.from(document.querySelectorAll('h1,h2,h3'))
        .map(h => h.innerText.trim()).filter(Boolean);
    if (headings.length) out.headings = headings.slice(0, 20);

    // Prices
    const prices = Array.from(document.querySelectorAll(
        '[class*=price],[class*=Price],[class*=cost],[data-price],[itemprop=price],.price'
    )).map(el => el.innerText.trim()).filter(Boolean);
    if (prices.length) out.prices = [...new Set(prices)].slice(0, 30);

    // Tables
    const tables = [];
    document.querySelectorAll('table').forEach(table => {
        const rows = [];
        table.querySelectorAll('tr').forEach(tr => {
            const cells = Array.from(tr.querySelectorAll('th,td')).map(c => c.innerText.trim());
            if (cells.some(c => c)) rows.push(cells);
        });
        if (rows.length > 0) tables.push(rows.slice(0, 20));
    });
    if (tables.length) out.tables = tables.slice(0, 5);

    // Lists
    const lists = [];
    document.querySelectorAll('ul,ol').forEach(list => {
        const items = Array.from(list.querySelectorAll('li'))
            .map(li => li.innerText.trim().slice(0, 150))
            .filter(Boolean);
        if (items.length > 1) lists.push(items.slice(0, 20));
    });
    if (lists.length) out.lists = lists.slice(0, 5);

    // Key-value metadata (definition lists, spec tables)
    const kvPairs = {};
    document.querySelectorAll('dl').forEach(dl => {
        const terms = dl.querySelectorAll('dt');
        const defs = dl.querySelectorAll('dd');
        terms.forEach((dt, i) => {
            if (defs[i]) kvPairs[dt.innerText.trim()] = defs[i].innerText.trim().slice(0, 200);
        });
    });
    if (Object.keys(kvPairs).length) out.specs = kvPairs;

    // Search result cards with product links. Keep each price connected to its
    // product and URL instead of returning one unlabeled page-wide price list.
    const productLinks = Array.from(document.querySelectorAll('a[href]'))
        .filter(a => /\/(?:dp|gp\/product|p)\//i.test(a.getAttribute('href') || ''));
    const products = [];
    const seenProducts = new Set();
    for (const link of productLinks) {
        const url = new URL(link.getAttribute('href'), window.location.href).href;
        if (seenProducts.has(url)) continue;
        seenProducts.add(url);
        const card = link.closest('[data-component-type="s-search-result"], article, li, [data-id]') || link.parentElement;
        const text = (card?.innerText || link.innerText || '').replace(/\s+/g, ' ').trim();
        const price = text.match(/(?:₹|Rs\.?|INR)\s?[\d,]+(?:\.\d{1,2})?/i)?.[0] || '';
        const title = (link.innerText || link.getAttribute('title') || text).trim().slice(0, 240);
        if (title) products.push({ title, price, url });
        if (products.length >= 12) break;
    }
    if (products.length) out.products = products;

    // Page text paragraphs (top 10)
    const paragraphs = Array.from(document.querySelectorAll('p,article,main'))
        .map(el => el.innerText.trim().replace(/\s+/g, ' '))
        .filter(t => t.length > 30 && t.length < 500)
        .slice(0, 10);
    if (paragraphs.length) out.paragraphs = paragraphs;

    out.url = window.location.href;
    out.title = document.title;

    return out;
})()
"""


class ActionExecutionResult:
    def __init__(
        self,
        success: bool,
        message: str = "",
        extracted_data: Optional[Any] = None,
        ask_user_prompt: Optional[str] = None,
        is_login: bool = False,
        is_finished: bool = False,
        finish_result: Optional[Any] = None,
        screenshot_b64: Optional[str] = None,
        requires_approval: bool = False,
    ):
        self.success = success
        self.message = message
        self.extracted_data = extracted_data
        self.ask_user_prompt = ask_user_prompt
        self.is_login = is_login
        self.is_finished = is_finished
        self.finish_result = finish_result
        self.screenshot_b64 = screenshot_b64
        self.requires_approval = requires_approval

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "message": self.message,
            "extracted_data": self.extracted_data,
            "ask_user_prompt": self.ask_user_prompt,
            "is_login": self.is_login,
            "is_finished": self.is_finished,
            "finish_result": self.finish_result,
            "screenshot_b64": self.screenshot_b64,
            "requires_approval": self.requires_approval,
        }


async def _get_element_locator(page, element_id: int):
    """Returns a Playwright locator for a webmind-tagged element."""
    selector = f"[data-webmind-id='{element_id}']"
    return page.locator(selector).first


async def execute_action(
    session: BrowserSession,
    action: ActionSchema,
    extracted_store: Optional[Dict[str, Any]] = None,
    approved_irreversible: bool = False,
) -> ActionExecutionResult:
    """Executes a structured action on the browser session with safety checks."""
    name = action.action

    # ── 1. Terminal / Dialog actions ─────────────────────────────────────────
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
            is_login=bool(action.is_login),
        )

    page = session.page

    # ── 2. Navigation ─────────────────────────────────────────────────────────
    if name == "goto":
        if not action.url:
            return ActionExecutionResult(success=False, message="goto requires a 'url' parameter.")
        if not session.is_url_allowed(action.url):
            return ActionExecutionResult(
                success=False,
                message=f"Security violation: Navigation to '{action.url}' is blocked.",
            )
        try:
            await page.goto(action.url, wait_until="domcontentloaded", timeout=28000)
            await _wait_for_settle(page)
            await session.dismiss_cookie_banners()
            return ActionExecutionResult(success=True, message=f"Navigated to {action.url}")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to navigate to {action.url}: {str(e)}")

    if name == "go_back":
        try:
            await page.go_back(timeout=10000)
            await _wait_for_settle(page)
            return ActionExecutionResult(success=True, message="Navigated back.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to go back: {str(e)}")

    if name == "new_tab":
        try:
            tab_idx = await session.new_tab(action.url)
            await _wait_for_settle(page)
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

    # ── 3. Element Interactions ───────────────────────────────────────────────
    if name == "click":
        if action.element_id is None:
            return ActionExecutionResult(success=False, message="click requires 'element_id'.")
        locator = await _get_element_locator(page, action.element_id)

        # Safety guard: check for irreversible action keywords
        try:
            el_text = (await locator.inner_text(timeout=2000) or "").lower()
            if not approved_irreversible and any(keyword in el_text for keyword in IRREVERSIBLE_ACTION_KEYWORDS):
                return ActionExecutionResult(
                    success=True,
                    message="Irreversible action intercepted for safety confirmation.",
                    ask_user_prompt=(
                        f"Safety Check: The agent is about to click '{el_text.strip()[:100]}' on "
                        f"{page.url}. Review the hotel, stay dates, cancellation terms, and final total "
                        "shown in the browser. Approve only if they match your request."
                    ),
                    requires_approval=True,
                )
        except Exception:
            pass

        popup_waiter = asyncio.create_task(page.wait_for_event("popup"))
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.click(timeout=6000)
            try:
                popup = await asyncio.wait_for(popup_waiter, timeout=1.5)
            except asyncio.TimeoutError:
                popup = None
            if popup:
                popup.set_default_timeout(10000)
                popup.set_default_navigation_timeout(20000)
                session.pages.append(popup)
                session.active_tab_index = len(session.pages) - 1
                await _wait_for_settle(popup)
            else:
                await _wait_for_settle(page, short=True)
            await session.dismiss_cookie_banners()
            return ActionExecutionResult(success=True, message=f"Clicked element [{action.element_id}].")
        except Exception as e:
            if not popup_waiter.done():
                popup_waiter.cancel()
            # An approved transaction click must never be replayed via fallback.
            if approved_irreversible:
                return ActionExecutionResult(
                    success=False,
                    message=f"Approved click could not be verified: {str(e)}",
                )
            # Fallback: try JavaScript click
            try:
                await page.evaluate(
                    f"document.querySelector('[data-webmind-id=\"{action.element_id}\"]')?.click()"
                )
                await _wait_for_settle(page, short=True)
                return ActionExecutionResult(success=True, message=f"JS-clicked element [{action.element_id}].")
            except Exception:
                pass
            return ActionExecutionResult(
                success=False,
                message=f"Failed to click element [{action.element_id}]: {str(e)}",
            )

    if name == "hover":
        if action.element_id is None:
            return ActionExecutionResult(success=False, message="hover requires 'element_id'.")
        try:
            locator = await _get_element_locator(page, action.element_id)
            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.hover(timeout=5000)
            await asyncio.sleep(0.6)  # brief pause for hover menus to open
            return ActionExecutionResult(success=True, message=f"Hovered over element [{action.element_id}].")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to hover element [{action.element_id}]: {str(e)}")

    if name == "type":
        if action.element_id is None:
            return ActionExecutionResult(success=False, message="type requires 'element_id'.")
        text = action.text or ""

        # Sensitive data prevention (credit card numbers)
        if re.search(r"\b(?:\d[ -]*?){13,16}\b", text):
            return ActionExecutionResult(
                success=False,
                message="Security Policy Error: Entering potential credit card numbers is prohibited.",
            )

        locator = await _get_element_locator(page, action.element_id)
        try:
            locator_count = await locator.count()
            if locator_count != 1:
                return ActionExecutionResult(
                    success=False,
                    message=(
                        f"The observed field reference [{action.element_id}] is stale or ambiguous "
                        f"(matched {locator_count} elements). Re-observe the page and choose a fresh field."
                    ),
                )
            if not await locator.is_visible():
                return ActionExecutionResult(
                    success=False,
                    message=f"The observed field [{action.element_id}] is no longer visible. Re-observe the page.",
                )
            if not await locator.is_enabled():
                return ActionExecutionResult(
                    success=False,
                    message=f"The observed field [{action.element_id}] is disabled. Choose an enabled field.",
                )
            await locator.scroll_into_view_if_needed(timeout=3000)
            await locator.click(timeout=3000)          # focus first
            await locator.fill(text, timeout=6000)
            # Verify the field directly. Some booking pages render labels and
            # values in frames/portals that are not represented in the text
            # summary used by the general page verifier.
            try:
                actual_value = await locator.input_value(timeout=2000)
            except Exception:
                actual_value = await locator.evaluate(
                    "el => el.isContentEditable ? el.innerText : (el.value ?? '')"
                )
            if str(actual_value) != text:
                return ActionExecutionResult(
                    success=False,
                    message=f"Field [{action.element_id}] did not retain the requested value.",
                )
            if action.submit:
                await locator.press("Enter")
                await _wait_for_settle(page)
            return ActionExecutionResult(
                success=True,
                message=f"Verified text entry into field [{action.element_id}]"
                        + (" (submitted)" if action.submit else ""),
            )
        except Exception as e:
            return ActionExecutionResult(
                success=False,
                message=f"Failed to type into element [{action.element_id}]: {str(e)}",
            )

    if name == "select":
        if action.element_id is None or action.value is None:
            return ActionExecutionResult(success=False, message="select requires 'element_id' and 'value'.")
        locator = await _get_element_locator(page, action.element_id)
        try:
            await locator.select_option(value=action.value, timeout=5000)
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
            await _wait_for_settle(page, short=True)
            return ActionExecutionResult(success=True, message=f"Pressed key '{key}'.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to press key '{key}': {str(e)}")

    # ── 4. Scroll / Page Control ──────────────────────────────────────────────
    if name == "scroll":
        direction = action.direction or "down"
        amount = action.amount or 600
        delta = amount if direction == "down" else -amount
        try:
            await page.mouse.wheel(0, delta)
            await asyncio.sleep(0.4)
            return ActionExecutionResult(success=True, message=f"Scrolled {direction} {amount}px.")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to scroll {direction}: {str(e)}")

    if name == "scroll_to":
        if action.element_id is None:
            return ActionExecutionResult(success=False, message="scroll_to requires 'element_id'.")
        try:
            locator = await _get_element_locator(page, action.element_id)
            await locator.scroll_into_view_if_needed(timeout=5000)
            return ActionExecutionResult(success=True, message=f"Scrolled to element [{action.element_id}].")
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to scroll to element [{action.element_id}]: {str(e)}")

    if name == "wait":
        ms = min(action.ms or 1000, 8000)
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=ms)
        except Exception:
            await asyncio.sleep(ms / 1000.0)
        return ActionExecutionResult(success=True, message=f"Waited {ms}ms.")

    # ── 5. Extraction ─────────────────────────────────────────────────────────
    if name == "extract":
        desc = action.schema_or_description or "Extract key text data"
        try:
            extracted = await page.evaluate(RICH_EXTRACT_JS)
            if not isinstance(extracted, dict):
                extracted = {"raw": str(extracted)}
            if extracted_store is not None:
                url = str(extracted.get("url") or page.url)
                source = urlparse(url).hostname or url
                record = {
                    "source": source.removeprefix("www."),
                    "url": url,
                    "title": str(extracted.get("title") or page.title())[:240],
                    "data": {key: value for key, value in extracted.items() if key not in ("url", "title")},
                }
                sources = extracted_store.setdefault("sources", [])
                existing_index = next(
                    (index for index, item in enumerate(sources) if item.get("url") == url),
                    None,
                )
                if existing_index is None:
                    sources.append(record)
                    del sources[:-10]
                else:
                    sources[existing_index] = record
            return ActionExecutionResult(
                success=True,
                message=f"Extracted structured data: {desc}",
                extracted_data=extracted,
            )
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed extraction: {str(e)}")

    # ── 6. Screenshot ─────────────────────────────────────────────────────────
    if name == "screenshot":
        try:
            img_bytes = await page.screenshot(type="jpeg", quality=70)
            b64 = base64.b64encode(img_bytes).decode("utf-8")
            return ActionExecutionResult(
                success=True,
                message="Screenshot captured.",
                screenshot_b64=b64,
            )
        except Exception as e:
            return ActionExecutionResult(success=False, message=f"Failed to capture screenshot: {str(e)}")

    return ActionExecutionResult(success=False, message=f"Unknown action: '{name}'")


async def _wait_for_settle(page, short: bool = False) -> None:
    """Smart settle wait: wait for DOM content, then optionally network idle."""
    timeout = 1500 if short else 3000
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    if not short:
        try:
            await page.wait_for_load_state("networkidle", timeout=1500)
        except Exception:
            pass
