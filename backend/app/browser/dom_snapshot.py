import json
import logging
from typing import Any, Dict, List, Optional
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# JS Snippet to discover interactive elements, tag them with data-webmind-id,
# extract their bounding boxes, and inject visual Set-of-Marks labels.
DOM_INTERACTIVE_EXTRACT_JS = """
(() => {
    // Clean up any previous markers
    document.querySelectorAll('.webmind-mark-badge').forEach(el => el.remove());

    const isVisible = (el) => {
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width <= 2 || rect.height <= 2) return false;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none' || style.opacity === '0') return false;
        return true;
    };

    const isInteractive = (el) => {
        const tag = el.tagName.toLowerCase();
        if (['button', 'input', 'select', 'textarea', 'a'].includes(tag)) return true;
        const role = el.getAttribute('role');
        if (['button', 'link', 'checkbox', 'menuitem', 'option', 'tab', 'textbox', 'searchbox', 'combobox'].includes(role)) return true;
        if (el.hasAttribute('onclick') || el.hasAttribute('tabindex') && el.getAttribute('tabindex') !== '-1') return true;
        return false;
    };

    const candidates = Array.from(document.querySelectorAll('*')).filter(el => isInteractive(el) && isVisible(el));
    
    // Filter duplicates/children if parent is already an interactive button/link
    const filtered = [];
    for (const el of candidates) {
        const parent = el.parentElement;
        if (parent && isInteractive(parent) && parent.tagName.toLowerCase() === 'button') {
            continue;
        }
        filtered.push(el);
    }

    const items = [];
    let idCounter = 1;

    for (const el of filtered) {
        const id = idCounter++;
        el.setAttribute('data-webmind-id', id.toString());
        const rect = el.getBoundingClientRect();
        
        // Compute descriptive label
        let text = el.innerText || el.textContent || '';
        text = text.trim().replace(/\\s+/g, ' ').slice(0, 80);
        const placeholder = el.getAttribute('placeholder') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        const title = el.getAttribute('title') || '';
        const value = el.value || '';
        const name = ariaLabel || placeholder || title || text || value || '';

        items.push({
            id: id,
            tag: el.tagName.toLowerCase(),
            role: el.getAttribute('role') || el.tagName.toLowerCase(),
            name: name.slice(0, 100),
            value: (el.value || '').slice(0, 50),
            rect: {
                x: Math.round(rect.x),
                y: Math.round(rect.y),
                width: Math.round(rect.width),
                height: Math.round(rect.height)
            }
        });
    }

    return items;
})()
"""

INJECT_SOM_BADGES_JS = """
(() => {
    const badgeContainer = document.createElement('div');
    badgeContainer.id = 'webmind-badge-container';
    badgeContainer.className = 'webmind-mark-badge';
    badgeContainer.style.position = 'absolute';
    badgeContainer.style.top = '0';
    badgeContainer.style.left = '0';
    badgeContainer.style.zIndex = '2147483647';
    badgeContainer.style.pointerEvents = 'none';

    document.querySelectorAll('[data-webmind-id]').forEach(el => {
        const id = el.getAttribute('data-webmind-id');
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;

        const badge = document.createElement('div');
        badge.className = 'webmind-mark-badge';
        badge.textContent = id;
        badge.style.position = 'fixed';
        badge.style.top = Math.max(0, rect.top - 4) + 'px';
        badge.style.left = Math.max(0, rect.left - 4) + 'px';
        badge.style.backgroundColor = '#facc15';
        badge.style.color = '#000000';
        badge.style.fontSize = '11px';
        badge.style.fontWeight = 'bold';
        badge.style.padding = '1px 4px';
        badge.style.borderRadius = '3px';
        badge.style.border = '1px solid #000';
        badge.style.boxShadow = '0 1px 3px rgba(0,0,0,0.5)';
        badge.style.zIndex = '2147483647';
        badge.style.pointerEvents = 'none';

        badgeContainer.appendChild(badge);
    });

    document.body.appendChild(badgeContainer);
})()
"""

REMOVE_SOM_BADGES_JS = """
(() => {
    document.querySelectorAll('.webmind-mark-badge').forEach(el => el.remove());
})()
"""


class DOMSnapshot:
    def __init__(
        self,
        url: str,
        title: str,
        elements: List[Dict[str, Any]],
        text_summary: str,
        screenshot_bytes: Optional[bytes] = None,
    ):
        self.url = url
        self.title = title
        self.elements = elements
        self.text_summary = text_summary
        self.screenshot_bytes = screenshot_bytes

    def to_formatted_prompt(self, max_elements: int = 50, max_text_chars: int = 1500) -> str:
        """Generates a compact, token-budgeted text representation for the LLM."""
        lines = [
            f"PAGE URL: {self.url}",
            f"PAGE TITLE: {self.title}",
            "\nINTERACTIVE ELEMENTS (Reference by [ID]):",
        ]

        for el in self.elements[:max_elements]:
            id_ = el["id"]
            tag = el["tag"]
            role = el["role"]
            name = el["name"] or "(no text)"
            val = f" [val: '{el['value']}']" if el.get("value") else ""
            lines.append(f"  [{id_}] <{tag} role='{role}'> \"{name}\"{val}")

        if len(self.elements) > max_elements:
            lines.append(f"  ... ({len(self.elements) - max_elements} more interactive elements omitted)")

        lines.append("\nPAGE VISIBLE CONTENT SUMMARY:")
        clean_summary = self.text_summary[:max_text_chars].strip()
        lines.append(clean_summary if clean_summary else "(Empty or loading)")

        return "\n".join(lines)


async def capture_dom_snapshot(page: Page, include_som_screenshot: bool = True) -> DOMSnapshot:
    """Captures interactive elements, text summary, and marked screenshot from active page."""
    try:
        url = page.url
        title = await page.title()
    except Exception as e:
        logger.warning(f"Error reading page metadata: {e}")
        url = "about:blank"
        title = ""

    # 1. Discover and tag interactive elements
    try:
        elements = await page.evaluate(DOM_INTERACTIVE_EXTRACT_JS)
    except Exception as e:
        logger.error(f"Error evaluating interactive DOM elements: {e}")
        elements = []

    # 2. Extract visible text summary
    try:
        text_summary = await page.evaluate("""
            (() => {
                const text = document.body ? document.body.innerText : '';
                return text.split('\\n')
                    .map(line => line.trim())
                    .filter(line => line.length > 0)
                    .slice(0, 100)
                    .join('\\n');
            })()
        """)
    except Exception:
        text_summary = ""

    # 3. Screenshot with Set-of-Marks badges if requested
    screenshot_bytes = None
    if include_som_screenshot:
        try:
            await page.evaluate(INJECT_SOM_BADGES_JS)
            screenshot_bytes = await page.screenshot(type="jpeg", quality=70)
            await page.evaluate(REMOVE_SOM_BADGES_JS)
        except Exception as e:
            logger.warning(f"Failed to take SoM screenshot: {e}")
            try:
                screenshot_bytes = await page.screenshot(type="jpeg", quality=60)
            except Exception:
                screenshot_bytes = None

    return DOMSnapshot(
        url=url,
        title=title,
        elements=elements,
        text_summary=text_summary,
        screenshot_bytes=screenshot_bytes,
    )
