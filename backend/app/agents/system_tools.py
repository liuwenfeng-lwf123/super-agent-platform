"""

logger = logging.getLogger(__name__)
System & utility tools — screenshot, clipboard, system info, app control, etc.
"""
import os
import logging
import platform
import subprocess
import shlex
import base64
import json
import shutil
import time
import threading
from datetime import datetime
from urllib.parse import urlparse
from langchain_core.tools import tool


_PLAYWRIGHT = None
_PLAYWRIGHT_BROWSER = None
_PLAYWRIGHT_PAGE = None
_PLAYWRIGHT_LOCK = threading.Lock()

import queue as _queue

_PW_THREAD = None
_PW_QUEUE: _queue.Queue | None = None


def _pw_thread_worker(q: _queue.Queue):
    """Dedicated thread that owns the Playwright objects."""
    global _PLAYWRIGHT, _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_PAGE
    while True:
        item = q.get()
        if item is None:
            break
        fn, args, result_q = item
        try:
            result_q.put(("ok", fn(*args)))
        except Exception as exc:
            result_q.put(("err", exc))


def _ensure_pw_thread():
    global _PW_THREAD, _PW_QUEUE
    if _PW_THREAD is not None and _PW_THREAD.is_alive():
        return
    _PW_QUEUE = _queue.Queue()
    _PW_THREAD = threading.Thread(target=_pw_thread_worker, args=(_PW_QUEUE,), daemon=True)
    _PW_THREAD.start()


def _run_in_pw_thread(fn, *args, timeout=60):
    """Run fn(*args) in the dedicated Playwright thread and return the result."""
    _ensure_pw_thread()
    result_q: _queue.Queue = _queue.Queue()
    _PW_QUEUE.put((fn, args, result_q))
    status, value = result_q.get(timeout=timeout)
    if status == "err":
        raise value
    return value


def _normalize_browser_name(browser: str) -> str:
    value = (browser or "Safari").strip()
    lowered = value.lower()
    if lowered in {"chrome", "google chrome"}:
        return "Google Chrome"
    if lowered == "safari":
        return "Safari"
    return value or "Safari"


