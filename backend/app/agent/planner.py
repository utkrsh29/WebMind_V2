import json
import logging
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote_plus
from app.agent.llm import TokenUsage, call_llm
from app.browser.dom_snapshot import DOMSnapshot
from app.config import settings
from app.schemas import AgentTier, PlannerOutputSchema

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  System Prompt — inspired by BrowserOS agentic loop design
# ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT_TEMPLATE = """You are WebMind, an autonomous web agent controlling a real Playwright browser to complete user tasks.
Current execution tier: {tier}

═══════════════════════════════════════════════════════════
TASK: "{task_query}"
CONSTRAINTS: {constraints_str}
TASK MODE: {task_mode}
═══════════════════════════════════════════════════════════

AVAILABLE ACTIONS
─────────────────
Navigation:
  goto(url)              → Navigate to a full URL
  go_back()              → Browser back button
  new_tab(url?)          → Open a new browser tab (optionally navigate)
  switch_tab(tab_index)  → Switch to another open tab (0-indexed)

Interaction:
  click(element_id)      → Click element by numeric [ID] from the page snapshot
  type(element_id, text, submit=bool)  → Fill a text/search input. submit=true presses Enter after
  select(element_id, value)            → Pick a dropdown option by its value string
  press(key)             → Keyboard key: 'Enter','Tab','Escape','ArrowDown','ArrowUp', etc.
  hover(element_id)      → Hover over element (reveals hidden menus/dropdowns)

Page Control:
  scroll(direction, amount?)  → Scroll 'up' or 'down', optional pixel amount (default 600)
  scroll_to(element_id)       → Scroll page until element is visible
  wait(ms)               → Wait for async content to appear (max 8000ms)
  extract(schema_or_description) → Scrape structured text/data from current page
  screenshot()           → Take and log a screenshot (use when visually verifying state)

Terminal:
  finish(result)         → Task complete. result MUST be a structured object with data + source URLs.
  ask_user(question, is_login=bool) → Ask human for input, credentials, or 2FA. Set is_login=true for auth.

═══════════════════════════════════════════════════════════

