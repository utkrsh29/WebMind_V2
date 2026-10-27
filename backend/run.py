import asyncio
import sys

# On Windows, Uvicorn with --reload forces WindowsSelectorEventLoopPolicy which breaks Playwright subprocesses.
# We ensure WindowsProactorEventLoopPolicy is always used.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    try:
        import uvicorn.loops.asyncio
        uvicorn.loops.asyncio.asyncio_setup = lambda use_subprocess=False: asyncio.set_event_loop_policy(
            asyncio.WindowsProactorEventLoopPolicy()
        )
    except Exception:
        pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
