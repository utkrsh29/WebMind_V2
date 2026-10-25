import json
import logging
from typing import Any, Dict, List, Optional, Tuple
from app.agent.llm import TokenUsage, call_llm
from app.browser.dom_snapshot import DOMSnapshot
from app.config import settings
from app.schemas import AgentTier, PlannerOutputSchema

logger = logging.getLogger(__name__)

SYSTEM_PROMPT_TEMPLATE = """You are WebMind, an autonomous web agent executing a task in a real web browser.
Your current tier: {tier}

TASK:
"{task_query}"

GOAL & CONSTRAINTS:
{constraints_str}

AVAILABLE ACTIONS:
- goto(url): Navigate directly to a URL (e.g. DuckDuckGo, Wikipedia, or an e-commerce site)
- click(element_id): Click an interactive element by its numeric [ID]
- type(element_id, text, submit=bool): Type text into an input element
- select(element_id, value): Select an option from a dropdown
- press(key): Press a keyboard key (e.g. 'Enter', 'Escape')
- scroll(direction): Scroll 'up' or 'down'
- go_back(): Navigate back in browser history
- wait(ms): Wait for page to update (e.g. 1500)
- extract(schema_or_description): Scrape structured data from current page
- finish(result): Complete the task. 'result' MUST be a structured dictionary or comparison list with items, prices, specs, and source URLs.
- ask_user(question): Ask the human for required credentials, 2FA, or confirmation before irreversible transactions.

RULES:
1. If no specific target site is provided and the browser is on about:blank or start, navigate first to "https://html.duckduckgo.com/html/" or search for candidates.
2. Refer to interactive elements ONLY by their numeric [ID] shown in brackets.
3. Every step MUST return a valid JSON object matching the exact schema:
{{
  "thought": "Your step-by-step reasoning, analyzing current page state and past failures",
  "next_action": {{
    "action": "goto | click | type | select | press | scroll | go_back | wait | extract | finish | ask_user",
    "url": "string (for goto)",
    "element_id": number (for click/type/select),
    "text": "string (for type)",
    "submit": true/false (for type),
    "value": "string (for select)",
    "key": "string (for press)",
    "direction": "up | down (for scroll)",
    "ms": number (for wait),
    "schema_or_description": "string (for extract)",
    "result": any (for finish),
    "question": "string (for ask_user)"
  }},
  "expected_state": {{
    "url_contains": "string or null",
    "text_present": "string or null",
    "element_present": "css selector or null"
  }}
}}
4. NEVER attempt to enter payment or credit card details.
5. Provide concise, actionable plans. {tier_specific_instruction}
"""

TIER_INSTRUCTIONS = {
    "FAST": "Act quickly and directly with minimal overhead. Choose straightforward element clicks.",
    "GENERAL": "Analyze elements carefully. Check if filters or search refinements are needed.",
    "REASONING": "Critically verify if current search results fulfill all constraints. Plan multi-site checks if needed, and perform self-critique before finalizing results.",
}


class Planner:
    def __init__(self, tier: AgentTier):
        self.tier = tier
        if tier == "FAST":
            self.model = settings.FAST_MODEL
        elif tier == "GENERAL":
            self.model = settings.GENERAL_MODEL
        else:
            self.model = settings.REASONING_MODEL

    def get_system_prompt(self, task_query: str, constraints: Dict[str, Any]) -> str:
        constraints_str = json.dumps(constraints, indent=2) if constraints else "None specified"
        tier_instruction = TIER_INSTRUCTIONS.get(self.tier, "")
        return SYSTEM_PROMPT_TEMPLATE.format(
            tier=self.tier,
            task_query=task_query,
            constraints_str=constraints_str,
            tier_specific_instruction=tier_instruction,
        )

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
    ) -> Tuple[PlannerOutputSchema, TokenUsage]:
        """Calls the tier LLM to decide the next action based on page observation."""
        system_prompt = self.get_system_prompt(task_query, constraints)

        # Build user message with history & current observation
        history_str = ""
        if action_history:
            history_lines = []
            for h in action_history[-4:]:  # last 4 steps
                history_lines.append(f"Step {h['step']}: Action {h['action']} -> Result: {h['status']}")
            history_str = "\nRECENT ACTIONS:\n" + "\n".join(history_lines)

        hints_str = ""
        if experience_hints:
            hints_str = "\nHINTS FROM PAST SIMILAR TASKS:\n" + "\n".join(f"- {hint}" for hint in experience_hints)

        failure_str = ""
        if last_failure_reason:
            failure_str = f"\nATTENTION: The previous action failed because: {last_failure_reason}. Change your approach or choose a different element!"

        extracted_str = ""
        if extracted_data:
            extracted_str = f"\nCURRENTLY ACCUMULATED DATA: {json.dumps(extracted_data)[:400]}"

        page_view = current_snapshot.to_formatted_prompt()

        user_content = (
            f"CURRENT STEP: {step_number}\n"
            f"{history_str}"
            f"{hints_str}"
            f"{failure_str}"
            f"{extracted_str}\n\n"
            f"OBSERVED PAGE STATE:\n{page_view}\n\n"
            f"What is your next action? Respond ONLY in valid JSON matching the schema."
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        # Use screenshot only if tier is GENERAL or REASONING and snapshot exists
        screenshot = None
        if self.tier in ("GENERAL", "REASONING") and current_snapshot.screenshot_bytes:
            screenshot = current_snapshot.screenshot_bytes

        parsed, usage = await call_llm(
            model=self.model,
            messages=messages,
            temperature=0.0,
            screenshot_bytes=screenshot,
        )

        output = PlannerOutputSchema.model_validate(parsed)
        return output, usage