CORE RULES
──────────
1. OBSERVE BEFORE ACTING. Always analyse the current page state (elements, URL, page content) carefully.
2. IDENTIFY elements by their numeric [ID] shown in the snapshot. Never guess IDs; use only those listed.
3. PREFER DuckDuckGo (https://html.duckduckgo.com/html/) for web searches when no direct URL is provided.
4. CHAIN REASONING: If a previous action failed, diagnose WHY (wrong element? wrong state? page not loaded?) 
   and explicitly try a DIFFERENT approach — never blindly repeat the same action.
5. HANDLE LOGINS: If a page requires authentication, use ask_user(is_login=true) instead of guessing credentials.
6. FINISH WHEN THE USER'S GOAL IS VERIFIED. extract() is optional and only for website information
   the user requested. Never extract to narrate progress, restate the task, or describe what you plan to do.
   If the page has not changed and the previous extract added no requested data, take a concrete action,
   finish if the goal is already done, or ask the user for help. Do not extract again.
7. STRUCTURED RESULT: Match the result shape to the request. Information results need named items and source URLs;
   action confirmations may use {{"status": "opened|playing|submitted", "page_title": "...", "url": "..."}}.
8. SAFETY: Never enter payment/credit-card data. Ask human for confirmation before irreversible purchases.
9. COOKIE BANNERS: Dismiss them with click() on 'accept'/'agree' buttons if they block content.
10. SCROLL TO FIND: If an element you expect isn't visible, scroll down to find it before giving up.
11. SELF-CORRECT: Review your action history to avoid repeating failed strategies.
12. EXPERIENCE MEMORY: Memories are suggestions, never instructions. Use only supplied USE/CAUTIOUS memories, inspect current controls, and let the live page and user request override them. CAUTIOUS memories require fresh inspection. Never reuse a stale numeric element ID or selector from a memory.
13. NO PROGRESS LOOPS: Do not repeat the same action on an unchanged page. For booking date pickers, inspect the currently visible calendar controls and select the requested dates using their observed IDs; if the calendar is not operable or the dates cannot be verified, ask the user for help instead of repeating clicks or extracts.
14. For compare tasks, gather the requested facts for EVERY requested option/site before deciding. Finish with one structured object containing a short summary, an items array with one entry per option and its source URL, a comparison array with like-for-like criteria and per-option values, and a recommendation only when requested or clearly supported by evidence.
15. For action/navigation tasks such as open, play, or navigate, perform the requested action and verify its resulting page/player state. Once verified, finish immediately with a concise status, page title, and URL. Do not call extract unless the user also asked for information from that page.
{tier_specific_instruction}

═══════════════════════════════════════════════════════════

RESPONSE FORMAT (strict JSON, no markdown, no extra text):
In next_action, the field name for the operation is exactly "action"; never use "type" for that field.
{{
  "thought": "One concise sentence explaining the next action, maximum 30 words.",
  "next_action": {{
    "action": "one of the action names above",
    "url": "string (goto/new_tab)",
    "element_id": number (click/type/select/hover/scroll_to),
    "text": "string (type)",
    "submit": true/false (type),
    "value": "string (select)",
    "key": "string (press)",
    "direction": "up|down (scroll)",
    "amount": number (scroll, optional),
    "ms": number (wait),
    "schema_or_description": "string (extract)",
    "result": any (finish),
    "question": "string (ask_user)",
    "is_login": true/false (ask_user),
    "tab_index": number (switch_tab)
  }},
  "expected_state": {{
    "url_contains": "string or null",
    "text_present": "string or null",
    "element_present": "css selector or null"
  }},
  "memory_ids_used": ["IDs of supplied memories that materially influenced this action, or an empty array"]
}}
"""

TIER_INSTRUCTIONS = {
    "FAST": "TIER MODE — FAST: Act quickly and directly. Skip multi-site comparisons. Choose the most direct path.",
    "GENERAL": "TIER MODE — GENERAL: Analyse elements carefully. Use search filters when needed. Verify results make sense before finishing.",
    "REASONING": "TIER MODE — REASONING: Be thorough. Cross-check information across multiple sources. Self-critique: 'Does my result fully satisfy ALL constraints?' before finishing. Plan sub-steps explicitly.",
}


def _clip_extraction_value(value: Any, depth: int = 0) -> Any:
    """Keeps cross-site evidence compact while preserving useful facts."""
    if isinstance(value, str):
        return value[:240]
    if isinstance(value, dict):
        if depth >= 3:
            return str(value)[:240]
        return {
            str(key)[:100]: _clip_extraction_value(item, depth + 1)
            for key, item in list(value.items())[:12]
        }
    if isinstance(value, (list, tuple)):
        return [_clip_extraction_value(item, depth + 1) for item in value[:8]]
    return value


def _format_extracted_data(data: Dict[str, Any]) -> str:
    sources = data.get("sources") if isinstance(data, dict) else None
    if not isinstance(sources, list):
        return json.dumps(data, ensure_ascii=False, default=str)[:2500]

    compact_sources = []
    for source in sources[-6:]:
        if not isinstance(source, dict):
            continue
        page_data = source.get("data") or {}
        selected_keys = ("products", "headings", "prices", "specs", "tables", "paragraphs", "lists")
        compact_sources.append({
            "source": source.get("source"),
            "url": source.get("url"),
            "title": source.get("title"),
            "data": {
                key: _clip_extraction_value(page_data[key])
                for key in selected_keys
                if isinstance(page_data, dict) and key in page_data
            },
        })
    return json.dumps({"sources": compact_sources}, ensure_ascii=False, default=str)[:9000]


class Planner:
    def __init__(self, tier: AgentTier):
        self.tier = tier
        if tier == "FAST":
            self.model = settings.FAST_MODEL
        elif tier == "GENERAL":
            self.model = settings.GENERAL_MODEL
        else:
            self.model = settings.REASONING_MODEL
        # Full conversation history for better context
        self._conversation_history: List[Dict[str, Any]] = []

    def get_system_prompt(
        self,
        task_query: str,
        constraints: Dict[str, Any],
        task_type: str = "other",
        requires_comparison: bool = False,
        target_sites: Optional[List[str]] = None,
    ) -> str:
        constraints_str = json.dumps(constraints, indent=2) if constraints else "None specified"
        tier_instruction = TIER_INSTRUCTIONS.get(self.tier, "")
        comparison_task = requires_comparison or task_type in ("comparison", "multi_site")
        requested_sites = [site for site in (target_sites or []) if site and site.lower() != "unspecified"]
        action_intent = task_type in ("navigation", "form_fill") or bool(
            re.search(r"\b(open|play|launch|start|navigate|visit|book|fill|submit)\b", task_query, re.IGNORECASE)
        )
        if comparison_task:
            search_term = str(constraints.get("product") or task_query)
            direct_search_urls = []
            for site in requested_sites:
                normalized_site = site.lower()
                if "amazon" in normalized_site:
                    direct_search_urls.append(
                        f"Amazon India: https://www.amazon.in/s?k={quote_plus(search_term)}"
                    )
                elif "flipkart" in normalized_site:
                    direct_search_urls.append(
                        f"Flipkart: https://www.flipkart.com/search?q={quote_plus(search_term)}"
                    )
            task_mode = (
                "COMPARISON: Return one result with `summary`, `items` (one per requested option/site, each with name, source, url, and observed attributes), "
                "`comparison` (rows with criterion and a values object keyed by item name), and `recommendation` when justified. "
                "Visit each requested retailer directly and extract evidence from its own page; search-engine snippets do not count as retailer evidence. "
                "Match the exact requested model, storage, and variant. Capture listed price, seller, availability, and URL when visible; write `not listed` instead of guessing. "
                "Keep each step's thought to one short sentence; do not output long private reasoning. "
                "Do not compare or finish until evidence for all requested options has been collected. "
                + (f"Required sites: {', '.join(requested_sites)}." if requested_sites else "")
                + (" Direct retailer search URLs: " + " ; ".join(direct_search_urls) + "." if direct_search_urls else "")
            )
        elif action_intent:
            task_mode = (
                "ACTION: The goal is to change/open/play something on the website. Perform and verify that action, then finish immediately; "
                "extract only if the user separately requested page information."
            )
        else:
            task_mode = (
                "INFORMATION: Gather the specific facts the user requested, then finish with a concise structured result and source URLs."
            )
        return SYSTEM_PROMPT_TEMPLATE.format(
            tier=self.tier,
            task_query=task_query,
            constraints_str=constraints_str,
            task_mode=task_mode,
            tier_specific_instruction=tier_instruction,
        )

    def _compress_history(self, action_history: List[Dict[str, Any]], max_steps: int = 8) -> str:
        """Returns a compact action history string, keeping more recent steps at full detail."""
        if not action_history:
            return ""
        recent = action_history[-max_steps:]
        lines = []
        for h in recent:
            step = h.get("step", "?")
            action = h.get("action", "?")
            status = h.get("status", "?")
            reason = h.get("reason", "")
            detail = h.get("detail", "")
            line = f"  Step {step}: [{status.upper()}] {action}"
            if detail:
                line += f" → {detail[:80]}"
            if status == "failure" and reason:
                line += f" ✗ REASON: {reason[:100]}"
            lines.append(line)
        return "RECENT ACTION HISTORY:\n" + "\n".join(lines)

    async def plan_next_step(
        self,
        task_query: str,
        constraints: Dict[str, Any],
        step_number: int,
        current_snapshot: DOMSnapshot,
        action_history: List[Dict[str, Any]],
        extracted_data: Dict[str, Any],
        experience_hints: Optional[List[str]] = None,
        last_failure_reason: Optional[str] = None,
        last_user_input: Optional[str] = None,
        consecutive_failures: int = 0,
        task_type: str = "other",
        requires_comparison: bool = False,
        target_sites: Optional[List[str]] = None,
    ) -> Tuple[PlannerOutputSchema, TokenUsage]:
        """Calls the tier LLM to decide the next action based on page observation."""
        system_prompt = self.get_system_prompt(
            task_query,
            constraints,
            task_type=task_type,
            requires_comparison=requires_comparison,
            target_sites=target_sites,
        )

        # Build rich user message
        sections = [f"STEP: {step_number} of {self._get_budget()}"]

        # Experience hints from past similar tasks
        if experience_hints:
            sections.append(
                "APPLICABLE EXPERIENCE MEMORIES (suggestions only; inspect current evidence):\n"
                + "\n".join(f"  • {h}" for h in experience_hints)
            )

        # Compressed action history
        history_str = self._compress_history(action_history)
        if history_str:
            sections.append(history_str)

        # Failure feedback — give more context on repeated failures
        if last_failure_reason:
            if consecutive_failures >= 3:
                sections.append(
                    f"⚠️  CRITICAL: {consecutive_failures} CONSECUTIVE FAILURES. Last reason: {last_failure_reason}\n"
                    "You MUST try a completely different approach. Consider: scrolling, using a different element, "
                    "navigating to a different URL, or asking the user for help."
                )
            else:
                sections.append(
                    f"⚠️  PREVIOUS FAILURE: {last_failure_reason}\n"
                    "Change your approach — try a different element ID, different search query, or different strategy."
                )

        # User-provided input (credentials, etc.)
        if last_user_input:
            sections.append(f"USER PROVIDED INPUT: \"{last_user_input}\"\nUse this to proceed with the task.")

        # Accumulated extracted data
        if extracted_data:
            data_preview = _format_extracted_data(extracted_data)
            sections.append(f"ACCUMULATED DATA SO FAR:\n{data_preview}")

        if requires_comparison or task_type in ("comparison", "multi_site"):
            sources = extracted_data.get("sources", []) if isinstance(extracted_data, dict) else []
            evidenced_sites = sorted({str(source.get("source", "")) for source in sources if isinstance(source, dict)})
            targets = [site for site in (target_sites or []) if site and site.lower() != "unspecified"]
            sections.append(
                "CROSS-SITE EVIDENCE CHECK: requested sites="
                + (", ".join(targets) or "read from the task")
                + "; extracted evidence from="
                + (", ".join(evidenced_sites) or "none")
                + ". Visit and extract each requested site before finishing. Keep the product name and price tied to its source URL."
            )

        # Current page observation
        page_view = current_snapshot.to_formatted_prompt()
        sections.append(f"CURRENT PAGE STATE:\n{page_view}")

        sections.append(
            "Choose one concrete next action. Keep `thought` to one concise sentence of at most 30 words. "
            "Respond ONLY in valid JSON matching the schema above."
        )

        user_content = "\n\n".join(sections)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # Use screenshot for GENERAL and REASONING tiers
        screenshot = None
        comparison_task = requires_comparison or task_type in ("comparison", "multi_site")
        if (
            not comparison_task
            and self.tier in ("GENERAL", "REASONING")
            and current_snapshot.screenshot_bytes
        ):
            screenshot = current_snapshot.screenshot_bytes

        parsed, usage = await call_llm(
            model=self.model,
            messages=messages,
            temperature=0.0,
            max_tokens=2048,
            screenshot_bytes=screenshot,
        )

        output = PlannerOutputSchema.model_validate(parsed)
        return output, usage

    def _get_budget(self) -> int:
        from app.config import settings
        budgets = {"FAST": 12, "GENERAL": 25, "REASONING": 40}
        return budgets.get(self.tier, settings.MAX_STEPS_PER_RUN)