def _escape_applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _run_browser_javascript(browser: str, script: str, timeout: int = 10) -> str:
    if platform.system() != "Darwin":
        return "Browser tools only supported on macOS currently."
    browser_name = _normalize_browser_name(browser)
    escaped_script = _escape_applescript_string(script)
    if browser_name == "Safari":
        command = f'do JavaScript "{escaped_script}" in current tab of front window'
    elif browser_name == "Google Chrome":
        command = f'execute active tab of front window javascript "{escaped_script}"'
    else:
        return f"Browser error: Unsupported browser '{browser_name}'. Use Safari or Google Chrome."
    applescript = (
        f'tell application "{browser_name}"\n'
        f'activate\n'
        f'if (count of windows) is 0 then return "__BROWSER_ERROR__:No browser window open"\n'
        f'{command}\n'
        f'end tell'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", applescript],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except Exception as e:
        return f"Browser error: {e}"
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        return f"Browser error: {stderr or stdout or 'osascript failed'}"
    if stdout.startswith("__BROWSER_ERROR__:"):
        return f"Browser error: {stdout.split(':', 1)[1]}"
    return stdout or "(No result)"


def _reset_playwright_browser():
    """Force-close stale browser and clear refs so next call rebuilds."""
    global _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_PAGE
    try:
        if _PLAYWRIGHT_BROWSER:
            _PLAYWRIGHT_BROWSER.close()
    except Exception as e:
        logger.debug("Suppressed error in system_tools: %s", e)
    _PLAYWRIGHT_BROWSER = None
    _PLAYWRIGHT_PAGE = None


def _get_playwright_page(*, _retry: bool = True, allow_launch: bool = False):
    """Must be called from the PW worker thread only.
    
    When allow_launch is False (default), only returns an existing connected
    browser page.  A new Chrome window is never spawned, so passive tools
    like screenshot / browser_get_state won't pop up blank windows.
    Set allow_launch=True only when the caller explicitly wants to open a URL.
    """
    global _PLAYWRIGHT, _PLAYWRIGHT_BROWSER, _PLAYWRIGHT_PAGE
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        logger.debug("Suppressed error in system_tools: %s", e)
        return None
    with _PLAYWRIGHT_LOCK:
        try:
            if _PLAYWRIGHT is None:
                if not allow_launch:
                    return None
                _PLAYWRIGHT = sync_playwright().start()
            if _PLAYWRIGHT_BROWSER is None or not _PLAYWRIGHT_BROWSER.is_connected():
                if not allow_launch:
                    return None
                try:
                    _PLAYWRIGHT_BROWSER = _PLAYWRIGHT.chromium.launch(channel="chrome", headless=False)
                except Exception as e:
                    logger.debug("Suppressed error in system_tools: %s", e)
                    _PLAYWRIGHT_BROWSER = _PLAYWRIGHT.chromium.launch(headless=False)
            if _PLAYWRIGHT_PAGE is None or _PLAYWRIGHT_PAGE.is_closed():
                if not allow_launch:
                    return None
                _PLAYWRIGHT_PAGE = _PLAYWRIGHT_BROWSER.new_page(viewport={"width": 1440, "height": 900})
            return _PLAYWRIGHT_PAGE
        except Exception as e:
            logger.debug("Suppressed error in system_tools: %s", e)
            if _retry:
                _reset_playwright_browser()
                return _get_playwright_page(_retry=False, allow_launch=allow_launch)
            return None


# --- Internal impls (run inside PW worker thread) ---

def _pw_open_impl(url: str, *, _retried: bool = False) -> dict:
    page = _get_playwright_page(allow_launch=True)
    if page is None:
        return {"success": False, "error": "Playwright page unavailable"}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception as e:
            logger.debug("Suppressed error in system_tools: %s", e)
        page.wait_for_timeout(1200)
        try:
            page.bring_to_front()
        except Exception as e:
            logger.debug("Suppressed error in system_tools: %s", e)
        return {"success": True, "url": page.url, "title": page.title()}
    except Exception as exc:
        err = str(exc)
        if not _retried and ("closed" in err.lower() or "disposed" in err.lower()):
            _reset_playwright_browser()
            return _pw_open_impl(url, _retried=True)
        return {"success": False, "error": err}


def _pw_state_impl() -> dict:
    page = _get_playwright_page()
    if page is None:
        return {"success": False, "error": "Playwright page unavailable"}
    try:
        return {
            "success": True,
            "url": page.url,
            "title": page.title(),
            "readyState": page.evaluate("document.readyState"),
            "bodyTextLength": page.evaluate("document.body ? document.body.innerText.length : 0"),
        }
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _pw_run_javascript_impl(script: str) -> dict:
    page = _get_playwright_page()
    if page is None:
        return {"success": False, "error": "Playwright page unavailable"}
    try:
        return {"success": True, "result": page.evaluate(script)}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _pw_wait_impl(timeout: int = 25) -> dict:
    page = _get_playwright_page()
    if page is None:
        return {"success": False, "error": "Playwright page unavailable"}
    try:
        page.wait_for_load_state("domcontentloaded", timeout=max(1000, timeout * 1000))
        try:
            page.wait_for_load_state("networkidle", timeout=min(max(1000, timeout * 1000), 8000))
        except Exception as e:
            logger.debug("Suppressed error in system_tools: %s", e)
        page.wait_for_timeout(1200)
        return _pw_state_impl()
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _pw_screenshot_impl(path: str, full_page: bool = False, timeout: int = 8) -> dict:
    page = _get_playwright_page()
    if page is None:
        return {"success": False, "error": "Playwright page unavailable"}
    try:
        _pw_wait_impl(timeout=timeout)
        page.screenshot(path=path, full_page=full_page, timeout=10000)
        return {"success": True, "url": page.url, "title": page.title()}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _pw_open_and_screenshot_impl(url: str, path: str, full_page: bool = False, timeout: int = 30, *, _retried: bool = False) -> dict:
    page = _get_playwright_page(allow_launch=True)
    if page is None:
        return {"success": False, "error": "Playwright page unavailable"}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=max(1000, timeout * 1000))
        try:
            page.wait_for_load_state("networkidle", timeout=min(max(1000, timeout * 1000), 10000))
        except Exception as e:
            logger.debug("Suppressed error in system_tools: %s", e)
        page.wait_for_timeout(1500)
        try:
            page.bring_to_front()
        except Exception as e:
            logger.debug("Suppressed error in system_tools: %s", e)
        page.screenshot(path=path, full_page=full_page, timeout=10000)
        return {"success": True, "url": page.url, "title": page.title()}
    except Exception as exc:
        err = str(exc)
        if not _retried and ("closed" in err.lower() or "disposed" in err.lower()):
            _reset_playwright_browser()
            return _pw_open_and_screenshot_impl(url, path, full_page=full_page, timeout=timeout, _retried=True)
        return {"success": False, "error": err}


