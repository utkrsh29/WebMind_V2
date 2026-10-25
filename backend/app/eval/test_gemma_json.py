import os, litellm, json
from dotenv import load_dotenv
load_dotenv()
key = os.getenv("LLM_API_KEY")

models_to_test = [
    "gemini/gemini-flash-lite-latest",
    "gemini/gemma-4-26b-a4b-it",
]

msg = 'Return ONLY a JSON object: {"action": "goto", "url": "https://bing.com", "thought": "navigate to search"}'

for m in models_to_test:
    print(f"\n--- Testing {m} ---")
    try:
        res = litellm.completion(
            model=m,
            messages=[{"role": "user", "content": msg}],
            api_key=key,
            response_format={"type": "json_object"},
            timeout=15,
        )
        content = res.choices[0].message.content
        parsed = json.loads(content)
        print(f"  OK with response_format: {parsed}")
    except Exception as e1:
        print(f"  FAIL with response_format: {type(e1).__name__}: {str(e1)[:80]}")
        try:
            res = litellm.completion(
                model=m,
                messages=[{"role": "user", "content": msg}],
                api_key=key,
                timeout=15,
            )
            content = res.choices[0].message.content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            parsed = json.loads(content)
            print(f"  OK without response_format: {parsed}")
        except Exception as e2:
            print(f"  FAIL without response_format: {type(e2).__name__}: {str(e2)[:80]}")
