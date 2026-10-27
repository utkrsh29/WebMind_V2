import asyncio
import fnmatch
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from typing import List, Optional
from urllib.parse import urlparse
from playwright.async_api import Browser, BrowserContext, Page, async_playwright
from app.config import settings

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logger = logging.getLogger(__name__)

# Common cookie accept selectors and texts
COOKIE_ACCEPT_SELECTORS = [
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('Accept cookies')",
    "button:has-text('Accept Cookies')",
    "button:has-text('I agree')",
    "button:has-text('Agree')",
    "button:has-text('Allow all')",
    "button:has-text('Got it')",
    "#onetrust-accept-btn-handler",
    ".cookie-banner button.accept",
]


class BrowserSession:
    def __init__(self, headless: Optional[bool] = None, survive_backend_shutdown: bool = False):
        self.headless = headless if headless is not None else settings.HEADLESS
        self.survive_backend_shutdown = survive_backend_shutdown
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.pages: List[Page] = []
        self.active_tab_index: int = 0
        self._detached_process: Optional[subprocess.Popen] = None
        self._profile_dir: Optional[str] = None
        self._connected_over_cdp = False

    async def start(self) -> None:
        """Launches Playwright Chromium browser and initializes first page."""
        self.playwright = await async_playwright().start()
        
        args = [
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--disable-blink-features=AutomationControlled",
        ]

        if self.survive_backend_shutdown:
            await self._launch_detached_browser(args)
        else:
            self.browser = await self.playwright.chromium.launch(
                headless=self.headless,
                args=args,
            )

            self.context = await self.browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="Asia/Kolkata",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                ignore_https_errors=True,
            )

        first_page = await self.context.new_page()
        first_page.set_default_timeout(10000)
        first_page.set_default_navigation_timeout(20000)
        self.pages.append(first_page)
        self.active_tab_index = 0

    async def _launch_detached_browser(self, base_args: List[str]) -> None:
        """Starts Chromium outside the backend process and attaches via CDP."""
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            debug_port = port_socket.getsockname()[1]

        self._profile_dir = tempfile.mkdtemp(prefix="webmind-browser-")
        browser_args = [
            self.playwright.chromium.executable_path,
            *base_args,
            f"--remote-debugging-port={debug_port}",
            "--remote-debugging-address=127.0.0.1",
            "--remote-allow-origins=*",
            f"--user-data-dir={self._profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,900",
        ]
        if self.headless:
            browser_args.append("--headless=new")

        popen_options = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "close_fds": True,
        }
        if os.name == "nt":
            popen_options["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            popen_options["start_new_session"] = True

        self._detached_process = subprocess.Popen(browser_args, **popen_options)
        endpoint = f"http://127.0.0.1:{debug_port}"
        deadline = asyncio.get_running_loop().time() + 30.0
        last_error: Optional[Exception] = None

        while asyncio.get_running_loop().time() < deadline:
            if self._detached_process.poll() is not None:
                break
            try:
                self.browser = await self.playwright.chromium.connect_over_cdp(
                    endpoint,
                    timeout=2000,
                )
                self._connected_over_cdp = True
                contexts = self.browser.contexts
                if not contexts:
                    raise RuntimeError("Detached Chromium did not expose its default browser context.")
                self.context = contexts[0]
                return
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(0.25)

        await self.close()
        raise RuntimeError(f"Could not attach to detached Chromium: {last_error}")

    @property
    def page(self) -> Page:
        """Returns the currently active page."""
        if not self.pages or self.active_tab_index >= len(self.pages):
            raise RuntimeError("No active page available in browser session.")
        return self.pages[self.active_tab_index]

    def is_url_allowed(self, target_url: str) -> bool:
        """Enforces domain allow/deny list safety policies."""
        if not target_url or target_url.startswith("about:"):
            return True

        parsed = urlparse(target_url)
        hostname = (parsed.hostname or "").lower()

        # Check deny list
        for pattern in settings.DOMAIN_DENY_LIST:
            if fnmatch.fnmatch(hostname, pattern.lower()):
                logger.warning(f"Blocked navigation to denied host: {hostname} (pattern {pattern})")
                return False

        # If allow list is configured, host must match at least one
        if settings.DOMAIN_ALLOW_LIST:
            allowed = any(fnmatch.fnmatch(hostname, pattern.lower()) for pattern in settings.DOMAIN_ALLOW_LIST)
            if not allowed:
                logger.warning(f"Blocked navigation to host not in allow list: {hostname}")
                return False

        return True

    async def dismiss_cookie_banners(self) -> None:
        """Attempts to dismiss common cookie popups without throwing errors."""
        try:
            for selector in COOKIE_ACCEPT_SELECTORS:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=500):
                    await btn.click(timeout=1000)
                    logger.info(f"Dismissed cookie banner via selector: {selector}")
                    await asyncio.sleep(0.5)
                    break
        except Exception:
            pass

    async def new_tab(self, url: Optional[str] = None) -> int:
        """Opens a new tab and focuses it."""
        if not self.context:
            raise RuntimeError("Browser session not started.")
        new_page = await self.context.new_page()
        new_page.set_default_timeout(10000)
        new_page.set_default_navigation_timeout(20000)
        self.pages.append(new_page)
        self.active_tab_index = len(self.pages) - 1

        if url:
            if not self.is_url_allowed(url):
                raise PermissionError(f"Navigation to '{url}' is restricted by domain security policy.")
            await new_page.goto(url, wait_until="domcontentloaded")
            await self.dismiss_cookie_banners()

        return self.active_tab_index

    async def switch_tab(self, index: int) -> None:
        """Switches active page index."""
        if index < 0 or index >= len(self.pages):
            raise IndexError(f"Tab index {index} out of range (0-{len(self.pages)-1})")
        self.active_tab_index = index
        await self.pages[self.active_tab_index].bring_to_front()

    async def close(self) -> None:
        """Closes this session and its browser process when explicitly requested."""
        try:
            if self.context and not self._connected_over_cdp:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.warning(f"Error while closing browser session: {e}")
        finally:
            if self._detached_process and self._detached_process.poll() is None:
                self._detached_process.terminate()
                try:
                    self._detached_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._detached_process.kill()
            if self._profile_dir:
                shutil.rmtree(self._profile_dir, ignore_errors=True)
            self.pages.clear()
            self.context = None
            self.browser = None
            self.playwright = None
            self._detached_process = None
            self._profile_dir = None

    async def detach(self) -> None:
        """Disconnects Playwright while leaving detached Chromium running."""
        try:
            # Closing the CDP transport disconnects automation without issuing
            # a browser-close command to the independently launched process.
            if self.playwright:
                await self.playwright.stop()
        except Exception as e:
            logger.warning(f"Error while detaching Playwright from retained browser: {e}")
        finally:
            self.pages.clear()
            self.context = None
            self.browser = None
            self.playwright = None
            self._connected_over_cdp = False
