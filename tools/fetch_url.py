# SPDX-License-Identifier: CC0-1.0

"""Fetch a URL and return its readable text content.

Two extraction paths:

  1. If `trafilatura` is installed locally, use it — the same readability
     heuristics openclaw's web-readability plugin uses, ported to Python.
     Best quality, but trafilatura pulls in lxml + 6 other deps so it's
     deliberately NOT in requirements.txt (`pip install trafilatura` to
     opt in).
  2. Otherwise, a stdlib-only extractor built on `html.parser`. It
     strips script/style/nav/header/footer/aside, decodes HTML entities,
     and emits markdown — h1-h6 as `#` headings, <pre><code> as fenced
     blocks with language hints from `class="language-X"`, ul/ol as
     `- ` / `N. ` lists with indentation for nesting, tables as
     pipe-separated rows, links as [text](url). Lower extraction
     quality than trafilatura on complex pages (sidebars and cookie
     banners can leak through) but zero new dependencies and the
     markdown formatting closes most of the structural-content gap.

Only http(s) URLs accepted. file://, ftp://, etc. rejected — reading
local files is what `read_file` is for, and allowing file:// here would
back-door around kin-scoped path resolution."""

import html
import html.parser
import ipaddress
import socket
import urllib.error
import urllib.parse
import urllib.request


# Asymmetric caps. The body cap (1 MB) is large because modern pages
# carry tens of KB of <head>/nav/CSS-class-soup before the actual
# article starts — 64KB wasn't enough on Wikipedia, which can hit ~200KB
# of HTML before the first <p> in the article body. The output cap
# (64 KB chars) is what actually constrains context spend, since the
# extractor strips the boilerplate before this number kicks in.
_MAX_BYTES = 1048576
_MAX_OUTPUT_CHARS = 65536
_DEFAULT_TIMEOUT = 30       # seconds
_USER_AGENT = "Hearthkin/0.1 (+local model agent; Python urllib)"


# ─── SSRF guard ─────────────────────────────────────────────────────────────
#
# The model controls the URL passed to fetch_url. Without host filtering, a
# prompt-injected kin (or a Telegram user with the 'read' bucket) can reach
# loopback (Ollama's API on 127.0.0.1:11434), link-local (cloud metadata at
# 169.254.169.254), or RFC1918 internal services and exfiltrate the contents
# back through chat. We block any host that resolves to a private/loopback/
# link-local/reserved/multicast address — both at the initial request and on
# every HTTP redirect (urllib's default redirect handler does NOT re-validate
# the destination). DNS rebinding still has a small window between resolve
# and connect; that's an acceptable residual for a desktop chat client.
class _SSRFBlocked(Exception):
    """Raised when a request target resolves to a disallowed address."""
    pass


def _resolve_addresses(host):
    """Return a list of resolved IPs for `host`, or [] on resolution
    failure. Used by the SSRF check."""
    try:
        infos = socket.getaddrinfo(host, None)
    except (socket.gaierror, UnicodeError):
        return []
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):
            continue
    return out


def _is_disallowed_host(host):
    """True if `host` (a string from a parsed URL) resolves to any IP that
    we won't fetch — loopback / private / link-local / reserved / multicast
    / unspecified. Fails CLOSED on resolution failure: if we can't resolve,
    we don't know it's safe, so we refuse."""
    if not host:
        return True
    # Strip IPv6 brackets if present.
    bare = host.strip("[]")
    # If the host is itself a literal IP, classify directly without DNS.
    try:
        ip = ipaddress.ip_address(bare)
        return _ip_is_blocked(ip)
    except ValueError:
        pass
    addrs = _resolve_addresses(bare)
    if not addrs:
        return True  # fail closed
    return any(_ip_is_blocked(ip) for ip in addrs)


