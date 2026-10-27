import json
import logging
from typing import Any, Dict, List, Optional
from playwright.async_api import Page

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# JS: Discover interactive + readable elements and tag them with data-webmind-id
# Inspired by BrowserOS accessibility-tree approach: collect tag, role, name,
# value, checked state, disabled, and bounding box for every visible element.
# ────────────────────────────────────────────────────────────────────────────
DOM_INTERACTIVE_EXTRACT_JS = """
(() => {
    // Clean up previous marks
    document.querySelectorAll('[data-webmind-id]').forEach(el => el.removeAttribute('data-webmind-id'));
    document.querySelectorAll('.webmind-mark-badge').forEach(el => el.remove());

    const INTERACTIVE_TAGS = new Set(['button','input','select','textarea','a','details','summary']);
    const INTERACTIVE_ROLES = new Set([
        'button','link','checkbox','menuitem','menuitemcheckbox','menuitemradio',
        'option','tab','textbox','searchbox','combobox','listbox','radio',
        'switch','slider','spinbutton','treeitem','gridcell','row','columnheader'
    ]);

    const isVisible = (el) => {
        if (!el || !el.isConnected) return false;
        const rect = el.getBoundingClientRect();
        if (rect.width < 2 || rect.height < 2) return false;
        const style = window.getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none' || parseFloat(style.opacity) < 0.05) return false;
        // Check if it's inside the visible viewport
        if (rect.bottom < 0 || rect.top > window.innerHeight + 200) return false;
        return true;
    };

    const isInteractive = (el) => {
        const tag = el.tagName.toLowerCase();
        if (INTERACTIVE_TAGS.has(tag)) return true;
        const role = (el.getAttribute('role') || '').toLowerCase();
        if (INTERACTIVE_ROLES.has(role)) return true;
        if (el.hasAttribute('onclick')) return true;
        const ti = el.getAttribute('tabindex');
        if (ti !== null && ti !== '-1') return true;
        if (el.contentEditable === 'true') return true;
        return false;
    };

    const getLabel = (el) => {
        // aria-label > aria-labelledby > placeholder > title > innerText > value > name attr
        const ariaLabel = el.getAttribute('aria-label') || '';
        if (ariaLabel.trim()) return ariaLabel.trim();

        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
            const labelEl = document.getElementById(labelledBy);
            if (labelEl) return (labelEl.innerText || '').trim();
        }

        const placeholder = el.getAttribute('placeholder') || '';
        if (placeholder.trim()) return placeholder.trim();

        const title = el.getAttribute('title') || '';
        if (title.trim()) return title.trim();

        const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
        if (text) return text.slice(0, 100);

        return (el.getAttribute('name') || '').trim().slice(0, 60);
    };

    const collectFromRoot = (root, items, idCounter) => {
        const candidates = Array.from(root.querySelectorAll('*'));
        for (const el of candidates) {
            if (!isInteractive(el) || !isVisible(el)) continue;
            // Skip children whose interactive parent is already tagged
            if (el.closest('[data-webmind-id]')) continue;

            const id = idCounter.val++;
            el.setAttribute('data-webmind-id', id.toString());
            const rect = el.getBoundingClientRect();
            const tag = el.tagName.toLowerCase();
            const role = el.getAttribute('role') || tag;
            const label = String(getLabel(el) ?? '').slice(0, 120);
            const inputType = tag === 'input' ? (el.type || 'text') : '';
            const autocomplete = (el.getAttribute('autocomplete') || '').toLowerCase();
            const fieldIdentity = [
                el.getAttribute('name') || '', el.getAttribute('id') || '',
                el.getAttribute('placeholder') || '', el.getAttribute('aria-label') || '', autocomplete
            ].join(' ').toLowerCase();
            const rawValue = String(el.value !== undefined ? el.value : (el.getAttribute('value') || '')).slice(0, 80);
            const sensitiveValue = ['password', 'email', 'tel'].includes(inputType) ||
                /username|current-password|new-password|email|phone|tel|full.?name|first.?name|last.?name|address|cc-|cvv|otp|one.?time|verification|account/.test(fieldIdentity);
            const value = sensitiveValue && rawValue ? '[REDACTED]' : rawValue;
            const checked = el.checked !== undefined ? el.checked : undefined;
            const disabled = el.disabled || el.getAttribute('aria-disabled') === 'true';
            const href = (tag === 'a' ? el.getAttribute('href') || '' : '').slice(0, 100);
            const placeholder = (el.getAttribute('placeholder') || '').slice(0, 120);
            const options = tag === 'select'
                ? Array.from(el.options).filter(option => !option.disabled).slice(0, 30).map(option => ({
                    label: (option.label || option.textContent || '').trim().slice(0, 100),
                    value: String(option.value || '').slice(0, 100)
                }))
                : [];

            items.push({
                id,
                tag,
                role,
                name: label,
                value,
                checked,
                disabled,
                href,
                inputType,
                placeholder,
                options,
                rect: {
                    x: Math.round(rect.x),
                    y: Math.round(rect.y),
                    width: Math.round(rect.width),
                    height: Math.round(rect.height)
                }
            });

            // Recurse into shadow DOM
            if (el.shadowRoot) {
                collectFromRoot(el.shadowRoot, items, idCounter);
            }
        }
    };

    const items = [];
    const idCounter = { val: 1 };
    collectFromRoot(document, items, idCounter);

    return items;
})()
"""