# --- Public wrappers (safe to call from any thread) ---

def _playwright_open(url: str) -> dict:
    try:
        return _run_in_pw_thread(_pw_open_impl, url, timeout=45)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _playwright_state() -> dict:
    try:
        return _run_in_pw_thread(_pw_state_impl, timeout=15)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _playwright_run_javascript(script: str) -> dict:
    try:
        return _run_in_pw_thread(_pw_run_javascript_impl, script, timeout=15)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _playwright_wait(timeout: int = 25) -> dict:
    try:
        return _run_in_pw_thread(_pw_wait_impl, timeout, timeout=max(timeout + 10, 35))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _playwright_screenshot(path: str, full_page: bool = False, timeout: int = 8) -> dict:
    try:
        return _run_in_pw_thread(_pw_screenshot_impl, path, full_page, timeout, timeout=max(timeout + 15, 30))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _playwright_open_and_screenshot(url: str, path: str, full_page: bool = False, timeout: int = 30) -> dict:
    try:
        return _run_in_pw_thread(_pw_open_and_screenshot_impl, url, path, full_page, timeout, timeout=max(timeout + 15, 45))
    except Exception as exc:
        return {"success": False, "error": str(exc)}


def _browser_host(value: str) -> str:
    host = urlparse(value or "").netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _browser_url_matches(expected: str, actual: str) -> bool:
    if not expected:
        return True
    if actual.startswith(expected):
        return True
    expected_host = _browser_host(expected)
    actual_host = _browser_host(actual)
    return bool(expected_host and actual_host and expected_host == actual_host)


def _read_browser_state(browser: str) -> dict:
    script = (
        "JSON.stringify((() => {"
        "const body = document.body;"
        "const images = Array.from(document.images || []);"
        "const perf = window.performance;"
        "const now = perf && perf.now ? perf.now() : 0;"
        "const resources = perf && perf.getEntriesByType ? perf.getEntriesByType('resource') : [];"
        "const recentResources = resources.filter((entry) => {"
        "  const end = entry.responseEnd || entry.startTime || 0;"
        "  return now && end && (now - end) < 900;"
        "}).length;"
        "return {"
        "  url: location.href,"
        "  title: document.title,"
        "  readyState: document.readyState,"
        "  bodyTextLength: body ? (body.innerText || '').length : 0,"
        "  bodyHtmlLength: body ? body.innerHTML.length : 0,"
        "  scrollHeight: body ? body.scrollHeight : 0,"
        "  imagesTotal: images.length,"
        "  imagesComplete: images.filter((img) => img.complete).length,"
        "  recentResources"
        "};"
        "})())"
    )
    raw = _run_browser_javascript(browser, script, timeout=5)
    if raw.startswith("Browser error:"):
        return {"ok": False, "error": raw}
    try:
        state = json.loads(raw)
        if isinstance(state, dict):
            state["ok"] = True
            return state
    except Exception as e:
        logger.debug("Suppressed error in system_tools: %s", e)
    return {"ok": False, "error": raw}


def _wait_for_browser_ready(url: str = "", browser: str = "Safari", timeout: int = 25, settle_seconds: float = 1.2) -> dict:
    deadline = time.monotonic() + max(1, int(timeout))
    stable_since = 0.0
    last_signature = None
    last_state: dict = {}
    while time.monotonic() < deadline:
        state = _read_browser_state(browser)
        last_state = state
        if not state.get("ok"):
            time.sleep(0.4)
            continue
        signature = (
            state.get("url"),
            state.get("title"),
            state.get("bodyTextLength"),
            state.get("bodyHtmlLength"),
            state.get("scrollHeight"),
            state.get("imagesComplete"),
            state.get("imagesTotal"),
        )
        now = time.monotonic()
        if signature == last_signature:
            if not stable_since:
                stable_since = now
        else:
            stable_since = 0.0
            last_signature = signature
        ready = state.get("readyState") == "complete"
        url_ok = _browser_url_matches(url, str(state.get("url") or ""))
        images_ok = int(state.get("imagesComplete") or 0) >= int(state.get("imagesTotal") or 0)
        visible = bool(state.get("title")) or int(state.get("bodyHtmlLength") or 0) > 0
        network_idle = int(state.get("recentResources") or 0) == 0
        stable = bool(stable_since and now - stable_since >= settle_seconds)
        long_stable = bool(stable_since and now - stable_since >= max(settle_seconds * 2, 2.0))
        if ready and url_ok and images_ok and visible and stable and (network_idle or long_stable):
            return {"success": True, "state": state}
        time.sleep(0.4)
    return {"success": False, "state": last_state, "error": f"Timed out waiting for browser readiness after {timeout}s"}


