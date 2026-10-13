"""PDF download with retry, validation, and logging."""

import sys
import time
import base64
import urllib.parse
import urllib.request
import urllib.error
import ssl
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from utils import setup_logger, sanitize_filename, validate_pdf, retry, save_json

logger = setup_logger("pdf_downloader")


class PDFDownloader:
    """Downloads PDFs from IEEE Xplore using browser session cookies."""

    def __init__(self, output_dir: Path, cookies: list[dict] | None = None,
                 library_dir: Path | None = None, browser_context=None):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.library_dir = library_dir or output_dir.parent
        self.cookies = cookies or []
        self.browser_context = browser_context  # Playwright BrowserContext (optional browser-assisted fallback)
        self.log: list[dict] = []
        self.ssl_context = ssl.create_default_context()

    def _cookie_header(self, url: str = "") -> str:
        """Build Cookie header from browser cookies that apply to the target URL."""
        if not self.cookies:
            return ""
        host = urllib.parse.urlparse(url).hostname or ""
        parts = []
        for cookie in self.cookies:
            name = cookie.get("name", "")
            value = cookie.get("value", "")
            domain = cookie.get("domain", "").lstrip(".")
            if not name:
                continue
            if domain and host and not (host == domain or host.endswith(f".{domain}")):
                continue
            parts.append(f"{name}={value}")
        return "; ".join(parts)

    def _candidate_urls(self, paper: dict, pdf_url: str) -> list[str]:
        """Return possible PDF endpoints for a paper (source-agnostic)."""
        urls: list[str] = []
        for u in (paper.get("pdf_urls") or []):
            if u:
                urls.append(u)
        if pdf_url:
            urls.append(pdf_url)
        arnumber = str(paper.get("arnumber", "")).strip()
        if arnumber:
            urls.extend([
                f"https://ieeexplore.ieee.org/stamp/stamp.jsp?tp=&arnumber={arnumber}",
                f"https://ieeexplore.ieee.org/stampPDF/getPDF.jsp?tp=&arnumber={arnumber}",
            ])
        return list(dict.fromkeys(urls))

    def _pdf_filename(self, index: int, paper: dict) -> str:
        """Generate standardized PDF filename."""
        title = sanitize_filename(paper.get("title", "untitled"), 50)
        ident = str(paper.get("arnumber") or paper.get("pii") or paper.get("uid")
                    or paper.get("doi") or "unknown")
        ident = sanitize_filename(ident, 40)
        return f"{index:03d}_{title}_{ident}.pdf"

    def _existing_pdf(self, paper: dict) -> Path | None:
        title = sanitize_filename(paper.get("title", ""), 50).lower()
        arnumber = str(paper.get("arnumber", "")).strip().lower()
        doi = sanitize_filename(str(paper.get("doi", "")), 80).lower()
        pii = str(paper.get("pii", "")).strip().lower()
        if not self.library_dir.exists():
            return None
        for path in self.library_dir.rglob("*.pdf"):
            name = path.stem.lower()
            if arnumber and arnumber in name:
                return path
            if doi and doi in name:
                return path
            if pii and pii in name:
                return path
            if title and title in name:
                return path
        return None

    @retry(max_attempts=3, base_delay=3.0, backoff=2.0, exceptions=(urllib.error.URLError, OSError))
    def _download_file(self, url: str, dest: Path) -> bool:
        """Download a single file with cookie auth."""
        req = urllib.request.Request(url)
        req.add_header(
            "User-Agent",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
        )
        req.add_header("Accept", "application/pdf,*/*")
        host = urllib.parse.urlparse(url).netloc
        if host:
            req.add_header("Referer", f"https://{host}/")
        cookie = self._cookie_header(url)
        if cookie:
            req.add_header("Cookie", cookie)

        with urllib.request.urlopen(req, timeout=60, context=self.ssl_context) as resp:
            content_type = resp.headers.get("Content-Type", "")
            content = resp.read()
            if "text/html" in content_type or content.startswith(b"<!DOCTYPE html"):
                return False
            dest.write_bytes(content)
        return True

    def _browser_fetch(self, url: str, dest: Path) -> bool:
        """Browser-assisted download fallback.

        Handles bot-protected PDF endpoints (e.g. ScienceDirect's JS challenge
        'crasolve' page): navigates the real browser to the PDF URL, lets the
        challenge script complete and redirect to the signed asset URL, then
        fetches the bytes from within that page (same-origin fetch keeps the
        challenge clearance).
        """
        if not self.browser_context:
            return False
        page = None
        try:
            page = self.browser_context.new_page()
            page.set_default_timeout(90000)
            try:
                page.bring_to_front()
            except Exception:
                pass
            page.goto(url, wait_until="domcontentloaded", timeout=90000)

            # JS challenge may take a while before redirecting to the asset host
            try:
                page.wait_for_url(
                    lambda u: ("pdf.sciencedirectassets.com" in u) or ("X-Amz" in u),
                    timeout=180000,
                )
            except Exception:
                pass

            result = page.evaluate(r"""
            async () => {
                try {
                    const r = await fetch(location.href, {credentials: 'include'});
                    const buf = await r.arrayBuffer();
                    const bytes = new Uint8Array(buf);
                    let bin = '';
                    const chunk = 8192;
                    for (let i = 0; i < bytes.length; i += chunk) {
                        bin += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
                    }
                    return {status: r.status, len: bytes.length,
                            head: String.fromCharCode.apply(null, bytes.subarray(0, 8)),
                            b64: btoa(bin)};
                } catch (e) { return {error: String(e)}; }
            }
            """)
            if result and str(result.get("head", "")).startswith("%PDF"):
                dest.write_bytes(base64.b64decode(result["b64"]))
                return True

            # Fallback: fetch the signed final URL via the browser context
            final = page.url
            if "pdf.sciencedirectassets.com" in final or "X-Amz" in final:
                resp = self.browser_context.request.get(final, timeout=120000)
                body = resp.body()
                if body.startswith(b"%PDF-"):
                    dest.write_bytes(body)
                    return True
            return False
        except Exception as e:
            logger.debug("Browser-assisted fetch failed for %s: %s", url[:90], e)
            return False
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def download(self, index: int, paper: dict, pdf_url: str) -> dict:
        """Download PDF for a paper. Returns log entry."""
        filename = self._pdf_filename(index, paper)
        dest = self.output_dir / filename
        entry: dict[str, Any] = {
            "index": index,
            "title": paper.get("title", ""),
            "arnumber": paper.get("arnumber", ""),
            "filename": filename,
            "success": False,
            "error": None,
        }

        candidates = self._candidate_urls(paper, pdf_url)
        if not candidates:
            entry["error"] = "No PDF URL"
            self.log.append(entry)
            return entry

        existing = self._existing_pdf(paper)
        if existing:
            entry["success"] = True
            entry["skipped"] = True
            entry["existing_file"] = str(existing)
            entry["size_kb"] = round(existing.stat().st_size / 1024, 1)
            logger.info("Skipping existing PDF [%03d]: %s", index, existing.name)
            self.log.append(entry)
            return entry

        logger.info("Downloading PDF [%03d]: %s", index, paper.get("title", "")[:60])
        last_error = None
        for candidate_url in candidates:
            try:
                ok = self._download_file(candidate_url, dest)
                if ok and validate_pdf(dest):
                    size_kb = dest.stat().st_size / 1024
                    entry["success"] = True
                    entry["size_kb"] = round(size_kb, 1)
                    entry["url"] = candidate_url
                    logger.info("  OK (%.1f KB)", size_kb)
                    break
                if ok:
                    last_error = "Invalid PDF header"
                    logger.warning("  Invalid PDF from %s", candidate_url)
                else:
                    last_error = "No download permission or login expired"
                    logger.warning("  No download permission or login expired: %s", candidate_url)
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}"
                if e.code in (401, 403, 404):
                    if self.browser_context is None:
                        entry["skipped"] = True
                        logger.warning("  Skipping inaccessible PDF (HTTP %d): %s", e.code, candidate_url)
                    else:
                        logger.warning("  HTTP %d (will retry via browser): %s", e.code, candidate_url)
                    break
                logger.error("  HTTP %d from %s", e.code, candidate_url)
            except Exception as e:
                last_error = str(e)[:200]
                logger.error("  Failed from %s: %s", candidate_url, e)
            dest.unlink(missing_ok=True)

        # Browser-assisted fallback (Cloudflare/bot-protected endpoints, e.g. ScienceDirect)
        if not entry["success"] and candidates:
            logger.info("  Trying browser-assisted download...")
            if self._browser_fetch(candidates[0], dest) and validate_pdf(dest):
                size_kb = dest.stat().st_size / 1024
                entry["success"] = True
                entry["skipped"] = False
                entry["size_kb"] = round(size_kb, 1)
                entry["url"] = candidates[0]
                entry["mode"] = "browser"
                logger.info("  OK via browser (%.1f KB)", size_kb)
            else:
                dest.unlink(missing_ok=True)

        if not entry["success"]:
            entry["error"] = last_error or "No PDF URL"

        self.log.append(entry)
        return entry

    def save_log(self) -> None:
        """Write download log to JSON."""
        save_json(self.output_dir / "download_log.json", self.log)

    def summary(self) -> dict:
        """Return summary of download results."""
        total = len(self.log)
        success = sum(1 for e in self.log if e["success"] and not e.get("skipped"))
        skipped = sum(1 for e in self.log if e.get("skipped"))
        total_size = sum(e.get("size_kb", 0) for e in self.log if e["success"])
        return {
            "total": total,
            "success": success,
            "skipped": skipped,
            "failed": total - success - skipped,
            "total_size_kb": round(total_size, 1),
        }


def extract_pdf_urls(page) -> list[str]:
    """Extract possible PDF URLs from an IEEE Xplore detail page.

    Uses JavaScript evaluation on a Playwright page object.
    """
    js = """
    () => {
        const links = [];
        document.querySelectorAll('a').forEach(a => {
            const href = a.href || '';
            if (href.includes('/stamp/stamp.jsp') || href.includes('.pdf')) {
                links.push(href);
            }
        });
        return [...new Set(links)];
    }
    """
    try:
        return page.evaluate(js)
    except Exception:
        return []