INJECT_SOM_BADGES_JS = """
(() => {
    document.querySelectorAll('.webmind-mark-badge').forEach(el => el.remove());
    const badgeContainer = document.createElement('div');
    badgeContainer.id = 'webmind-badge-container';
    badgeContainer.className = 'webmind-mark-badge';
    badgeContainer.style.cssText = 'position:fixed;top:0;left:0;z-index:2147483647;pointer-events:none;';

    document.querySelectorAll('[data-webmind-id]').forEach(el => {
        const id = el.getAttribute('data-webmind-id');
        const rect = el.getBoundingClientRect();
        if (rect.width <= 0 || rect.height <= 0) return;

        const badge = document.createElement('div');
        badge.className = 'webmind-mark-badge';
        badge.textContent = id;
        badge.style.cssText = [
            'position:fixed',
            `top:${Math.max(0, rect.top - 4)}px`,
            `left:${Math.max(0, rect.left - 4)}px`,
            'background:#facc15',
            'color:#000',
            'font:bold 11px/1 monospace',
            'padding:1px 4px',
            'border-radius:3px',
            'border:1px solid #000',
            'box-shadow:0 1px 3px rgba(0,0,0,.5)',
            'z-index:2147483647',
            'pointer-events:none'
        ].join(';');
        badgeContainer.appendChild(badge);
    });

    document.body.appendChild(badgeContainer);
})()
"""

REMOVE_SOM_BADGES_JS = """
(() => { document.querySelectorAll('.webmind-mark-badge, #webmind-badge-container').forEach(el => el.remove()); })()
"""

