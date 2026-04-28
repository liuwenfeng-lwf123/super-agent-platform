import asyncio
import logging
import ipaddress
import httpx
import re
import socket
from typing import Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup



logger = logging.getLogger(__name__)

# Module-level shared httpx client for connection pooling
_shared_client: httpx.AsyncClient | None = None


def _get_shared_client() -> httpx.AsyncClient:
    global _shared_client
    if _shared_client is None or _shared_client.is_closed:
        _shared_client = httpx.AsyncClient(
            timeout=15,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
        )
    return _shared_client


class WebSearchTool:
    def __init__(self, max_results: int = 5):
        self.max_results = max_results

    async def search(self, query: str, max_results: Optional[int] = None) -> list[dict]:
        limit = max_results or self.max_results
        results = []

        try:
            results = await self._search_duckduckgo(query, limit)
            if results and len(results) > 0 and "error" not in results[0]:
                return results
        except Exception as e:
            logger.debug("Suppressed error in search: %s", e)

        results = []
        try:
            results = await self._search_html(query, limit)
            if results:
                return results
        except Exception as e:
            logger.debug("Suppressed error in search: %s", e)

        return [{"title": "Search unavailable", "body": f"Web search is currently unavailable in this environment. You can still use web_fetch to read specific URLs, or use your own knowledge to answer questions about: {query}", "href": ""}]

    async def _search_duckduckgo(self, query: str, limit: int) -> list[dict]:
        def _sync_search():
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=limit):
                    results.append({
                        "title": r.get("title", ""),
                        "body": r.get("body", ""),
                        "href": r.get("href", ""),
                    })
            return results
        return await asyncio.get_event_loop().run_in_executor(None, _sync_search)

    async def _search_html(self, query: str, limit: int) -> list[dict]:
        client = _get_shared_client()
        resp = await client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
        )
        resp.raise_for_status()
        return self._parse_ddg_html(resp.text, limit)

    def _parse_ddg_html(self, html: str, limit: int) -> list[dict]:
        results = []
        try:
            soup = BeautifulSoup(html, "html.parser")
            for item in soup.select(".result"):
                title_el = item.select_one(".result__title a")
                snippet_el = item.select_one(".result__snippet")
                if not title_el:
                    continue
                title = title_el.get_text(strip=True)
                href = title_el.get("href", "")
                if href.startswith("//"):
                    href = "https:" + href
                snippet = snippet_el.get_text(strip=True) if snippet_el else ""
                results.append({"title": title, "body": snippet, "href": href})
                if len(results) >= limit:
                    break
        except Exception as e:
            logger.debug("Suppressed error in search: %s", e)
        return results

    async def search_and_summarize(self, query: str) -> str:
        results = await self.search(query)
        if not results:
            return "No search results found."

        lines = [f"Search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "")
            body = r.get("body", "")
            href = r.get("href", "")
            lines.append(f"{i}. {title}")
            if body:
                lines.append(f"   {body}")
            if href:
                lines.append(f"   Source: {href}")
            lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _is_safe_url(url: str) -> str | None:
        """Return error message if URL is unsafe (SSRF), else None."""
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return f"Blocked: only http/https allowed, got '{parsed.scheme}'"
        hostname = parsed.hostname or ""
        if not hostname:
            return "Blocked: no hostname"
        # Block localhost variants
        if hostname in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
            return "Blocked: localhost access not allowed"
        # Resolve hostname and check for private IPs
        try:
            for info in socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM):
                addr = info[4][0]
                ip = ipaddress.ip_address(addr)
                if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                    return f"Blocked: resolved to private/reserved IP {ip}"
        except socket.gaierror:
            pass  # DNS failure will be caught by httpx
        return None

    async def fetch_url(self, url: str, max_length: int = 15000) -> str:
        ssrf_err = self._is_safe_url(url)
        if ssrf_err:
            return ssrf_err
        try:
            client = _get_shared_client()
            resp = await client.get(url)
            resp.raise_for_status()
            content_type = resp.headers.get("content-type", "")
            if "text/html" in content_type:
                text = self._extract_text_from_html(resp.text)
            else:
                text = resp.text
            return text[:max_length]
        except Exception as e:
            return f"Failed to fetch {url}: {str(e)}"

    def _extract_text_from_html(self, html: str) -> str:
        html = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<nav[^>]*>[\s\S]*?</nav>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<footer[^>]*>[\s\S]*?</footer>', '', html, flags=re.IGNORECASE)
        html = re.sub(r'<[^>]+>', ' ', html)
        html = re.sub(r'&nbsp;', ' ', html)
        html = re.sub(r'&amp;', '&', html)
        html = re.sub(r'&lt;', '<', html)
        html = re.sub(r'&gt;', '>', html)
        html = re.sub(r'&#\d+;', '', html)
        html = re.sub(r'\s+', ' ', html)
        return html.strip()


web_search_tool = WebSearchTool()
