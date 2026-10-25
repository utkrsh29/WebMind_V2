import pytest
from app.browser.dom_snapshot import capture_dom_snapshot
from app.browser.session import BrowserSession


@pytest.mark.asyncio
async def test_smoke_search_laptop():
    """End-to-end smoke test: Searches for 'laptop' and returns top 3 result titles."""
    session = BrowserSession(headless=True)
    await session.start()

    try:
        page = session.page
        
        # 1. Attempt DuckDuckGo search
        await page.goto("https://duckduckgo.com/?q=laptop", wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(1500)

        # Check if DuckDuckGo blocked with 418 / anti-bot wall
        is_blocked = "home-error" in page.url or "418" in page.url
        if is_blocked:
            # Fall back to alternative search engine (Bing) as documented in generic web agent rules
            await page.goto("https://www.bing.com/search?q=laptop", wait_until="domcontentloaded", timeout=20000)
            await page.wait_for_timeout(2000)

        # Observe interactive DOM snapshot with Set-of-Marks
        snapshot = await capture_dom_snapshot(page, include_som_screenshot=True)
        assert len(snapshot.elements) > 0, "Expected interactive elements on search results page"

        # Extract top 3 search result titles
        titles = await page.evaluate("""
            (() => {
                const results = Array.from(document.querySelectorAll('h2, article h2, [data-testid="result-title-a"], h2 a'))
                    .map(el => (el.innerText || '').trim())
                    .filter(t => t.length > 5 && !t.toLowerCase().includes('search') && !t.toLowerCase().includes('downloads'));
                return results.slice(0, 3);
            })()
        """)

        # Fallback to Set-of-Marks link texts if specific selectors vary
        if not titles:
            titles = [el["name"] for el in snapshot.elements if len(el.get("name", "")) > 15][:3]

        assert len(titles) >= 3, f"Expected at least 3 result titles, got: {titles}"
        print(f"\n[Smoke Test Success] Top 3 result titles for 'laptop':\n" + "\n".join(f"  {i+1}. {t}" for i, t in enumerate(titles[:3])))

    finally:
        await session.close()