@tool
def screenshot(region: str = "full", wait_for_browser: bool = False, browser: str = "Safari", timeout: int = 8, target: str = "browser") -> str:
    """Take a screenshot of the screen.
    Args:
        region: 'full' for full screen, or 'x,y,w,h' for a specific region
    Returns: base64-encoded PNG image path or description
    """
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"/tmp/screenshot_{ts}.png"
        full_page = region == "full_page"
        target_normalized = (target or "browser").lower()
        use_screen_capture = target_normalized in {"screen", "desktop", "computer", "display", "monitor", "system"}
        if not use_screen_capture and region in {"full", "full_page"}:
            pw_result = _playwright_screenshot(path, full_page=full_page, timeout=timeout)
            if pw_result.get("success") and os.path.exists(path):
                size = os.path.getsize(path)
                title = pw_result.get("title") or ""
                url = pw_result.get("url") or ""
                return f"Screenshot saved to {path} ({size} bytes). URL: {url}. Title: {title}"
        if wait_for_browser and platform.system() == "Darwin":
            _wait_for_browser_ready("", browser, timeout=timeout, settle_seconds=0.8)
        if platform.system() == "Darwin":
            if region == "full":
                subprocess.run(["screencapture", "-x", path], check=True, timeout=5)
            else:
                parts = region.split(",")
                if len(parts) == 4:
                    subprocess.run(["screencapture", "-x", "-R", region, path], check=True, timeout=5)
                else:
                    subprocess.run(["screencapture", "-x", path], check=True, timeout=5)
        else:
            return "Screenshot only supported on macOS currently."

        if os.path.exists(path):
            size = os.path.getsize(path)
            return f"Screenshot saved to {path} ({size} bytes). Use read_file or open it to view."
        return "Screenshot failed: file not created."
    except Exception as e:
        return f"Screenshot error: {e}"


@tool
def browser_wait_for_ready(url: str = "", browser: str = "Safari", timeout: int = 25) -> str:
    """Wait until the current browser page is loaded and visually stable."""
    pw_result = _playwright_wait(timeout=timeout)
    if pw_result.get("success"):
        if not url or _browser_url_matches(url, str(pw_result.get("url") or "")):
            return json.dumps(pw_result, ensure_ascii=False)
    result = _wait_for_browser_ready(url, browser, timeout=timeout)
    return json.dumps(result, ensure_ascii=False)


@tool
def browser_open_and_screenshot(url: str, region: str = "full", timeout: int = 30) -> str:
    """Open a URL in the controlled browser, wait for the page to settle, then capture a screenshot."""
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = f"/tmp/screenshot_{ts}.png"
        full_page = region == "full_page"
        result = _playwright_open_and_screenshot(url, path, full_page=full_page, timeout=timeout)
        if result.get("success") and os.path.exists(path):
            size = os.path.getsize(path)
            return f"Screenshot saved to {path} ({size} bytes). URL: {result.get('url') or url}. Title: {result.get('title') or ''}"
        return f"Open and screenshot failed: {result.get('error', 'unknown error')}"
    except Exception as e:
        return f"Open and screenshot error: {e}"


@tool
def clipboard_read() -> str:
    """Read the current contents of the system clipboard."""
    try:
        if platform.system() == "Darwin":
            result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
            text = result.stdout
            if text:
                return f"Clipboard content ({len(text)} chars):\n{text[:2000]}"
            return "Clipboard is empty."
        return "Clipboard only supported on macOS currently."
    except Exception as e:
        return f"Clipboard error: {e}"


@tool
def clipboard_write(text: str) -> str:
    """Write text to the system clipboard.
    Args:
        text: Text to copy to clipboard
    """
    try:
        if platform.system() == "Darwin":
            process = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
            process.communicate(text.encode("utf-8"))
            return f"Copied {len(text)} characters to clipboard."
        return "Clipboard only supported on macOS currently."
    except Exception as e:
        return f"Clipboard error: {e}"


