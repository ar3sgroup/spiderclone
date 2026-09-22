#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Orchestrateur du clone (SiteClone) : file BFS, pool de threads, manifest periodique, reprise, collisions Windows.
"""

import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import warnings
import zipfile
from collections import Counter, deque
from concurrent import futures as cf
from pathlib import Path

import requests
from bs4 import BeautifulSoup, XMLParsedAsHTMLWarning

from cs_config import (ADAPTIVE_MAX_DELAY, ADAPTIVE_MIN_DELAY, ADAPTIVE_RATIO,
                       CSS_URL_RE, DEFAULT_TIMEOUT, LAZY_ATTRS, MANIFEST_NAME,
                       META_IMAGE_PROPS, REPORT_NAME, SECOND_PASS_COOLDOWN,
                       SKIP_SCHEMES, USER_AGENT, VERSION)
from cs_fetch import (CloudflareChallengeError, DeadLinkError, FetchResult,
                      ProtectedError, RateLimitedError, Throttle, fetch_once,
                      load_robots,
                      robots_path_allowed)
from cs_render import render_html, screenshot_pages
from cs_rewrite import Rewriter
from cs_utils import (decode_any, extract_api_endpoints, is_html_url,
                      is_static_url, looks_like_html, sanitize_filename,
                      srcset_urls)
import cs_ui

MANIFEST_PERIODIC = 25
SMAP_MAX_LEVELS = 2
SMAP_MAX_CHILDREN = 5

PNG_PLACEHOLDER = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
SVG_PLACEHOLDER = ('<svg xmlns="http://www.w3.org/2000/svg" '
                   'width="1" height="1" viewBox="0 0 1 1"/>')


def _parse_markup(content):
    """Construit un BeautifulSoup(html.parser) sans émettre
    XMLParsedAsHTMLWarning (pages XML/XHTML légitimes : flux RSS,
    sitemaps, XHTML servis avec un Content-Type HTML). Le parsing
    reste en mode HTML : la réécriture d'attributs fonctionne pour
    les deux. Le warning est filtré, pas le document."""
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        return BeautifulSoup(content, "html.parser")


class SiteClone:
    """Clone un site web (pages HTML, assets) avec profondeur et politesse."""

    def __init__(self, start_url: str, out_dir: str, max_depth: int = 3,
                 workers: int = 8, include_subdomains: bool = False,
                 external: bool = False, user_agent: str = USER_AGENT,
                 delay: float = 0.0, max_size: int = 50 * 1024 * 1024,
                 verbose: bool = False, raw: bool = False, render: bool = False,
                 cookies: str | None = None, cookie_file: str | None = None,
                 headers: list | None = None, proxy: str | None = None,
                 check: bool = True,
                 resume: bool = False, respect_robots: bool = False,
                 sitemap=None, zip_file: str | None = None,
                 menu: bool = False, rate: float = 0.0,
                 max_urls: int = 0, max_time: int = 0,
                 only_assets: bool = False, only_pages: bool = False,
                 screenshot: bool = False):
        parsed = urllib.parse.urlsplit(start_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"URL invalide : {start_url}")
        self.start_url = start_url
        self.base_host = (parsed.hostname or "").lower()
        self.base_scheme = parsed.scheme.lower() or "http"
        self.include_subdomains = include_subdomains
        self.external = external
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.max_depth = max(0, int(max_depth))
        self.workers = max(1, int(workers))
        self.delay = max(0.0, float(delay))
        self.max_size = int(max_size)
        self.verbose = verbose
        self.raw = raw
        self.render = render
        self.check = check
        self.resume = resume
        self.respect_robots = respect_robots
        self.sitemap = sitemap
        self.zip_file = zip_file
        self.menu = menu
        self.rate = max(0.0, float(rate))
        self.max_urls = max(0, int(max_urls))
        self.max_time = max(0, int(max_time))
        self.manifest_path = self.out_dir / MANIFEST_NAME
        self.user_agent = user_agent

        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        if headers:
            for h in headers:
                if ":" in h:
                    k, v = h.split(":", 1)
                    self.session.headers[k.strip()] = v.strip()
        if cookies:
            self._parse_cookies(cookies)
        if cookie_file:
            self._load_cookie_file(cookie_file)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        self.pages = {}
        self.assets = {}
        self._seen = set()
        self._queue = deque()
        self.failures = []
        self.protected = []
        self.rate_limited = []
        self.dead_urls = set()
        self.stats = Counter()
        self.total_bytes = 0
        self.saw_login = False
        self._done_count = 0
        self._path_registry = {}
        self.robots_rules = None
        self.menu_file = None
        self.zip_path = None
        self.only_assets = only_assets
        self.only_pages = only_pages
        self.screenshot = screenshot
        self.stats["placeholders"] = 0
        self._submitted = 0
        self._second_pass = False
        self._second_pass_urls = set()

        self.rewriter = Rewriter(self)
        self.throttle = Throttle(rate)

        self._seed()
        if self.respect_robots:
            self._load_robots()
        if self.sitemap:
            self._load_sitemap()


    def _normalize(self, url: str) -> str:
        """Normalise une URL (hôte en minuscules, fragment retiré, port par défaut)."""
        p = urllib.parse.urlsplit(url)
        scheme = p.scheme.lower() or "http"
        host = p.hostname.lower() if p.hostname else ""
        port = p.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            netloc = host
        else:
            netloc = host if port is None else p.netloc.lower()
        path = p.path or "/"
        if p.query:
            path += "?" + p.query
        return urllib.parse.urlunsplit((scheme, netloc, path, "", ""))

    def _abs(self, base: str, ref: str) -> str:
        """Résout une référence en URL absolue normalisée."""
        return self._normalize(urllib.parse.urljoin(base, ref))

    def _in_scope(self, url: str) -> bool:
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https"):
            return False
        host = p.hostname.lower()
        if self.external:
            return True
        if host == self.base_host:
            return True
        if self.include_subdomains:
            base = self.base_host
            if base.startswith("www."):
                base = base[4:]
            if host == base or host.endswith("." + base):
                return True
        return False

    def _base_local_path(self, url: str) -> str:
        """Chemin local de base d'une URL (sans gestion des collisions)."""
        p = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(p.path)
        is_dir = path.endswith("/") or (not path and not p.query)
        has_query = bool(p.query)
        ext = os.path.splitext(path)[1].lower()
        no_ext = not ext

        if path in ("", "/"):
            return "index.html"

        parts = [sanitize_filename(seg) for seg in path.strip("/").split("/")]
        if no_ext and not is_dir and not has_query:
            parts[-1] += ".html"
            no_ext = False
        if is_dir or no_ext:
            parts.append("index.html")
        name = parts[-1]
        return "/".join(parts[:-1] + [name]) if len(parts) > 1 else name

    def _unique_rel(self, rel: str, url: str) -> str:
        """Rend un chemin local unique, insensible à la casse (fix Windows)."""
        key = rel.lower()
        existing = self._path_registry.get(key)
        if existing is None:
            self._path_registry[key] = url
            return rel
        if existing == url:
            return rel
        stem, fext = os.path.splitext(rel)
        size = 8
        while True:
            h = hashlib.md5(url.encode("utf-8")).hexdigest()[:size]
            candidate = f"{stem}__{h}{fext}"
            if candidate.lower() not in self._path_registry:
                self._path_registry[candidate.lower()] = url
                return candidate
            size += 4

    def _local_path(self, url: str) -> str:
        """Convertit une URL en chemin relatif local unique (sans out_dir)."""
        return self._unique_rel(self._base_local_path(url), url)

    def _local_rel_for(self, url: str) -> str:
        """Chemin local déjà connu (manifest, dictionnaires) ou calculé."""
        return self.pages.get(url) or self.assets.get(url) or self._local_path(url)

    def _register_path(self, rel: str, url: str):
        self._path_registry.setdefault(rel.lower(), url)


    def _seed(self):
        self._seen.add(self.start_url)
        self._queue.append((self.start_url, 0, None))


    def _parse_cookies(self, raw: str):
        for kv in raw.split(";"):
            kv = kv.strip()
            if "=" in kv:
                k, v = kv.split("=", 1)
                self.session.cookies.set(k.strip(), v.strip())

    def _load_cookie_file(self, path: str):
        """Charge un fichier de cookies au format Netscape (export HTTPie/curl)."""
        try:
            lines = Path(path).read_text("utf-8", errors="replace").splitlines()
        except OSError as e:
            raise ValueError(f"cookie-file illisible : {path} ({e})") from e
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            f = line.split("\t")
            if len(f) >= 7:
                domain = f[0].lstrip(".")
                path_c = f[2]
                name, value = f[5], f[6]
                self.session.cookies.set(name, value, domain=domain, path=path_c)


    def _load_robots(self):
        self.robots_rules = load_robots(self.session, self.base_scheme,
                                        self.base_host, self.user_agent)
        if self.robots_rules is not None:
            if self.robots_rules.crawl_delay is not None:
                self.throttle.set_delay(self.base_host, self.robots_rules.crawl_delay)
                if self.verbose:
                    print(f"   robots: crawl-delay {self.robots_rules.crawl_delay}s "
                          f"appliqué pour {self.base_host}")
            if self.verbose:
                print(f"   robots: {len(self.robots_rules.disallow)} Disallow "
                      f"et {len(self.robots_rules.allow)} Allow chargés")

    def _robots_allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        return robots_path_allowed(self.robots_rules, url)


    def _load_sitemap(self):
        """Bootstrap : ajoute à la file les URLs des sitemaps, en suivant les
        sitemaps index récursivement (niveau ≤ SMAP_MAX_LEVELS)."""
        candidates = []
        if self.sitemap is True:
            candidates = [f"{self.base_scheme}://{self.base_host}/sitemap.xml",
                          f"{self.base_scheme}://{self.base_host}/sitemap_index.xml"]
        elif isinstance(self.sitemap, str):
            candidates = [self._abs(self.start_url, self.sitemap)]

        found = set()
        seen_index = set()

        def crawl_sitemap(url: str, level: int):
            if level > SMAP_MAX_LEVELS or url in seen_index:
                return
            seen_index.add(url)
            try:
                r = self.session.get(url, timeout=DEFAULT_TIMEOUT)
                if r.status_code != 200:
                    return
            except requests.RequestException:
                return
            text = r.text
            if "<sitemap" in text.lower():
                subs = re.findall(r"<sitemap>\s*<loc>\s*([^<]+?)\s*</loc>", text, re.I)
                for loc in subs[:SMAP_MAX_CHILDREN]:
                    crawl_sitemap(self._abs(url, loc.strip()), level + 1)
            else:
                for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", text, re.I):
                    u = self._normalize(loc.strip())
                    if self._in_scope(u):
                        found.add(u)

        for cand in candidates:
            crawl_sitemap(cand, 1)

        count = 0
        for u in sorted(found):
            if not self._robots_allowed(u):
                continue
            if u not in self._seen:
                self._seen.add(u)
                self._queue.append((u, 1, self.start_url))
                count += 1
        if count:
            print(cs_ui.info(f"Sitemap : {count} URL(s) ajoutée(s) au crawl"))


    def _limits_reached(self, elapsed: float) -> bool:
        if self.max_urls and len(self._seen) >= self.max_urls:
            return True
        if self.max_time and elapsed >= self.max_time:
            return True
        return False


    def _save_bytes(self, data: bytes, rel_path: str) -> Path:
        dest = self.out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def _on_login(self):
        self.saw_login = True


    def _load_manifest(self):
        """Recharge l'état d'un clone précédent (URLs, chemins, types)."""
        if not self.manifest_path.exists():
            return
        try:
            data = json.loads(self.manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        files = data.get("files", {})
        if not isinstance(files, dict):
            return
        self.pages = {}
        self.assets = {}
        self._seen = set()
        for url, info in files.items():
            if not isinstance(info, dict) or "rel" not in info:
                continue
            self._seen.add(url)
            if info.get("type") == "page":
                self.pages[url] = info["rel"]
            else:
                self.assets[url] = info["rel"]
            self._register_path(info["rel"], url)
        for url in self.pages:
            self._queue.append((url, 1, self.start_url))
        for url in self.assets:
            self._queue.append((url, 1, self.start_url))
        if self.start_url not in self.pages:
            self._seen.add(self.start_url)
            self._queue.append((self.start_url, 0, None))
        print(cs_ui.info(f"Reprise : {len(self.pages)} page(s), "
                         f"{len(self.assets)} asset(s) connus"))

    def _save_manifest(self):
        files = {}
        for url, rel in self.pages.items():
            files[url] = {"rel": rel, "type": "page"}
        for url, rel in self.assets.items():
            files[url] = {"rel": rel, "type": "asset"}
        payload = {
            "spiderclone": VERSION,
            "start_url": self.start_url,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": files,
        }
        try:
            self.manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
        except OSError:
            pass


    def run(self):
        print(cs_ui.step(f"Clone de {self.start_url} -> {self.out_dir}"))
        print(cs_ui.info(f"Profondeur max : {self.max_depth} | ouvriers : {self.workers}")
              + "\n")
        if self.resume:
            self._load_manifest()
        t0 = time.time()

        pool = None
        try:
            pool = cf.ThreadPoolExecutor(max_workers=self.workers,
                                         thread_name_prefix="clone")
            while True:
                futures = {}
                cancelled = False
                while self._queue or futures:
                    cancelled = self._limits_reached(time.time() - t0)
                    while self._queue:
                        url, depth, referer = self._queue.popleft()
                        if cancelled:
                            continue
                        try:
                            if self.delay and depth > 0:
                                time.sleep(self.delay)
                            f = pool.submit(self._process, url, depth, referer)
                            futures[f] = url
                            self._submitted += 1
                        except Exception as e:
                            self._note_failure(url, str(e))
                        self._progress()

                    if cancelled and not futures:
                        break

                    done, _ = cf.wait(futures, return_when=cf.FIRST_COMPLETED)
                    for f in done:
                        url = futures.pop(f)
                        try:
                            f.result()
                        except Exception as e:
                            self._note_failure(url, str(e))
                        self._done_count += 1
                        if self._done_count % MANIFEST_PERIODIC == 0:
                            self._save_manifest()
                        self._progress()

                if cancelled or self._second_pass:
                    break
                if not self.rate_limited:
                    break

                self._maybe_adaptive_backoff()
                print(cs_ui.step(f"2e passe : {len(self.rate_limited)} "
                                 f"ressource(s) rate-limitée(s) relancée(s)"))
                time.sleep(SECOND_PASS_COOLDOWN)
                self._second_pass = True
                urls = [u for u, _ in self.rate_limited]
                self.rate_limited.clear()
                self._second_pass_urls.update(urls)
                for u in urls:
                    self._queue.append((u, 1, self.start_url))
        finally:
            if pool is not None:
                pool.shutdown(wait=False, cancel_futures=True)
            self._save_manifest()
        self._progress()
        print()

        elapsed = time.time() - t0

        self._write_placeholders()
        if self.menu:
            self._write_menu()
        if self.check:
            self._verify_integrity()
        if self.screenshot:
            self._take_screenshots()
        if self.zip_file:
            self._make_zip(self.zip_file)
        self._write_report(elapsed)
        self._summary(elapsed)

    def _process(self, url: str, depth: int, referer: str | None = None):
        """Télécharge et traite une URL (page ou asset)."""
        if depth > self.max_depth and is_html_url(url):
            self.stats["depth_cut"] += 1
            return

        if self.resume and url in self.pages \
                and (self.out_dir / self.pages[url]).exists():
            rel = self.pages[url]
            try:
                data = (self.out_dir / rel).read_bytes()
            except OSError:
                data = None
            if data is not None:
                self._process_page(data, url, depth, from_disk=True)
                self.stats["skipped"] += 1
                if self.verbose:
                    print(f"   skip : {rel} (déjà cloné)")
                return
        if self.resume and url in self.assets \
                and (self.out_dir / self.assets[url]).exists():
            self.stats["skipped"] += 1
            if self.verbose:
                print(f"   skip : {self.assets[url]} (déjà cloné)")
            return

        if self.respect_robots and not self._robots_allowed(url):
            self.stats["robot_excl"] += 1
            if self.verbose:
                print(f"   robots: {url} exclue (Disallow)")
            return

        r = fetch_once(self.session, url, self.max_size, self.throttle,
                       referer=referer, on_login=self._on_login)
        if url in self._second_pass_urls:
            self.stats["recovered"] += 1
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        self.total_bytes += len(r.content)

        is_html = ("html" in ctype
                   or ((not ctype) and is_html_url(url))
                   or (is_html_url(url) and looks_like_html(r.content)))

        if is_html and self.render and not self.raw:
            rendered = render_html(url, self.verbose)
            if rendered is not None:
                r = FetchResult(rendered,
                                {"Content-Type": ctype or "text/html"}, url)

        if is_html:
            if self.only_assets:
                self._scan_page_for_assets(r.content, url, depth)
            else:
                self._process_page(r.content, url, depth)
        else:
            if self.only_pages:
                self.stats["skipped"] += 1
                return
            self._process_asset(r.content, url)


    def _process_page(self, content: bytes, url: str, depth: int,
                      from_disk: bool = False):
        rel = self.pages.get(url) or self._local_path(url)
        self.pages[url] = rel
        self._register_path(rel, url)

        if self.raw:
            self._process_page_raw(content, url, depth, rel)
            return

        soup = _parse_markup(content)
        self.rewriter.rewrite_html(soup, url, depth)

        enc = soup.original_encoding or "utf-8"
        data = soup.encode(enc)
        self._save_bytes(data, rel)
        self.pages[url] = rel
        self.stats["pages"] += 1
        if self.verbose:
            print(f"   page : {url} -> {rel}")

    def _process_page_raw(self, content: bytes, url: str, depth: int, rel: str):
        """Mode brut : réécrit les attributs sans reformater le HTML."""
        text, enc = decode_any(content)
        text = self.rewriter.rewrite_page_raw(text, url, depth)
        self._save_bytes(text.encode(enc, errors="replace"), rel)
        self.pages[url] = rel
        self.stats["pages"] += 1
        if self.verbose:
            print(f"   page : {url} -> {rel} (brut)")


    def _process_asset(self, content: bytes, url: str):
        rel = self.assets.get(url) or self._local_path(url)

        if rel.endswith((".css", ".scss", ".less")):
            content = self.rewriter.rewrite_css(content, url)
        elif rel.endswith((".js", ".mjs")):
            self._queue_api_endpoints(content, url)
            content = self.rewriter.rewrite_js(content, url)

        self._save_bytes(content, rel)
        self.assets[url] = rel
        self._register_path(rel, url)
        self.stats["assets"] += 1
        if self.verbose:
            print(f"   asset: {url} -> {rel}")


    def _note_failure(self, url: str, reason):
        """Classe un échec : lien mort, ressource protégée (401/403), challenge
        Cloudflare (v2.5.0), limite de débit (429/503) ou échec dur. Les trois
        premiers ne sont PAS des échecs : on copie ce qu'on peut, le reste est
        compté à part."""
        text = str(reason)
        if "lien mort" in text:
            self.stats["dead"] += 1
            self.dead_urls.add(url)
            return
        if isinstance(reason, CloudflareChallengeError) \
                or "challenge cloudflare" in text.lower():
            if any(u == url for u, _ in self.protected):
                return
            self.protected.append((url, text))
            self.stats["protected"] += 1
            return
        if isinstance(reason, ProtectedError) or "protégée" in text:
            if any(u == url for u, _ in self.protected):
                return
            self.protected.append((url, text))
            self.stats["protected"] += 1
            return
        if isinstance(reason, RateLimitedError) or "limite de débit" in text:
            if any(u == url for u, _ in self.rate_limited):
                return
            self.rate_limited.append((url, text))
            self.stats["rate_limited"] += 1
            return
        self.failures.append((url, text))
        self.stats["failures"] += 1

    def _progress(self):
        done = self.stats["pages"] + self.stats["assets"]
        pending = len(self._queue)
        pct = min(100.0, 100.0 * self._done_count
                  / max(self._submitted + len(self._queue), 1))
        extra = f" | 429 : {self.stats['rate_limited']}" \
            if self.stats.get("rate_limited") else ""
        sys.stdout.write(f"\r[+] téléchargés : {done} ({pct:3.0f}%) "
                         f"| en attente : {pending} "
                         f"| échecs : {self.stats['failures']}{extra}   ")
        sys.stdout.flush()


    def _api_local_path(self, url: str) -> str:
        """Chemin local d'un endpoint API (JSON) découvert dans un script."""
        base = self._base_local_path(url)
        if base.lower().endswith((".html", ".htm")):
            base = os.path.splitext(base)[0] + ".json"
        return self._unique_rel(base, url)

    def _queue_api_endpoints(self, content: bytes, js_url: str):
        """v2.4.0 — planifie les endpoints API (fetch/XHR) d'un script JS.

        Ne retient que les chaînes statiques (extract_api_endpoints) ; les
        gabarits (``${``), backticks et concaténations sont ignorés par le
        filtre. L'endpoint est traité comme un asset JSON ordinaire.
        """
        if self.only_pages:
            return
        text = content.decode("utf-8", errors="replace")
        for ref in extract_api_endpoints(text):
            if not ref:
                continue
            u = self._abs(js_url, ref)
            if (u.lower().startswith(SKIP_SCHEMES) or not self._in_scope(u)
                    or u in self._seen):
                continue
            self._seen.add(u)
            rel = self._api_local_path(u)
            self.assets[u] = rel
            self._register_path(rel, u)
            self._queue.append((u, 1, js_url))
            if self.verbose:
                print(f"   api  : {u} -> {rel}")

    def _scan_page_for_assets(self, content: bytes, url: str, depth: int):
        """v2.4.0 — --only-assets : scanne une page sans l'enregistrer, pour
        en extraire les ressources (le rendu réécrit est jeté, la page reste
        un clone du site live)."""
        if self.raw:
            text, _enc = decode_any(content)
            self.rewriter.rewrite_page_raw(text, url, depth)
        else:
            soup = _parse_markup(content)
            self.rewriter.rewrite_html(soup, url, depth)
        self.stats["pages_scanned"] += 1


    def _verify_integrity(self):
        """Contrôle d'intégrité : tous les liens locaux du clone existent-ils ?
        (Toutes les URLs d'un srcset sont vérifiées, data: URI comprises.)
        Les références vers des ressources non téléchargées (rate-limitées
        429/503 ou protégées 401/403) sont comptées à part : on a choisi de
        ne pas les cloner, ce ne sont pas des liens cassés."""
        skip = {MANIFEST_NAME}
        if self.menu_file:
            skip.add(self.menu_file)
        if self.zip_file:
            skip.add(Path(self.zip_file).name)

        url_attrs = {"href", "src", "srcset", "imagesrcset", "action", "data",
                     "poster", "cite"} | set(LAZY_ATTRS)
        missing = []
        checked = 0
        refs = 0
        expected = 0

        expected_missing = {
            os.path.normpath(self._local_rel_for(url)).lower()
            for url, _ in self.rate_limited + self.protected
        }

        def resolve(page_path: Path, raw: str) -> Path | None:
            raw = raw.strip()
            if not raw or raw.lower().startswith(SKIP_SCHEMES) or raw.startswith("#"):
                return None
            if "#" in raw:
                raw = raw.split("#", 1)[0]
            if not raw:
                return None
            if raw.lower().startswith("http"):
                return None
            return (page_path.parent / urllib.parse.unquote(raw)).resolve()

        def check(tmp_page: Path, raw: str, origin: str):
            nonlocal checked, refs, expected
            refs += 1
            target = resolve(tmp_page, raw)
            if target is None:
                return
            checked += 1
            try:
                if not target.exists():
                    try:
                        rel = os.path.normpath(
                            str(target.relative_to(self.out_dir))).lower()
                    except ValueError:
                        rel = None
                    if rel is not None and rel in expected_missing:
                        expected += 1
                        return
                    missing.append((str(tmp_page.relative_to(self.out_dir)),
                                    origin, raw))
            except OSError:
                missing.append((str(tmp_page.relative_to(self.out_dir)),
                                origin, raw))

        for p in sorted(self.out_dir.rglob("*")):
            if not p.is_file() or p.name in skip or p.suffix.lower() == ".zip":
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if p.suffix.lower() in (".html", ".htm"):
                soup = _parse_markup(data)
                for node in soup.find_all(True):
                    for attr, val in node.attrs.items():
                        al = attr.lower()
                        if al not in url_attrs:
                            continue
                        if not val:
                            continue
                        if al == "srcset" or al == "imagesrcset" or "srcset" in al:
                            for u in srcset_urls(val if isinstance(val, str)
                                                 else str(val)):
                                check(p, u, f"{node.name}@{attr}")
                        else:
                            check(p, val if isinstance(val, str) else str(val),
                                  f"{node.name}@{attr}")
                    if node.name == "meta":
                        key = (node.get("property") or node.get("name") or "").lower()
                        if key in META_IMAGE_PROPS and node.get("content"):
                            check(p, node["content"], "meta@" + key)
                for meta in soup.find_all("meta",
                                          attrs={"http-equiv": re.compile("refresh", re.I)}):
                    m = re.search(r"url\s*=\s*(.+)$", (meta.get("content") or ""), re.I)
                    if m:
                        check(p, m.group(1), "meta refresh")
                for script in soup.find_all("script", src=False):
                    if script.string:
                        for m in re.finditer(
                                r"['\"]([^'\"]+\.(?:js|json|mjs|png|jpg|css))['\"]",
                                script.string or ""):
                            check(p, m.group(1), "script inline")
            elif p.suffix.lower() == ".css":
                txt, _ = decode_any(data)
                for m in CSS_URL_RE.finditer(txt):
                    check(p, m.group(2), "css url()")
                for m in re.finditer(r"@import\s+(?:url\(\s*)?(['\"]?)(.*?)\1\s*\)?\s*;?",
                                     txt, re.I):
                    check(p, m.group(2), "css @import")
            elif p.suffix.lower() in (".js", ".mjs"):
                txt, _ = decode_any(data)
                for m in re.finditer(
                        r"['\"]([^'\"]+\.(?:json|js|mjs|wasm|png|jpg|css|svg|woff2?))['\"]",
                        txt):
                    check(p, m.group(1), "js string")

        print(f"\n--- CONTRÔLE D'INTÉGRITÉ ---")
        if expected:
            print(f"  {expected} référence(s) vers des ressources non "
                  f"téléchargées (429/protégé) : ignorées, ce n'est pas cassé.")
        if missing:
            by_ext = Counter()
            for _page, _origin, ref in missing:
                ext = os.path.splitext(
                    urllib.parse.urlsplit(ref).path)[1].lower() \
                    or "(sans extension)"
                by_ext[ext] += 1
            print(f"  {len(missing)} référence(s) locale(s) cassée(s) sur {refs} "
                  f"({len(by_ext)} type(s)) :")
            for ext, count in by_ext.most_common(8):
                print(f"    - {count}x {ext}")
            if len(by_ext) > 8:
                print(f"    ... et {len(by_ext) - 8} autre(s) type(s)")
            for page, origin, ref in missing[:5]:
                print(f"    ex : {page} [{origin}] -> {ref}")
            if len(missing) > 5:
                print(f"    ... et {len(missing) - 5} autre(s) référence(s)")
        else:
            print(f"  OK : {checked} lien(s) local(aux) vérifié(s), tous résolus.")
        if self.dead_urls:
            print(f"  {len(self.dead_urls)} lien(s) mort(s) détecté(s) pendant le clone "
                  f"(non comptés comme échecs) :")
            for u in sorted(self.dead_urls)[:10]:
                print(f"    - {u}")
            if len(self.dead_urls) > 10:
                print(f"    ... et {len(self.dead_urls) - 10} autres")

    def _write_menu(self):
        import html as html_mod
        items = sorted(self.pages.items(), key=lambda kv: kv[1])
        rows = "\n".join(
            f'    <li><a href="{html_mod.escape(rel)}">{html_mod.escape(url)}</a></li>'
            for url, rel in items)
        page = (f"<!DOCTYPE html>\n<html lang=\"fr\"><head><meta charset=\"utf-8\"/>"
                f"<title>Pages clonées</title></head>\n<body><h1>Pages clonées ({len(items)})</h1>\n"
                f"<ul>\n{rows}\n</ul></body></html>\n")
        taken = set(self.pages.values()) | set(self.assets.values())
        name = "pages_menu.html"
        n = 2
        while name in taken:
            name = f"pages_menu_{n}.html"
            n += 1
        (self.out_dir / name).write_text(page, "utf-8")
        self.menu_file = name
        print(cs_ui.info(f"Menu des pages généré : {self.out_dir / name}"))

    def _make_zip(self, dest: str):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        skip = {self.out_dir / MANIFEST_NAME, dest.resolve()}
        if self.menu_file:
            skip.add(self.out_dir / self.menu_file)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(self.out_dir.rglob("*")):
                if p.is_file() and p.resolve() not in skip:
                    z.write(p, p.relative_to(self.out_dir).as_posix())
        self.zip_path = dest
        print(cs_ui.info(f"Archive créée : {dest}"))


    def _write_placeholders(self):
        """v2.4.0 — fichier de remplacement pour chaque ressource non
        téléchargée (429/503, 401/403) : le rendu du clone reste sain et le
        contrôle d'intégrité ne signale plus de référence manquante."""
        created = 0
        for url, _reason in self.rate_limited + self.protected:
            rel = self._local_rel_for(url)
            dest = self.out_dir / rel
            if dest.exists() and dest.stat().st_size > 0:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(self._placeholder_for(rel))
            created += 1
        if created:
            self.stats["placeholders"] += created
            print(cs_ui.info(f"{created} fichier(s) de remplacement créé(s) "
                             f"(ressources non téléchargées)"))

    def _placeholder_for(self, rel: str) -> bytes:
        """Contenu minimal du placeholder selon l'extension du fichier."""
        ext = os.path.splitext(rel)[1].lower()
        if ext == ".svg":
            return SVG_PLACEHOLDER.encode("utf-8")
        if ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
                   ".bmp", ".avif"):
            return PNG_PLACEHOLDER
        if ext in (".css", ".scss", ".less", ".js", ".mjs", ".map"):
            return b"/* spiderclone: ressource non telechargee (429/401/403) */\n"
        if ext in (".html", ".htm"):
            return (b"<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>"
                    b"</head><body></body></html>\n")
        return b""

    def _maybe_adaptive_backoff(self):
        """v2.4.0 — si le taux de rate-limit dépasse ADAPTIVE_RATIO, le délai
        inter-requêtes est augmenté (borné par ADAPTIVE_MIN/MAX_DELAY) avant
        la 2e passe, pour maximiser les chances de récupération."""
        done = self._done_count
        limited = self.stats["rate_limited"]
        if done < 10 or limited <= 0:
            return
        if limited / max(done, 1) <= ADAPTIVE_RATIO:
            return
        current = self.throttle.delay_for(self.base_host)
        new_delay = min(max(current * 2.0, ADAPTIVE_MIN_DELAY),
                        ADAPTIVE_MAX_DELAY)
        self.throttle.set_delay(self.base_host, new_delay)
        self.stats["adaptive_backoff"] += 1
        print(cs_ui.warn(f"Rate-limit fréquents ({limited}/{done}) : délai "
                         f"inter-requêtes passé à {new_delay:.1f}s "
                         f"avant la 2e passe"))

    def _write_report(self, elapsed: float):
        """v2.4.0 — rapport exportable (spiderclone_report.txt) : stats,
        ressources non téléchargées, échecs groupés par raison."""
        path = self.out_dir / REPORT_NAME
        lines = [
            f"SpiderClone {VERSION} — rapport de clone",
            "=" * 60,
            f"URL de départ : {self.start_url}",
            f"Dossier       : {self.out_dir}",
            f"Durée         : {elapsed:.1f} s",
            "-" * 60,
            "Statistiques :",
        ]
        for key in ("pages", "assets", "failures", "dead", "protected",
                    "rate_limited", "skipped", "robot_excl", "depth_cut",
                    "recovered", "placeholders", "screenshots"):
            if self.stats.get(key):
                lines.append(f"  {key:<14} : {self.stats[key]}")
        if self.rate_limited or self.protected:
            lines.append("-" * 60)
            lines.append("Ressources non téléchargées (max 100) :")
            for url, reason in (self.rate_limited + self.protected)[:100]:
                lines.append(f"  - {url}  ({reason})")
        if self.failures:
            lines.append("-" * 60)
            lines.append("Échecs par raison (max 10) :")
            by_reason = Counter(r for _u, r in self.failures)
            for reason, count in by_reason.most_common(10):
                lines.append(f"  - {count}x {reason}")
        path.write_text("\n".join(lines) + "\n", "utf-8")
        print(cs_ui.info(f"Rapport écrit : {path}"))

    def _take_screenshots(self):
        """v2.4.0 — captures d'écran des pages clonées (--screenshot), avec
        dégradation propre si Chromium/Playwright est indisponible."""
        if not self.pages:
            print(cs_ui.warn("Aucune page à capturer (--screenshot ignoré)"))
            return
        n = screenshot_pages(str(self.out_dir), dict(self.pages), self.verbose)
        self.stats["screenshots"] += n
        if n:
            print(cs_ui.info(f"{n} capture(s) d'écran écrites dans "
                             f"{self.out_dir / 'captures'}"))
        else:
            print(cs_ui.warn("Captures impossibles (Chromium/Playwright "
                             "indisponible) — sans impact sur le clone"))


    def _summary(self, elapsed: float):
        print("\n\n" + "=" * 60)
        print("RÉSUMÉ DU CLONE")
        print("=" * 60)
        print(f"  URL de départ  : {self.start_url}")
        print(f"  Dossier de sortie : {self.out_dir}")
        print(f"  Pages HTML     : {self.stats['pages']}")
        print(f"  Assets         : {self.stats['assets']}")
        print(f"  Échecs         : {self.stats['failures']}")
        print(f"  Liens morts    : {self.stats['dead']}")
        if self.stats.get("protected"):
            print(f"  Protégés (401/403) : {self.stats['protected']} (ignorés)")
        if self.stats.get("rate_limited"):
            print(f"  Rate-limités (429/503) : {self.stats['rate_limited']} "
                  f"(ignorés, on copie ce qu'on peut)")
        if self.stats.get("recovered"):
            print(f"  Récupérés 2e passe : {self.stats['recovered']}")
        if self.stats.get("placeholders"):
            print(f"  Placeholders : {self.stats['placeholders']} "
                  f"(fichiers de remplacement)")
        if self.stats.get("screenshots"):
            print(f"  Captures d'écran : {self.stats['screenshots']}")
        if self.stats.get("skipped"):
            print(f"  Déjà clonés    : {self.stats['skipped']}")
        if self.stats.get("robot_excl"):
            print(f"  Exclus robots  : {self.stats['robot_excl']}")
        if self.stats.get("depth_cut"):
            print(f"  Pages hors profondeur : {self.stats['depth_cut']}")
        print(f"  Taille totale   : {self.total_bytes / 1024:.0f} Ko")
        print(f"  Durée          : {elapsed:.1f} s")
        print("-" * 60)
        if self.failures:
            by_reason = Counter(reason for _url, reason in self.failures)
            print(f"  Échecs détaillés ({len(by_reason)} type(s)) :")
            for reason, count in by_reason.most_common(5):
                print(f"    - {count}x {reason}")
            if len(by_reason) > 5:
                print(f"    ... et {len(by_reason) - 5} autre(s) type(s)")
            if self.verbose:
                for url, reason in self.failures[:10]:
                    print(f"    - {url}  ({reason})")
        if self.saw_login:
            print("  " + cs_ui.warn("Le site a une page de connexion ou a redirigé "
                                    "vers un login :"))
            print("      le clone est probablement incomplet (contenu protégé).")
        print("=" * 60)
        print("  Pour ouvrir le clone : double-cliquez sur "
              + str(self.out_dir / "index.html"))
        if self.zip_file:
            print("  Archive : " + str(Path(self.zip_file)))
        print("  Manifest (reprise --resume) : " + str(self.manifest_path))
        if self.screenshot:
            print("  Captures : " + str(self.out_dir / "captures"))
        print("  Rapport : " + str(self.out_dir / REPORT_NAME))
        print("=" * 60)