def _ip_is_blocked(ip):
    # IPv4-mapped IPv6 (::ffff:127.0.0.1) and 6to4 (2002::/16) embed an
    # IPv4 address; classify the embedded address instead of the IPv6
    # wrapper. Pre-3.13 ipaddress doesn't propagate is_loopback / is_private
    # through the mapping, so `::ffff:127.0.0.1` would otherwise pass.
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:
        return _ip_is_blocked(mapped)
    six = getattr(ip, "sixtofour", None)
    if six is not None:
        return _ip_is_blocked(six)
    return (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


class _SSRFGuardRedirectHandler(urllib.request.HTTPRedirectHandler):
    """urllib follows redirects by default with no host re-check, so a
    public URL that 302s to http://169.254.169.254/... would be followed
    transparently. This handler re-runs the SSRF check on every redirect
    target before letting urllib build the next request."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlparse(newurl)
        # Same scheme gate as the initial request: a 3xx to ftp:// (or
        # file://, etc.) would otherwise be handed to urllib's default
        # handler for that scheme.
        if parsed.scheme.lower() not in ("http", "https"):
            raise _SSRFBlocked(
                f"refusing redirect to non-http(s) URL "
                f"(scheme: {parsed.scheme or '(none)'})"
            )
        host = parsed.hostname
        if _is_disallowed_host(host):
            raise _SSRFBlocked(
                f"refusing redirect to disallowed host: {host or '(none)'}"
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# An explicit EMPTY ProxyHandler disables urllib's default pickup of the
# HTTP_PROXY / HTTPS_PROXY environment variables. With an env proxy in play,
# urllib hands the full URL to the proxy and the PROXY does the DNS
# resolution + connection — so the SSRF guard's local getaddrinfo check no
# longer reflects what actually gets connected to (2026-07 security audit H2).
# Fetching directly keeps the guard's vetted-IP reasoning authoritative.
_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    _SSRFGuardRedirectHandler(),
)

# Tags whose entire content is dropped during extraction. Includes the
# obvious script/style, plus structural noise (nav, header, footer,
# aside) that's almost always boilerplate around the real content.
# Void elements (input, embed, etc.) are intentionally NOT here — they
# have no content to skip, AND including them broke the depth counter
# because html.parser fires only handle_starttag for void tags, never
# handle_endtag. On Wikipedia the unclosed <input>s inside <header>
# inflated skip_depth past 0 and kept the extractor in skip mode for
# the entire rest of the document.
_SKIP_TAGS = frozenset({
    "script", "style", "noscript", "template", "svg",
    "nav", "header", "footer", "aside",
    "form", "button", "select", "textarea", "label",
    "iframe", "object", "video", "audio",
})

# HTML5 void elements — single-tag, no content, no close. Listed here
# so handle_starttag knows not to push onto the skip stack for void
# elements that happen to also be in _SKIP_TAGS (currently none — but
# the check is defense in depth in case someone adds, say, `img` to
# SKIP_TAGS without realizing).
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
})

# Tags treated as "block" — text inside them gets a paragraph break
# before/after so the output reads as separated paragraphs rather than
# one flowed run-on. <div>/<span> are intentionally NOT here — too many
# pages wrap inline text in divs, and splitting on every div fragments
# paragraphs that should read as one.
_BLOCK_TAGS = frozenset({
    "p", "section", "article", "main",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "blockquote", "pre", "dt", "dd",
    "tr", "br", "hr",
})

_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})


class _ReadableExtractor(html.parser.HTMLParser):
    """Stdlib HTML → readable markdown. Walks the document, skips
    boilerplate tags entirely (script, style, nav, aside, etc.), inserts
    paragraph breaks around block-level tags, and formats structured
    content as markdown: h1-h6 as `#` headings; `<pre>` (with or
    without an inner `<code class="language-X">`) as triple-backtick
    fences; inline `<code>` outside any `<pre>` as single-backtick
    `` `text` ``; `<ul>`/`<ol>` as `- ` / `N. ` list items with
    two-space indent per nesting level; `<table>` as pipe-separated
    rows; `<a href="...">` as `[text](url)` with relative hrefs
    resolved against `base_url` when provided. Entity decoding is
    automatic (convert_charrefs=True).

    The markdown layer is what closes the gap with trafilatura on
    structured pages (GitHub issues, ReadTheDocs, blog posts with code).
    Trafilatura still wins on extraction quality (finding the article body
    on pages with heavy chrome); the stdlib path's job is to make the
    fallback output *usable*, not just *readable*."""

    def __init__(self, base_url=""):
        super().__init__(convert_charrefs=True)
        self._base_url = base_url or ""
        self._skip_depth = 0
        self._parts = []
        self._buf = []
        # Markdown formatting state.
        self._heading_level = None      # 1-6 while inside h1-h6
        self._list_stack = []           # stack of [kind, counter] per nested ul/ol
        self._pre_depth = 0             # >0 while inside <pre> (fence-mode flushes)
        self._code_lang = None          # language hint from class="language-X"
        self._inline_code_starts = []   # buf positions for nested inline <code>
        self._link_stack = []           # stack of (href, buf_start_idx) per <a>
        self._table = None              # {"rows": [...], "first_row_has_th": bool}
        self._table_row = None          # list of cell strings while inside <tr>
        self._table_row_is_header = False
        self._cell_buf = None           # text buffer while inside <td>/<th>

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS and tag not in _VOID_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        # Table machinery bypasses the normal flush flow because cells
        # accumulate into their own buffer and the whole table emits
        # atomically on </table>.
        if tag == "table":
            self._flush()
            self._table = {"rows": [], "first_row_has_th": False}
            return
        if tag == "tr" and self._table is not None:
            self._flush()
            self._table_row = []
            self._table_row_is_header = False
            return
        if tag in ("td", "th") and self._table_row is not None:
            self._cell_buf = []
            if tag == "th":
                self._table_row_is_header = True
            return
        # Link — remember href (resolved against base_url for relative
        # URLs) and the buffer position so the closing tag can rewrite
        # the inner text as [text](url). In-page anchors (#section) and
        # javascript: hrefs are stripped — the document structure is
        # gone after extraction, so anchors don't lead anywhere useful
        # and scripts have no place in extracted text.
        if tag == "a":
            href = ""
            for k, v in attrs:
                if k == "href" and v:
                    href = v
                    break
            if href and not href.startswith(("javascript:", "#")):
                if self._base_url:
                    href = urllib.parse.urljoin(self._base_url, href)
                self._link_stack.append((href, len(self._buf)))
            else:
                self._link_stack.append((None, -1))
            return
        # Lists — flush pending text under the OUTER list context before
        # pushing the new (inner) list, so outer-text doesn't get prefixed
        # with the inner list marker.
        if tag in ("ul", "ol"):
            self._flush()
            self._list_stack.append([tag, 1])
            return
        # <pre> is the block-level code fence trigger. <code> by itself
        # (no enclosing <pre>) is inline code — single-backtick wrap.
        # <code> inside <pre> just contributes a language-hint class.
        if tag == "pre":
            self._flush()
            self._pre_depth += 1
            return
        if tag == "code":
            if self._pre_depth > 0:
                for k, v in attrs:
                    if k == "class" and v:
                        for c in v.split():
                            if c.startswith("language-"):
                                self._code_lang = c[len("language-"):]
                                break
                        break
            else:
                self._inline_code_starts.append(len(self._buf))
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag in _HEADING_TAGS:
            self._heading_level = int(tag[1])

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS and tag not in _VOID_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if tag in ("td", "th") and self._cell_buf is not None:
            cell = " ".join("".join(self._cell_buf).split())
            if self._table_row is not None:
                self._table_row.append(cell)
            self._cell_buf = None
            return
        if tag == "tr" and self._table is not None:
            if self._table_row:
                self._table["rows"].append(self._table_row)
                if len(self._table["rows"]) == 1 and self._table_row_is_header:
                    self._table["first_row_has_th"] = True
            self._table_row = None
            self._table_row_is_header = False
            return
        if tag == "table" and self._table is not None:
            self._emit_table(self._table)
            self._table = None
            return
        if tag == "a" and self._link_stack:
            href, start_idx = self._link_stack.pop()
            if href and 0 <= start_idx <= len(self._buf):
                link_text = " ".join("".join(self._buf[start_idx:]).split())
                if link_text:
                    self._buf[start_idx:] = [f"[{link_text}]({href})"]
            return
        # Inline <code> closing: wrap the buffered slice in single
        # backticks. NOT triple-fenced — that's only for block code
        # inside <pre>. We don't collapse whitespace inside inline code
        # (the join at flush time still hits the surrounding paragraph;
        # multi-space inside `code` survives mostly intact).
        if tag == "code" and self._pre_depth == 0:
            if self._inline_code_starts:
                start_idx = self._inline_code_starts.pop()
                if 0 <= start_idx <= len(self._buf):
                    code_text = "".join(self._buf[start_idx:])
                    if code_text:
                        self._buf[start_idx:] = [f"`{code_text}`"]
            return
        # <code> inside <pre>: no special closing action — the fence
        # flush happens at </pre>.
        if tag == "code":
            return
        # </pre> closing: flush accumulated content as a fenced block,
        # decrement depth, drop the language hint when fully exited.
        if tag == "pre":
            if self._pre_depth > 0:
                self._flush()
                self._pre_depth -= 1
                if self._pre_depth == 0:
                    self._code_lang = None
            return
        if tag in ("ul", "ol"):
            self._flush()
            if self._list_stack:
                self._list_stack.pop()
            return
        if tag in _BLOCK_TAGS:
            self._flush()
            if tag in _HEADING_TAGS:
                self._heading_level = None
            elif tag == "li" and self._list_stack and self._list_stack[-1][0] == "ol":
                self._list_stack[-1][1] += 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        if not data:
            return
        # Cell content goes to the cell buffer; everything else to the
        # main buffer. This is what keeps table cells from leaking into
        # surrounding paragraphs.
        if self._cell_buf is not None:
            self._cell_buf.append(data)
        else:
            self._buf.append(data)

    def _flush(self):
        if not self._buf:
            return
        chunk_raw = "".join(self._buf)
        self._buf = []
        # Inside <pre>: preserve whitespace; wrap in triple-backtick
        # fences. Inline <code> uses single backticks at handle_endtag
        # time, not this path.
        if self._pre_depth > 0:
            text = chunk_raw.strip("\n")
            if not text.strip():
                return
            lang = self._code_lang or ""
            self._parts.append(f"```{lang}\n{text}\n```")
            return
        chunk = chunk_raw.strip()
        if not chunk:
            return
        chunk = " ".join(chunk.split())
        if self._heading_level:
            chunk = "#" * self._heading_level + " " + chunk
        elif self._list_stack:
            depth = len(self._list_stack) - 1
            indent = "  " * depth
            kind, counter = self._list_stack[-1]
            if kind == "ul":
                chunk = indent + "- " + chunk
            else:
                chunk = indent + f"{counter}. " + chunk
        self._parts.append(chunk)

    def _emit_table(self, table):
        rows = table["rows"]
        if not rows:
            return
        lines = []
        for i, row in enumerate(rows):
            cells = [c if c else " " for c in row]
            lines.append("| " + " | ".join(cells) + " |")
            if i == 0 and table["first_row_has_th"]:
                lines.append("| " + " | ".join("---" for _ in row) + " |")
        self._parts.append("\n".join(lines))

    def get_text(self):
        self._flush()
        # Drop chrome-like one-liners that survived (very short lines
        # tend to be nav items: "Subscribe", "Menu", "Skip to content").
        # Markdown-prefixed lines (headings, list items, code fences,
        # table rows) all carry enough prefix to clear the 4-char floor,
        # so this only filters bare-paragraph chrome.
        cleaned = []
        for p in self._parts:
            if len(p) < 4:
                continue
            cleaned.append(p)
        return "\n\n".join(cleaned)


def _extract_stdlib(html_text, base_url=""):
    """Stdlib-only extraction. Returns markdown-formatted text with
    paragraph breaks. `base_url` lets the extractor resolve relative
    `<a href>` targets into absolute URLs via urljoin — pass the page's
    own URL when available."""
    parser = _ReadableExtractor(base_url=base_url)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        # Malformed HTML — html.parser is fairly tolerant, but if it
        # gives up partway through, take what we got.
        pass
    return parser.get_text()


def _extract_trafilatura(html_text, url):
    """Optional trafilatura path. Returns text or None if unavailable."""
    try:
        import trafilatura  # local import: optional dep
    except ImportError:
        return None
    try:
        return trafilatura.extract(
            html_text,
            url=url,
            include_comments=False,
            include_tables=True,
            favor_recall=True,
        )
    except Exception:
        return None


def fetch_url(url: str) -> str:
    """Fetch a web page and return its main readable text content. Use
    this when you need to read something on the web — an article, a
    documentation page, a forum post, a blog entry. The page's
    boilerplate (navigation, ads, footers, cookie banners) is stripped
    out before the text comes back to you, so what you see is roughly
    "the article" rather than the whole HTML soup.

    Only `http://` and `https://` URLs are accepted. To read a local
    file on disk, use `read_file` instead — it knows about your kin
    directory and is the safer path.

    Extraction quality depends on whether `trafilatura` is installed
    on the host. Without it, a stdlib extractor handles the common
    cases (nav/footer stripping, entity decoding, paragraph breaks)
    but may leak more chrome on complex sites.

    The fetched body is capped at 1 MB; the cleaned text returned to
    you is capped at 64K characters with an explicit `[truncated]`
    marker so you know there's more to fetch with a follow-up call if
    needed.
    Returns a brief error string (no exception) on malformed URLs,
    network failures, timeouts, or content that can't be parsed as
    text.
    """
    if not url or not isinstance(url, str):
        return "fetch_url: url was empty."
    url = url.strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return (
            f"fetch_url: only http(s) URLs are supported "
            f"({parsed.scheme or '(no scheme)'} given). "
            f"For local files use read_file."
        )
    if not parsed.netloc:
        return "fetch_url: URL is missing a host."
    if _is_disallowed_host(parsed.hostname):
        # Loopback, private (RFC1918), link-local (incl. cloud metadata
        # 169.254.169.254), reserved, multicast, or unresolvable. We
        # refuse rather than reach internal services on the operator's
        # network. If the operator genuinely needs to fetch from a local
        # service, they can use read_file or the model's own knowledge.
        return (
            f"fetch_url: refusing to fetch from disallowed host "
            f"({parsed.hostname!r}). Only public internet hosts are "
            f"allowed; private / loopback / link-local / metadata "
            f"endpoints are blocked."
        )

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
            "Accept-Language": "en-US,en;q=0.8",
        },
    )
    try:
        with _OPENER.open(req, timeout=_DEFAULT_TIMEOUT) as resp:
            raw = resp.read(_MAX_BYTES + 1)
            content_type = (resp.headers.get("Content-Type") or "").lower()
    except _SSRFBlocked as e:
        return f"fetch_url: {e}"
    except urllib.error.HTTPError as e:
        return f"fetch_url: HTTP {e.code} {e.reason} from {url}"
    except urllib.error.URLError as e:
        return f"fetch_url: network error fetching {url}: {e.reason}"
    except TimeoutError:
        return f"fetch_url: timed out after {_DEFAULT_TIMEOUT}s fetching {url}"
    except Exception as e:
        return f"fetch_url: could not fetch {url}: {e}"

    body_truncated = len(raw) > _MAX_BYTES
    if body_truncated:
        raw = raw[:_MAX_BYTES]

    # Decode tolerantly — many pages declare utf-8 but serve cp1252 or
    # latin-1 in practice. Same fallback chain robust_decode uses, but
    # inlined here so tools/_io doesn't need to be a dep just for this.
    text_raw = None
    for enc in ("utf-8", "cp1252"):
        try:
            text_raw = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text_raw is None:
        text_raw = raw.decode("utf-8", errors="replace")

    # Try trafilatura first if installed; fall back to stdlib extractor.
    cleaned = _extract_trafilatura(text_raw, url)
    if not cleaned:
        cleaned = _extract_stdlib(text_raw, base_url=url)
    if cleaned:
        cleaned = cleaned.strip()

    if not cleaned:
        kind = content_type or "(no Content-Type)"
        return (
            f"fetch_url: no extractable text from {url} "
            f"(Content-Type: {kind}). The page may be JavaScript-only "
            f"or a binary format; try a different URL or a direct API."
        )

    truncated_text = len(cleaned) > _MAX_OUTPUT_CHARS
    if truncated_text:
        cleaned = cleaned[:_MAX_OUTPUT_CHARS] + "\n\n[truncated]"
    elif body_truncated:
        cleaned += "\n\n[response body capped at 1 MB; some content may be missing]"
    return cleaned