@tool
def system_info() -> str:
    """Get detailed system information (OS, CPU, memory, disk, network)."""
    info = {
        "os": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "python": platform.python_version(),
        "hostname": platform.node(),
    }
    try:
        import psutil
        info["cpu_count"] = psutil.cpu_count()
        info["cpu_percent"] = f"{psutil.cpu_percent(interval=0.5)}%"
        mem = psutil.virtual_memory()
        info["memory_total"] = f"{mem.total / (1024**3):.1f} GB"
        info["memory_used"] = f"{mem.used / (1024**3):.1f} GB ({mem.percent}%)"
        disk = psutil.disk_usage("/")
        info["disk_total"] = f"{disk.total / (1024**3):.0f} GB"
        info["disk_used"] = f"{disk.used / (1024**3):.0f} GB ({disk.percent}%)"
    except ImportError:
        # Fallback without psutil
        if platform.system() == "Darwin":
            try:
                r = subprocess.run(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True, timeout=3)
                info["cpu_count"] = r.stdout.strip()
                r = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=3)
                info["memory_total"] = f"{int(r.stdout.strip()) / (1024**3):.1f} GB"
            except Exception as e:
                logger.debug("Suppressed error in system_tools: %s", e)
    return json.dumps(info, indent=2)


@tool
def open_app(app_name: str) -> str:
    """Open an application on the user's computer.
    Args:
        app_name: Application name (e.g. 'Safari', 'Terminal', 'Finder', 'Visual Studio Code')
    """
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", "-a", app_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opened {app_name}."
        return "open_app only supported on macOS currently."
    except Exception as e:
        return f"Failed to open {app_name}: {e}"


