import logging
import re
from datetime import date, timedelta
from typing import Any, Dict, List
from app.agent.llm import call_llm
from app.config import settings
from app.router.task_profile import NormalizedTaskData

logger = logging.getLogger(__name__)


def _repair_implicit_date_years(query: str, normalized: NormalizedTaskData) -> NormalizedTaskData:
    """Move model-invented years on yearless booking dates to the next occurrence."""
    if re.search(r"\b(?:19|20|21)\d{2}\b", query):
        return normalized

    constraints = dict(normalized.constraints)
    start_key = next((key for key in ("start_date", "check_in", "checkin", "arrival_date") if key in constraints), None)
    end_key = next((key for key in ("end_date", "check_out", "checkout", "departure_date") if key in constraints), None)
    if not start_key:
        return normalized

    try:
        original_start = date.fromisoformat(str(constraints[start_key]))
        original_end = date.fromisoformat(str(constraints[end_key])) if end_key else None
    except (TypeError, ValueError):
        return normalized

    today = date.today()
    year = max(today.year, original_start.year)
    while True:
        try:
            candidate = original_start.replace(year=year)
        except ValueError:  # Handle Feb 29 in a non-leap year.
            candidate = original_start.replace(year=year, day=28)
        if candidate >= today:
            break
        year += 1

    if candidate == original_start:
        return normalized

    constraints[start_key] = candidate.isoformat()
    if end_key and original_end:
        stay_length = max(0, (original_end - original_start).days)
        constraints[end_key] = (candidate + timedelta(days=stay_length)).isoformat()
    return normalized.model_copy(update={"constraints": constraints})

NORMALIZER_SYSTEM_PROMPT = """You are a strict task classification and normalization engine for a web agent.
Analyze the user's free-form web task query and extract structured fields in valid JSON matching this schema:
{
  "task_type": "search | navigation | extraction | form_fill | filtering | comparison | multi_site | other",
  "constraints": { "key": "value" },
  "target_sites": ["site1", "site2"] or ["unspecified"],
  "requires_comparison": boolean,
  "requires_decision": boolean,
  "requires_extraction": boolean,
  "requires_filtering": boolean,
  "requires_form_fill": boolean
}

Definitions:
- requires_comparison: true if user wants to compare multiple items, sites, or options.
- requires_decision: true if user requires evaluation or trade-off decisions between alternatives.
- requires_extraction: true if user explicitly wants structured data or tabular information scraped.
- requires_filtering: true if user specifies budget limits, specs, dates, star ratings, or filters.
- requires_form_fill: true if user mentions filling an input form, booking dates, or submitting fields.
- constraints: dictionary of all extracted constraints (e.g. budget, memory, location, nights).

Respond ONLY with valid JSON.
"""


def fallback_rule_based_normalizer(query: str) -> NormalizedTaskData:
    """Deterministic regex and keyword parser used when LLM call fails or offline."""
    q = query.lower()
    constraints: Dict[str, Any] = {}
    target_sites: List[str] = []

    # 1. Detect comparison
    requires_comparison = bool(
        re.search(r"\b(compare|comparison| vs |versus|better than|diff between)\b", q)
    )

    # 2. Detect decision
    requires_decision = bool(
        re.search(r"\b(recommend|best|suggest|which one|choose|decision)\b", q)
    )

    # 3. Detect form fill
    requires_form_fill = bool(
        re.search(r"\b(fill|form|book|signup|register|contact|submit)\b", q)
    )

    # 4. Detect extraction
    requires_extraction = bool(
        re.search(r"\b(extract|scrape|table|list of|catalog|export)\b", q)
    )

    # 5. Detect constraints & filtering
    # Budget
    budget_match = re.search(r"(?:under|below|less than|within)\s+([₹$€£]?\s*\d+(?:,\d+)*(?:\s*[kK]|(?:\s*thousand))?)", q)
    if budget_match:
        constraints["budget"] = budget_match.group(0).strip()

    # RAM / Specs
    ram_match = re.search(r"(\d+\s*(?:gb|tb)\s*(?:ram|rom|ssd|storage)?)", q)
    if ram_match:
        constraints["specs_memory"] = ram_match.group(0).strip()

    # Location
    loc_match = re.search(r"(?:in|at|to)\s+([a-zA-Z]+(?:\s+[a-zA-Z]+)?)", q)
    if loc_match:
        loc = loc_match.group(1).strip()
        if loc not in ("amazon", "flipkart", "google", "duckduckgo", "delhi", "pune", "goa", "india"):
            pass
        constraints["location"] = loc

    # Duration / nights
    nights_match = re.search(r"(\d+)\s*(?:night|nights|day|days)", q)
    if nights_match:
        constraints["duration"] = nights_match.group(0).strip()

    # Sites detection
    for domain in ["amazon", "flipkart", "ebay", "google", "duckduckgo", "booking", "makemytrip"]:
        if domain in q:
            target_sites.append(domain)

    if not target_sites:
        target_sites = ["unspecified"]

    requires_filtering = bool(constraints) or bool(
        re.search(r"\b(filter|under|below|with|rating|cheapest)\b", q)
    )

    # Task Type
    if len(target_sites) > 1 and "unspecified" not in target_sites:
        task_type = "multi_site"
    elif requires_comparison:
        task_type = "comparison"
    elif requires_form_fill:
        task_type = "form_fill"
    elif requires_filtering:
        task_type = "filtering"
    elif requires_extraction:
        task_type = "extraction"
    elif "search" in q or "find" in q:
        task_type = "search"
    else:
        task_type = "other"

    return NormalizedTaskData(
        task_type=task_type,
        constraints=constraints,
        target_sites=target_sites,
        requires_comparison=requires_comparison,
        requires_decision=requires_decision,
        requires_extraction=requires_extraction,
        requires_filtering=requires_filtering,
        requires_form_fill=requires_form_fill,
    )


async def normalize_task(query: str) -> NormalizedTaskData:
    """Normalizes natural language task using FAST_MODEL with rule-based fallback."""
    if not settings.LLM_API_KEY:
        logger.info("LLM_API_KEY not configured, using deterministic fallback normalizer.")
        return fallback_rule_based_normalizer(query)

    messages = [
        {
            "role": "system",
            "content": (
                NORMALIZER_SYSTEM_PROMPT
                + f"\nToday's date is {date.today().isoformat()}. For dates without a stated year, "
                "resolve them to the next future occurrence; do not invent a past year."
            ),
        },
        {"role": "user", "content": f"Task Query: {query}"},
    ]

    for attempt in range(2):
        try:
            parsed, _ = await call_llm(
                model=settings.FAST_MODEL,
                messages=messages,
                temperature=0.0,
            )
            normalized = NormalizedTaskData.model_validate(parsed)
            return _repair_implicit_date_years(query, normalized)
        except Exception as e:
            logger.warning(f"Task normalization attempt {attempt + 1} failed: {e}")

    logger.warning("Falling back to rule-based keyword task normalizer.")
    return fallback_rule_based_normalizer(query)
