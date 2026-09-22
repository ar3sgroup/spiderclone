#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Reecriture des liens HTML, CSS et JS (profondeur, srcset, new URL, @import, specifiers npm).
"""

import os
import re
import urllib.parse

from bs4 import BeautifulSoup

from cs_config import (ATTR_RE, CSS_IMPORT_RE, CSS_SOURCEMAP_RE, CSS_URL_RE,
                       HTML_ATTRS, JS_PATTERNS, LAZY_ATTRS, META_IMAGE_PROPS,
                       SCRIPT_BLOCK_RE, SKIP_SCHEMES, TAG_RE)
from cs_utils import decode_any, is_html_url, is_static_url


def _looks_relative(spec: str) -> bool:
    """Un specifier 'nu' (npm : 'react', 'lodash') n'est pas un chemin local."""
    return "/" in spec or "." in spec or ":" in spec


class Rewriter:
    """Réécrit les URLs d'une page ou d'un asset vers le clone local.

    S'appuie sur le cloner (SiteClone) pour la file d'attente, le périmètre
    et les chemins locaux.
    """

    def __init__(self, cloner):
        self.c = cloner


    def queue_url(self, raw: str, base_url: str, depth: int) -> str:
        """Planifie le téléchargement d'une URL et renvoie sa destination locale.

        Fix depth : une page au-delà de max_depth n'est pas clonée — le lien
        pointe vers l'URL absolue du site live. Les assets sont toujours clonés.
        """
        raw = raw.strip()
        if not raw or raw.lower().startswith(SKIP_SCHEMES):
            return raw

        url = self.c._abs(base_url, raw)
        if not self.c._in_scope(url):
            return raw

        if self.c.only_assets and is_html_url(url):
            return url
        if self.c.only_pages and is_static_url(url):
            return url

        if depth > self.c.max_depth and is_html_url(url):
            return url

        if url not in self.c._seen:
            self.c._seen.add(url)
            self.c._queue.append((url, depth, base_url))

        try:
            if is_static_url(url):
                if url not in self.c.assets:
                    self.c.assets[url] = self.c._local_path(url)
                return self.rel_to(base_url, self.c.assets[url])
            if url not in self.c.pages:
                self.c.pages[url] = self.c._local_path(url)
            return self.rel_to(base_url, self.c.pages[url])
        except Exception:
            return raw

    def rewrite_url(self, val: str, base_url: str, depth: int) -> str:
        return self.queue_url(val, base_url, depth)

    def rel_to(self, base_url: str, target_rel: str) -> str:
        """Chemin relatif depuis le dossier de la page vers la cible."""
        base_rel = self.c.pages.get(base_url, "index.html")
        base_dir = os.path.dirname(base_rel)
        rel = os.path.relpath(target_rel, base_dir)
        return rel.replace("\\", "/")


    def rewrite_srcset(self, srcset: str, base_url: str) -> str:
        """Réécrit un srcset en préservant les URI data: (qui contiennent des virgules)."""
        chunks = []
        buf = ""
        for piece in srcset.split(","):
            piece = piece.strip()
            if buf:
                if re.fullmatch(r"[A-Za-z0-9+/=]+", piece):
                    buf += "," + piece
                    continue
                chunks.append(buf)
                buf = ""
            if piece.startswith("data:"):
                buf = piece
            else:
                chunks.append(piece)
        if buf:
            chunks.append(buf)

        out = []
        for chunk in chunks:
            chunk = chunk.strip()
            if not chunk:
                continue
            toks = chunk.split()
            url = toks[0]
            desc = " ".join(toks[1:])
            if url.startswith(("data:", "blob:")):
                out.append(chunk)
                continue
            new = self.queue_url(url, base_url, 1)
            out.append(new + ((" " + desc) if desc else ""))
        return ", ".join(out)


    def rewrite_inline_style(self, css: str, base_url: str) -> str:
        """Réécrit les url(...) d'un attribut style (chemin relatif depuis la page)."""
        def repl(m):
            quote, inner = m.group(1), m.group(2).strip()
            if not inner or inner.lower().startswith(("data:", "blob:", "#")):
                return m.group(0)
            return f"url({quote}{self.queue_url(inner, base_url, 1)}{quote})"
        return CSS_URL_RE.sub(repl, css)


    def rewrite_css(self, content: bytes, css_url: str) -> bytes:
        """Réécrit les références d'une feuille de style (url(), @import, sourcemap)."""
        text, enc = decode_any(content)
        target_rel = self.c.assets.get(css_url) or self.c._local_path(css_url)
        base_dir = os.path.dirname(target_rel)

        def queue_path(raw: str) -> str:
            """Télécharge et réécrit une référence CSS en chemin relatif local."""
            raw = raw.strip()
            if not raw or raw.lower().startswith(("data:", "blob:", "#")):
                return raw
            if self.c.only_pages:
                return raw
            try:
                absu = self.c._abs(css_url, raw)
                if not self.c._in_scope(absu):
                    return raw
                if absu not in self.c.assets:
                    self.c.assets[absu] = self.c._local_path(absu)
                    if absu not in self.c._seen:
                        self.c._seen.add(absu)
                        self.c._queue.append((absu, 1, css_url))
                return os.path.relpath(self.c.assets[absu], base_dir).replace("\\", "/")
            except Exception:
                return raw

        def repl_url(m):
            quote, inner = m.group(1), m.group(2).strip()
            new = queue_path(inner)
            if new == inner:
                return m.group(0)
            return f"url({quote}{new}{quote})"

        def repl_import(m):
            quote, inner = m.group(1), m.group(2).strip()
            media = (m.group(3) or "").strip()
            new = queue_path(inner)
            if new == inner:
                return m.group(0)
            suffix = f" {media}" if media else ""
            return f"@import '{new}'{suffix};"

        def repl_sourcemap(m):
            new = queue_path(m.group(2))
            if new == m.group(2):
                return m.group(0)
            return f"{m.group(1)}{new}"

        text = CSS_URL_RE.sub(repl_url, text)
        text = CSS_IMPORT_RE.sub(repl_import, text)
        text = CSS_SOURCEMAP_RE.sub(repl_sourcemap, text)
        return text.encode(enc, errors="replace")


    def rewrite_js(self, content: bytes, js_url: str) -> bytes:
        text, enc = decode_any(content)
        target_rel = self.c.assets.get(js_url) or self.c._local_path(js_url)
        base_dir = os.path.dirname(target_rel)
        return self.rewrite_js_text(text, js_url, base_dir).encode(enc, errors="replace")

    def rewrite_js_text(self, text: str, base_url: str, base_dir: str | None = None) -> str:
        """Réécrit les URLs string dans du code JavaScript (fetch, import, Worker...)."""
        if self.c.only_pages:
            return text
        if base_dir is None:
            base_dir = os.path.dirname(self.c.pages.get(base_url, "index.html"))
        for pat, bare_ok in JS_PATTERNS:
            def repl(m):
                inner = m.group(3).strip()
                if not inner or inner.lower().startswith(("data:", "blob:", "javascript:", "#")):
                    return m.group(0)
                if not bare_ok and not _looks_relative(inner):
                    return m.group(0)
                try:
                    absu = self.c._abs(base_url, inner)
                    if not self.c._in_scope(absu):
                        return m.group(0)
                    if absu not in self.c.assets:
                        self.c.assets[absu] = self.c._local_path(absu)
                        if absu not in self.c._seen:
                            self.c._seen.add(absu)
                            self.c._queue.append((absu, 1, base_url))
                    rel = os.path.relpath(self.c.assets[absu], base_dir).replace("\\", "/")
                    suffix = ""
                    if m.lastindex and m.lastindex >= 4 and m.group(4):
                        suffix = m.group(4)
                    return f"{m.group(1)}{m.group(2)}{rel}{m.group(2)}{suffix}"
                except Exception:
                    return m.group(0)
            text = pat.sub(repl, text)
        return text


    def rewrite_html(self, soup: BeautifulSoup, url: str, depth: int):
        """Réécrit les liens d'une page HTML (mode BeautifulSoup)."""
        for tag in soup.find_all("base"):
            tag.decompose()

        if soup.find("input", {"type": "password"}):
            self.c.saw_login = True

        for tag, attrs in HTML_ATTRS.items():
            for node in soup.find_all(tag):
                for attr in attrs:
                    val = node.get(attr)
                    if not val:
                        continue
                    val = val.strip()
                    if "srcset" in attr.lower() or attr.lower() == "imagesrcset":
                        node[attr] = self.rewrite_srcset(val, url)
                    else:
                        node[attr] = self.queue_url(val, url, depth + 1)

        for meta in soup.find_all("meta"):
            key = (meta.get("property") or meta.get("name") or "").lower()
            itemprop = (meta.get("itemprop") or "").lower()
            if key in META_IMAGE_PROPS or itemprop in ("image", "thumbnailurl"):
                val = meta.get("content")
                if val:
                    meta["content"] = self.queue_url(val, url, depth + 1)

        for node in soup.find_all(True):
            for attr in LAZY_ATTRS:
                val = node.get(attr)
                if not val:
                    continue
                if attr.endswith("srcset") or "srcset" in attr:
                    node[attr] = self.rewrite_srcset(val, url)
                else:
                    node[attr] = self.queue_url(val, url, depth + 1)
            st = node.get("style")
            if st and ("url(" in st.lower() or "@import" in st.lower()):
                node["style"] = self.rewrite_inline_style(st, url)

        for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh", re.I)}):
            c = meta.get("content") or ""
            m = re.search(r"url\s*=\s*(.+)$", c, re.I)
            if m:
                new_url = self.queue_url(m.group(1).strip(), url, depth + 1)
                meta["content"] = f"{c[:m.start(1)]}{new_url}"

        for script in soup.find_all("script", src=False):
            body = script.string or ""
            if body and any(p.search(body) for p, _ in JS_PATTERNS):
                script.string = self.rewrite_js_text(body, url)

    def rewrite_page_raw(self, text: str, url: str, depth: int) -> str:
        """Mode brut : réécrit les attributs sans reformater le HTML."""
        def repl_tag(m):
            tag = m.group(0)
            lower_tag = tag.lower()
            if lower_tag.startswith("<base"):
                return ""
            is_refresh = re.search(r"http-equiv\s*=\s*[\"']?\s*refresh", lower_tag) is not None

            def repl_attr(am):
                name = am.group("name").lower()
                quote, val = am.group("quote"), am.group("val")
                stripped = val.strip()
                if name in ("href", "src", "action", "data", "poster", "cite") \
                        and not stripped.lower().startswith(SKIP_SCHEMES):
                    return f'{name}={quote}{self.queue_url(val, url, depth + 1)}{quote}'
                if name in ("srcset", "imagesrcset") or (name.startswith("data-") and "srcset" in name):
                    return f'{name}={quote}{self.rewrite_srcset(stripped, url)}{quote}'
                if name in LAZY_ATTRS:
                    return f'{name}={quote}{self.queue_url(val, url, depth + 1)}{quote}'
                if name == "style" and ("url(" in val.lower() or "@import" in val.lower()):
                    return f'{name}={quote}{self.rewrite_inline_style(val, url)}{quote}'
                if name == "content" and is_refresh:
                    mm = re.search(r"url\s*=\s*(.+)$", val, re.I)
                    if mm:
                        new_url = self.queue_url(mm.group(1).strip(), url, depth + 1)
                        return f'{name}={quote}{val[:mm.start(1)]}{new_url}{quote}'
                return am.group(0)

            return ATTR_RE.sub(repl_attr, tag)

        text = TAG_RE.sub(repl_tag, text)

        def repl_script(ms):
            attrs, body = ms.group("attrs"), ms.group("body")
            if re.search(r"\bsrc\s*=", attrs, re.I):
                return ms.group(0)
            if body and any(p.search(body) for p, _ in JS_PATTERNS):
                return f"<script{attrs}>{self.rewrite_js_text(body, url)}</script>"
            return ms.group(0)

        return SCRIPT_BLOCK_RE.sub(repl_script, text)