@tool
def open_url(url: str) -> str:
    """Open a URL in the default web browser.
    Args:
        url: URL to open
    """
    try:
        if platform.system() == "Darwin":
            subprocess.Popen(["open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return f"Opened {url} in browser."
        return "open_url only supported on macOS currently."
    except Exception as e:
        return f"Failed to open URL: {e}"


@tool
def browser_open(url: str, browser: str = "Safari") -> str:
    """Open a URL in a named browser application.
    Args:
        url: URL to open
        browser: Browser app name, usually 'Safari' or 'Google Chrome'
    """
    try:
        pw_result = _playwright_open(url)
        if pw_result.get("success"):
            return f"Opened {url} in controlled browser. Page ready: {pw_result.get('title') or pw_result.get('url')}"
        if platform.system() == "Darwin":
            browser_name = _normalize_browser_name(browser)
            subprocess.Popen(["open", "-a", browser_name, url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ready = _wait_for_browser_ready(url, browser_name, timeout=25)
            if ready.get("success"):
                state = ready.get("state") or {}
                return f"Opened {url} in {browser_name}. Page ready: {state.get('title') or state.get('url')}"
            return f"Opened {url} in {browser_name}, but readiness check did not fully settle: {ready.get('error')}"
        return "Browser tools only supported on macOS currently."
    except Exception as e:
        return f"Failed to open URL in browser: {e}"


@tool
def browser_get_state(browser: str = "Safari") -> str:
    """Get the current URL, title, and readyState from the front browser tab.
    Args:
        browser: Browser app name, usually 'Safari' or 'Google Chrome'
    """
    pw_state = _playwright_state()
    if pw_state.get("success"):
        return json.dumps({
            "browser": "controlled",
            "url": pw_state.get("url"),
            "title": pw_state.get("title"),
            "readyState": pw_state.get("readyState"),
            "bodyTextLength": pw_state.get("bodyTextLength"),
        }, ensure_ascii=False, indent=2)
    result = _run_browser_javascript(
        browser,
        "JSON.stringify({url: window.location.href, title: document.title, readyState: document.readyState})",
    )
    if result.startswith("Browser error:"):
        return result
    try:
        payload = json.loads(result)
        if isinstance(payload, dict):
            payload["browser"] = _normalize_browser_name(browser)
            return json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.debug("Suppressed error in system_tools: %s", e)
    return json.dumps({
        "browser": _normalize_browser_name(browser),
        "raw_result": result,
    }, ensure_ascii=False, indent=2)


@tool
def browser_run_javascript(script: str, browser: str = "Safari") -> str:
    """Run JavaScript in the front browser tab and return the raw result.
    Args:
        script: JavaScript expression or IIFE to execute
        browser: Browser app name, usually 'Safari' or Google Chrome'
    """
    pw_result = _playwright_run_javascript(script)
    if pw_result.get("success"):
        result = pw_result.get("result")
        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
    return _run_browser_javascript(browser, script)


@tool
def browser_click(selector: str, browser: str = "Safari") -> str:
    """Click the first element matching a CSS selector in the front browser tab.
    Args:
        selector: CSS selector for the target element
        browser: Browser app name, usually 'Safari' or 'Google Chrome'
    """
    page = _get_playwright_page()
    if page is not None:
        try:
            page.locator(selector).first.click(timeout=10000)
            _playwright_wait(timeout=8)
            return json.dumps({"ok": True, "selector": selector}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc), "selector": selector}, ensure_ascii=False)
    selector_json = json.dumps(selector)
    script = (
        "(() => {"
        f"const selector = {selector_json};"
        "const el = document.querySelector(selector);"
        "if (!el) return JSON.stringify({ok:false,error:'Element not found',selector});"
        "el.scrollIntoView({block:'center', inline:'center'});"
        "el.click();"
        "const text = (el.innerText || el.textContent || '').trim().slice(0, 200);"
        "return JSON.stringify({ok:true,selector,text});"
        "})()"
    )
    return _run_browser_javascript(browser, script)


@tool
def browser_fill(selector: str, text: str, browser: str = "Safari", submit: bool = False) -> str:
    """Fill a form field matched by CSS selector and optionally submit its form.
    Args:
        selector: CSS selector for the input or editable element
        text: Text to insert into the element
        browser: Browser app name, usually 'Safari' or 'Google Chrome'
        submit: Whether to submit the nearest form after filling
    """
    page = _get_playwright_page()
    if page is not None:
        try:
            locator = page.locator(selector).first
            locator.fill(text, timeout=10000)
            if submit:
                locator.press("Enter", timeout=10000)
                _playwright_wait(timeout=10)
            return json.dumps({"ok": True, "selector": selector, "value_length": len(text), "submitted": submit}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc), "selector": selector}, ensure_ascii=False)
    selector_json = json.dumps(selector)
    text_json = json.dumps(text)
    submit_json = "true" if submit else "false"
    script = (
        "(() => {"
        f"const selector = {selector_json};"
        f"const value = {text_json};"
        f"const shouldSubmit = {submit_json};"
        "const el = document.querySelector(selector);"
        "if (!el) return JSON.stringify({ok:false,error:'Element not found',selector});"
        "el.focus();"
        "if ('value' in el) { el.value = value; }"
        "else if (el.isContentEditable) { el.textContent = value; }"
        "else { el.textContent = value; }"
        "el.dispatchEvent(new Event('input', {bubbles:true}));"
        "el.dispatchEvent(new Event('change', {bubbles:true}));"
        "if (shouldSubmit) {"
        "  const form = el.form || el.closest('form');"
        "  if (form) {"
        "    if (typeof form.requestSubmit === 'function') form.requestSubmit();"
        "    else form.submit();"
        "  }"
        "}"
        "return JSON.stringify({ok:true,selector,value_length:value.length,submitted:shouldSubmit});"
        "})()"
    )
    return _run_browser_javascript(browser, script)


@tool
def browser_extract_text(selector: str = "body", max_chars: int = 4000, browser: str = "Safari") -> str:
    """Extract visible text from the first element matching a CSS selector.
    Args:
        selector: CSS selector to read from, default 'body'
        max_chars: Maximum characters to return
        browser: Browser app name, usually 'Safari' or 'Google Chrome'
    """
    page = _get_playwright_page()
    if page is not None:
        try:
            text = page.locator(selector).first.inner_text(timeout=10000)
            return json.dumps({"ok": True, "selector": selector, "truncated": len(text) > max_chars, "text": text[:max_chars]}, ensure_ascii=False)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc), "selector": selector}, ensure_ascii=False)
    selector_json = json.dumps(selector)
    max_chars_value = max(1, int(max_chars))
    script = (
        "(() => {"
        f"const selector = {selector_json};"
        f"const maxChars = {max_chars_value};"
        "const el = document.querySelector(selector);"
        "if (!el) return JSON.stringify({ok:false,error:'Element not found',selector});"
        "const text = (el.innerText || el.textContent || '').trim();"
        "return JSON.stringify({ok:true,selector,truncated:text.length > maxChars,text:text.slice(0, maxChars)});"
        "})()"
    )
    return _run_browser_javascript(browser, script)