PAGE_CONTENT_EXTRACT_JS = """
(() => {
    const seen = new Set();
    const lines = [];

    // Headings give structure
    document.querySelectorAll('h1,h2,h3,h4').forEach(h => {
        const t = h.innerText.trim().replace(/\\s+/g, ' ');
        if (t && !seen.has(t)) { seen.add(t); lines.push(`[${h.tagName}] ${t}`); }
    });

    // Paragraphs and list items
    document.querySelectorAll('p,li,td,th,label,span[class*=price],span[class*=title],div[class*=price]').forEach(el => {
        const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
        if (t.length > 5 && t.length < 300 && !seen.has(t)) {
            seen.add(t);
            lines.push(t);
        }
    });

    // Alert / status messages
    document.querySelectorAll('[role=alert],[role=status],[aria-live],.error,.success,.notification,.toast').forEach(el => {
        const t = (el.innerText || '').trim();
        if (t && !seen.has(t)) { seen.add(t); lines.push(`[ALERT] ${t}`); }
    });

    return lines.slice(0, 120).join('\\n');
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
        page_structure: Optional[str] = None,
    ):
        self.url = url
        self.title = title
        self.elements = elements
        self.text_summary = text_summary
        self.screenshot_bytes = screenshot_bytes
        self.page_structure = page_structure or ""

    def to_formatted_prompt(self, max_elements: int = 60, max_text_chars: int = 2000) -> str:
        """Generates a compact accessibility-tree style prompt for the LLM."""
        lines = [
            f"PAGE URL: {self.url}",
            f"PAGE TITLE: {self.title}",
        ]

        if self.page_structure:
            lines.append(f"\nPAGE STRUCTURE:\n{self.page_structure[:600]}")

        lines.append(f"\nINTERACTIVE ELEMENTS ({len(self.elements)} total, showing first {min(len(self.elements), max_elements)}):")
        lines.append("Format: [ID] <tag role='role'> \"name\" [val:'value'] [checked] [disabled] (href)")

        for el in self.elements[:max_elements]:
            id_ = el["id"]
            tag = el["tag"]
            role = el.get("role", tag)
            name = el.get("name") or "(no label)"
            val_part = f" [val:'{el['value']}']" if el.get("value") else ""
            checked_part = " [checked]" if el.get("checked") else ""
            disabled_part = " [disabled]" if el.get("disabled") else ""
            href_part = f" ({el['href']})" if el.get("href") else ""
            input_type = f" type={el['inputType']}" if el.get("inputType") and el["inputType"] not in ("", "text") else ""
            lines.append(f"  [{id_}] <{tag}{input_type} role='{role}'> \"{name}\"{val_part}{checked_part}{disabled_part}{href_part}")

        if len(self.elements) > max_elements:
            lines.append(f"  ... ({len(self.elements) - max_elements} more elements not shown)")

        lines.append("\nPAGE VISIBLE CONTENT:")
        clean_summary = self.text_summary[:max_text_chars].strip()
        lines.append(clean_summary if clean_summary else "(Empty or loading)")

        return "\n".join(lines)


async def wait_for_page_settle(page: Page, timeout_ms: int = 3000) -> None:
    """Waits for the page network/DOM to settle rather than using a blind sleep.
    Mirrors BrowserOS Neo's 'readiness handled for you' philosophy."""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        # Also wait for network idle (short timeout - not mandatory)
        await page.wait_for_load_state("networkidle", timeout=1500)
    except Exception:
        pass


async def capture_dom_snapshot(page: Page, include_som_screenshot: bool = True) -> DOMSnapshot:
    """Captures interactive elements, text summary, and marked screenshot from active page."""
    try:
        url = page.url
        title = await page.title()
    except Exception as e:
        logger.warning(f"Error reading page metadata: {e}")
        url = "about:blank"
        title = ""

    # 1. Discover and tag interactive elements (accessibility-tree style)
    try:
        elements = await page.evaluate(DOM_INTERACTIVE_EXTRACT_JS)
        if not isinstance(elements, list):
            elements = []
    except Exception as e:
        logger.error(f"Error evaluating interactive DOM elements: {e}")
        elements = []

    # 2. Extract rich visible text summary
    try:
        text_summary = await page.evaluate(PAGE_CONTENT_EXTRACT_JS)
        if not text_summary:
            # Fallback to body innerText
            text_summary = await page.evaluate("""
                (() => {
                    const text = document.body ? document.body.innerText : '';
                    return text.split('\\n')
                        .map(line => line.trim())
                        .filter(line => line.length > 3)
                        .slice(0, 100)
                        .join('\\n');
                })()
            """)
    except Exception:
        text_summary = ""

    # 3. Page structure (headings/sections for navigation context)
    try:
        page_structure = await page.evaluate("""
            (() => {
                const parts = [];
                document.querySelectorAll('h1,h2,h3,nav a,[role=navigation] a,[role=menubar] [role=menuitem]').forEach(el => {
                    const t = (el.innerText || '').trim().replace(/\\s+/g, ' ');
                    if (t && t.length < 80) parts.push(t);
                });
                return [...new Set(parts)].slice(0, 30).join(' | ');
            })()
        """)
    except Exception:
        page_structure = ""

    # 4. Screenshot with Set-of-Marks badges if requested
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
        page_structure=page_structure,
    )
