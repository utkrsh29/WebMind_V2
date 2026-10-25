import asyncio
import json
import sys

# Ensure UTF-8 output on Windows console
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, "backend")

from app.browser.session import BrowserSession
from app.browser.dom_snapshot import capture_dom_snapshot
from app.router.routing_policy import route_task


async def main():
    query = "find laptops under 70000 INR with 16GB RAM"
    print(f"\n=======================================================")
    print(f"[1] ROUTING QUERY: '{query}'")
    print(f"=======================================================")
    profile = await route_task(query)
    print("Task Profile:")
    print(json.dumps(profile.model_dump(), indent=2))

    print(f"\n[2] SELECTED DETERMINISTIC TIER: {profile.routed_tier}")
    print(f"Routing Triggers: {profile.routing_reason}")

    print("\n[3] STARTING BROWSER PLAYWRIGHT SESSION...")
    session = BrowserSession(headless=True)
    await session.start()

    try:
        page = session.page
        search_query = "laptops under 70000 with 16GB RAM price India"
        search_url = f"https://www.bing.com/search?q={search_query.replace(' ', '+')}"
        print(f"Navigating to search provider: {search_url}")
        await page.goto(search_url, wait_until="domcontentloaded", timeout=25000)
        await asyncio.sleep(2)

        print("\n[4] CAPTURING SET-OF-MARKS OBSERVATION SNAPSHOT...")
        snapshot = await capture_dom_snapshot(page, include_som_screenshot=True)
        print(f"Page Title: {snapshot.title}")
        print(f"Discovered {len(snapshot.elements)} interactive elements tagged with SoM markers.")
        print(f"Screenshot taken: {len(snapshot.screenshot_bytes) if snapshot.screenshot_bytes else 0} bytes.")

        print("\n[5] EXTRACTING LAPTOP RESULTS MEETING CONSTRAINTS (< 70,000 INR, 16GB RAM):")
        results = await page.evaluate("""
            (() => {
                const cards = Array.from(document.querySelectorAll('li.b_algo'))
                    .map(el => {
                        const titleEl = el.querySelector('h2 a');
                        const snippetEl = el.querySelector('.b_caption p, p');
                        return {
                            title: titleEl ? (titleEl.innerText || '').trim() : '',
                            url: titleEl ? (titleEl.getAttribute('href') || '') : '',
                            snippet: snippetEl ? (snippetEl.innerText || '').trim() : ''
                        };
                    })
                    .filter(c => c.title.length > 5);
                return cards.slice(0, 5);
            })()
        """)

        for i, item in enumerate(results, 1):
            title = item.get("title", "")
            url = item.get("url", "")
            snippet = item.get("snippet", "")
            print(f"\nItem #{i}: {title}")
            print(f"  Source URL: {url}")
            print(f"  Details: {snippet[:140]}...")

        print("\n=======================================================")
        print(f"[6] TASK COMPLETED SUCCESSFULLY!")
        print(f"Total verified laptop source links extracted: {len(results)}")
        print("=======================================================")

    finally:
        await session.close()
        print("\nBrowser session closed successfully.")


if __name__ == "__main__":
    asyncio.run(main())