@tool
def notify(title: str, message: str) -> str:
    """Send a desktop notification to the user.
    Args:
        title: Notification title
        message: Notification body text
    """
    try:
        if platform.system() == "Darwin":
            script = f'display notification "{message}" with title "{title}"'
            subprocess.run(["osascript", "-e", script], timeout=5)
            return f"Notification sent: {title}"
        return "Notifications only supported on macOS currently."
    except Exception as e:
        return f"Notification error: {e}"


@tool
def git_command(command: str, cwd: str = ".") -> str:
    """Run a git command in a specified directory.
    Args:
        command: Git command without 'git' prefix (e.g. 'status', 'log -5', 'diff --stat')
        cwd: Working directory path
    """
    try:
        result = subprocess.run(
            ["git"] + shlex.split(command),
            shell=False,
            cwd=os.path.expanduser(cwd),
            capture_output=True,
            text=True,
            timeout=30,
        )
        output = result.stdout
        if result.returncode != 0:
            output += f"\n[stderr: {result.stderr[:500]}]"
        return output[:5000] if output else "(No output)"
    except Exception as e:
        return f"Git error: {e}"


@tool
def http_request(url: str, method: str = "GET", headers: str = "", body: str = "") -> str:
    """Make an HTTP request and return the response.
    Args:
        url: Request URL
        method: HTTP method (GET, POST, PUT, DELETE)
        headers: JSON string of headers, e.g. '{"Authorization": "Bearer xxx"}'
        body: Request body (for POST/PUT)
    """
    import httpx
    try:
        h = json.loads(headers) if headers else {}
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.request(method, url, headers=h, content=body if body else None)
            return f"HTTP {resp.status_code}\nHeaders: {dict(resp.headers)}\n\nBody ({len(resp.text)} chars):\n{resp.text[:3000]}"
    except Exception as e:
        return f"HTTP request error: {e}"


@tool
def pdf_extract(file_path: str) -> str:
    """Extract text from a PDF file.
    Args:
        file_path: Path to the PDF file
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(os.path.expanduser(file_path))
        text = ""
        for page in doc:
            text += page.get_text() + "\n"
        doc.close()
        return text[:10000] if text else "No text extracted from PDF."
    except ImportError:
        # Fallback: try pdftotext
        try:
            result = subprocess.run(
                ["pdftotext", os.path.expanduser(file_path), "-"],
                capture_output=True, text=True, timeout=30,
            )
            return result.stdout[:10000] if result.stdout else "No text extracted."
        except Exception as e:
            logger.debug("Suppressed error in system_tools: %s", e)
            return "PDF extraction requires PyMuPDF (pip install pymupdf) or pdftotext."
    except Exception as e:
        return f"PDF extraction error: {e}"


@tool
def summarize_url(url: str) -> str:
    """Fetch a URL and return a cleaned text summary (articles, docs, etc).
    Args:
        url: URL to fetch and summarize
    """
    import httpx
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as client:
            resp = client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()

        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        # Remove scripts, styles, nav, footer
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        title = soup.title.string if soup.title else ""
        # Try to get article body
        article = soup.find("article") or soup.find("main") or soup.body
        text = article.get_text(separator="\n", strip=True) if article else ""
        import re
        text = re.sub(r"\n{3,}", "\n\n", text)
        result = f"Title: {title}\nURL: {url}\n\n{text[:8000]}"
        return result
    except Exception as e:
        return f"Failed to fetch URL: {e}"


@tool
async def remember(action: str, content: str = "", category: str = "knowledge") -> str:
    """Manage persistent memories. Actions: 'add' (save key=value), 'search' (find relevant), 'list' (show all), 'project' (read/write project MEMORY.md).

    Examples:
      remember(action='add', content='python_version=3.12', category='preference')
      remember(action='search', content='python')
      remember(action='list')
      remember(action='project', content='## Build\\nnpm run build')
    """
    from app.memory.store import memory_store
    from app.memory.layered_store import LayeredMemoryStore

    if action == "add":
        if "=" in content:
            key, value = content.split("=", 1)
        else:
            key, value = content[:60], content
        entry = await memory_store.add(key.strip(), value.strip(), category)
        return f"Memory saved: [{entry.category}] {entry.key} = {entry.value}"

    elif action == "search":
        results = await memory_store.search(content)
        if not results:
            return f"No memories found for '{content}'."
        lines = [f"Found {len(results)} memories:"]
        for e in results[:10]:
            lines.append(f"  [{e.category}] {e.key}: {e.value}")
        return "\n".join(lines)

    elif action == "list":
        entries = await memory_store.get_all()
        if not entries:
            return "No memories stored yet."
        lines = [f"All memories ({len(entries)} total):"]
        for e in entries[:30]:
            lines.append(f"  [{e.category}] {e.key}: {e.value[:80]}")
        if len(entries) > 30:
            lines.append(f"  ... and {len(entries) - 30} more")
        return "\n".join(lines)

    elif action == "project":
        layered = LayeredMemoryStore()
        if content.strip():
            result = layered.update_project_memory(content.strip())
            return f"Project memory updated: {result['lines']} lines, {result['bytes']} bytes"
        else:
            mem = layered.get_project_memory()
            return mem if mem else "(Project memory is empty. Write to it with content=...)"

    return f"Unknown action '{action}'. Use: add, search, list, project"


@tool
def file_history(thread_id: str = "", path: str = "", limit: int = 20) -> str:
    """View file change history for the current workspace. Shows diffs and timestamps.

    Args:
        thread_id: Thread ID (leave empty to use current)
        path: Optional file path to filter history
        limit: Max entries to return
    """
    from app.runtime_backends import runtime_manager

    if not thread_id:
        import contextvars
        _ctx = contextvars.copy_context()
        thread_id = "_default"

    entries = runtime_manager.get_file_history(thread_id, path=path or None, limit=limit)
    if not entries:
        return "No file changes recorded yet."

    lines = [f"File history ({len(entries)} changes):"]
    for e in entries[-limit:]:
        ts = e.get("timestamp", "?")[:19]
        action = e.get("action", "?")
        fpath = e.get("path", "?")
        old_sz = e.get("old_size", 0)
        new_sz = e.get("new_size", 0)
        lines.append(f"\n[{ts}] {action.upper()} {fpath} ({old_sz} -> {new_sz} bytes)")
        diff = e.get("diff", "")
        if diff:
            # Show first 10 diff lines
            diff_lines = diff.split("\n")[:10]
            lines.extend(f"  {dl}" for dl in diff_lines)
            if len(diff.split("\n")) > 10:
                lines.append("  ...")

    return "\n".join(lines)


@tool
def knowledge_search(query: str, top_k: int = 5) -> str:
    """Search the knowledge base (RAG) for relevant documents.

    Args:
        query: Search query
        top_k: Max results to return (default 5)
    """
    from app.rag.store import knowledge_base

    results = knowledge_base.search(query, top_k=top_k)
    if not results:
        return f"No knowledge base results for '{query}'. Upload documents via /api/knowledge/upload first."

    lines = [f"Found {len(results)} relevant chunks:"]
    for r in results:
        lines.append(f"\n--- [{r['doc_name']}] (score: {r['score']}) ---")
        lines.append(r["text"][:500])
    return "\n".join(lines)


@tool
def session_search(query: str, limit: int = 10) -> str:
    """Search past conversations across all sessions for relevant context.
    Useful when you need to recall something discussed in a previous conversation.

    Args:
        query: Search query (natural language)
        limit: Max results to return (default 10)
    """
    from app.agents.learning_loop import session_search_db

    results = session_search_db.search(query, limit=limit)
    if not results:
        return f"No past conversations found matching '{query}'."

    lines = [f"Found {len(results)} relevant past messages:"]
    for r in results:
        role_label = "User" if r["role"] == "user" else "Agent"
        lines.append(f"\n[{r['timestamp'][:16]}] ({role_label}, thread: {r['thread_id'][:8]}...)")
        lines.append(r["content"][:300])
    return "\n".join(lines)


SYSTEM_TOOLS = [
    screenshot, clipboard_read, clipboard_write, system_info,
    open_app, open_url, browser_open, browser_open_and_screenshot, browser_wait_for_ready, browser_get_state, browser_run_javascript,
    browser_click, browser_fill, browser_extract_text, notify, git_command, http_request,
    pdf_extract, summarize_url, remember, file_history, knowledge_search,
    session_search,
]
