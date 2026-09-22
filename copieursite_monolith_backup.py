#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
copieursite — Clone complet d'un site web en local (v2).

Recopie récursivement un site (HTML, CSS, JavaScript, images, polices, médias,
documents...) en réécrivant tous les liens pour que le clone soit navigable
hors-ligne, de A à Z.

Fonctionnalités v2 :
  - Métadonnées sociales (og:image, twitter:image, apple-touch-icon, preload)
  - Images lazy-load (data-src, data-srcset, data-bg...) et style="url(...)"
  - Mode brut (--raw) : le HTML est préservé à l'octet près
  - Réécriture des URLs dans les fichiers JavaScript (fetch, import,
    Worker, XHR, importScripts, require, new URL(...))
  - Rendu JavaScript optionnel (--render, via Playwright)
  - Authentification : --cookies, --cookie-file (Netscape), --header
  - Détection de redirection vers une page de login
  - Contrôle d'intégrité intégré (--check / --no-check)
  - Reprise d'un clone interrompu (--resume) via manifest JSON
  - robots.txt (--respect-robots) et bootstrap sitemap (--sitemap)
  - Emballage : --zip, menu de pages (--menu), manifest JSON toujours écrit
  - Politesse réseau : --rate (req/s par domaine), plafonds --max-urls/--max-time
  - Liens morts (404/410) détectés et listés séparément
  - Fichier de configuration --config (JSON), --version

Usage:
    python copieursite.py https://exemple.fr [options]

Exemples :
    python copieursite.py https://exemple.fr
    python copieursite.py https://exemple.fr -o mon_clone --depth 5 --workers 16
    python copieursite.py https://exemple.fr --raw --zip clone.zip --menu
    python copieursite.py https://exemple.fr --sitemap --respect-robots --rate 5
    python copieursite.py https://exemple.fr --cookies "session=abc123"
    python copieursite.py https://exemple.fr --resume            # reprend un clone
"""

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

import requests
from bs4 import BeautifulSoup

VERSION = "2.0.0"
USER_AGENT = f"copieursite/{VERSION} (outil de clonage local)"
DEFAULT_TIMEOUT = 25
MAX_RETRIES = 3
DEFAULT_MAX_SIZE = 50 * 1024 * 1024  # 50 Mo par fichier
MANIFEST_NAME = "copieursite_manifest.json"

# ---------------------------------------------------------------------------
# Attributs HTML porteurs d'URL, par balise
# ---------------------------------------------------------------------------
HTML_ATTRS = {
    "a": ("href",),
    "area": ("href",),
    "link": ("href", "imagesrcset"),
    "script": ("src",),
    "img": ("src", "srcset"),
    "source": ("src", "srcset"),
    "video": ("src", "poster"),
    "audio": ("src",),
    "iframe": ("src",),
    "embed": ("src",),
    "object": ("data",),
    "track": ("src",),
    "input": ("src",),
    "form": ("action",),
    "blockquote": ("cite",),
    "q": ("cite",),
    "del": ("cite",),
    "ins": ("cite",),
}

# Attributs de lazy-loading courants (les valeurs sont des URLs)
LAZY_ATTRS = (
    "data-src", "data-original", "data-url", "data-lazy", "data-bg",
    "data-background", "data-image", "data-img", "data-href", "data-srcset",
    "data-original-src", "data-zoom-src", "data-thumb", "data-poster",
    "data-src-large", "data-full", "data-full-src", "data-src-small",
    "data-large", "data-original-uri", "data-fallback-src",
)

# Métadonnées sociales portant une image
META_IMAGE_PROPS = {
    "og:image", "og:image:url", "og:image:secure_url",
    "twitter:image", "twitter:image:src", "msapplication-tileimage",
}

# Extensions considérées comme des fichiers statiques (assets)
STATIC_EXT = {
    ".css", ".js", ".mjs", ".json", ".xml",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico", ".bmp", ".avif", ".jfif",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".mp4", ".webm", ".ogg", ".ogv", ".mp3", ".wav", ".m4a", ".aac", ".flac",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".rar", ".7z", ".tar", ".gz",
    ".txt", ".csv", ".md",
    ".map", ".webmanifest",
}

# Caractères interdits dans les noms de fichiers Windows
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
_CSS_IMPORT_RE = re.compile(r"@import\s+(?:url\(\s*)?(['\"]?)(.*?)\1\s*\)?\s*;?", re.IGNORECASE)
_CSS_SOURCEMAP_RE = re.compile(r"(sourceMappingURL\s*=\s*)([^\s\*]+)", re.IGNORECASE)

# Motifs d'URLs dans le JavaScript
_JS_PATTERNS = (
    re.compile(r"(fetch\s*\(\s*)(['\"])(.*?)\2", re.I | re.S),
    re.compile(r"(import\s*\(\s*)(['\"])(.*?)\2", re.I | re.S),
    re.compile(r"(new\s+URL\s*\(\s*)(['\"])(.*?)\2(?:\s*,\s*import\.meta\.url)?", re.I | re.S),
    re.compile(r"(new\s+Worker\s*\(\s*)(['\"])(.*?)\2", re.I | re.S),
    re.compile(r"(new\s+SharedWorker\s*\(\s*)(['\"])(.*?)\2", re.I | re.S),
    re.compile(r"(navigator\.serviceWorker\.register\s*\(\s*)(['\"])(.*?)\2", re.I | re.S),
    re.compile(r"(importScripts\s*\(\s*)(['\"])(.*?)\2", re.I),
    re.compile(r"(require\s*\(\s*)(['\"])(.*?)\2", re.I | re.S),
    re.compile(r"(\.open\s*\(\s*['\"](?:GET|POST|PUT|PATCH|DELETE|HEAD)['\"]\s*,\s*)(['\"])(.*?)\2", re.I),
    re.compile(r"(\bfrom\s*)(['\"])(.*?)\2", re.I | re.S),  # imports ES modules
)

# Mode brut : balises et attributs
_TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")
_ATTR_RE = re.compile(r"""(?P<name>[a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?P<quote>["'])(?P<val>.*?)(?P=quote)""", re.S)
_SCRIPT_BLOCK_RE = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.I | re.S)

_SKIP_SCHEMES = ("#", "data:", "javascript:", "mailto:", "tel:", "whatsapp:", "sms:", "about:", "blob:", "ws:", "wss:", "file:")
_LOGIN_PATH_RE = re.compile(r"(login|log-in|connexion|signin|sign-in|auth|account)", re.I)


def sanitize_filename(name: str) -> str:
    """Nettoie un nom de fichier pour qu'il soit valide partout (Windows inclus)."""
    name = urllib.parse.unquote(name)
    name = _FORBIDDEN.sub("_", name).strip(" .")
    if not name:
        name = "index"
    root, ext = os.path.splitext(name)
    if root.upper() in _RESERVED:
        root = "_" + root
    if len(root) > 120:
        root = root[:120]
    return root + ext


def is_static_url(url: str) -> bool:
    """Détermine si une URL pointe vraisemblablement vers un fichier statique."""
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext:
        return ext in STATIC_EXT
    return False


def is_html_url(url: str) -> bool:
    """Détermine si une URL pointe vraisemblablement vers une page HTML."""
    path = urllib.parse.urlparse(url).path
    ext = os.path.splitext(path)[1].lower()
    if ext:
        return ext in (".html", ".htm", ".shtml", ".xhtml", ".php", ".asp", ".aspx", ".jsp")
    return not ext


def looks_like_html(content: bytes) -> bool:
    """Détecte grossièrement si des octets ressemblent à du HTML (pour les URLs
    sans extension servies avec un type MIME ambigu, ex: /article ou /news)."""
    head = content[:2048].lstrip()
    return (head[:16].lower().startswith((b"<!doctype", b"<html", b"<head", b"<body", b"<title"))
            or b"<html" in head[:512].lower())


class SiteClone:
    """Clone un site web en local."""

    def __init__(self, start_url, out_dir, max_depth=3, workers=8,
                 include_subdomains=False, external=False, user_agent=USER_AGENT,
                 delay=0.0, max_size=DEFAULT_MAX_SIZE, verbose=False,
                 raw=False, render=False, cookies=None, cookie_file=None,
                 headers=None, check=True, resume=False, respect_robots=False,
                 sitemap=None, zip_file=None, menu=False, rate=0.0,
                 max_urls=0, max_time=0):
        self.start_url = self._normalize(start_url)
        parsed = urllib.parse.urlparse(self.start_url)
        if not parsed.scheme or not parsed.netloc:
            raise ValueError(f"URL invalide : {start_url}")
        self.base_host = (parsed.hostname or "").lower()
        self.base_scheme = parsed.scheme.lower() or "http"
        self.include_subdomains = include_subdomains
        self.external = external
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.max_depth = max_depth
        self.workers = max(1, workers)
        self.delay = delay
        self.max_size = max_size
        self.verbose = verbose
        self.raw = raw
        self.render = render
        self.check = check
        self.resume = resume
        self.respect_robots = respect_robots
        self.sitemap = sitemap
        self.zip_file = zip_file
        self.menu = menu
        self.rate = max(0.0, rate)
        self.max_urls = max(0, max_urls)
        self.max_time = max(0, max_time)
        self.manifest_path = self.out_dir / MANIFEST_NAME

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

        # URL normalisée -> (profondeur, destination locale, type)
        self.pages = {}   # url -> chemin local (pages HTML)
        self.assets = {}  # url -> chemin local (fichiers statiques)
        self._seen = set()            # URLs déjà planifiées
        self._queue = []              # file BFS (url, profondeur)
        self.failures = []            # (url, raison)
        self.dead_urls = set()        # liens morts (404/410)
        self.stats = Counter()
        self.total_bytes = 0
        self.saw_login = False
        self.robots_disallow = []
        self.robots_delay = None
        self._last_req = defaultdict(float)
        self._render_warned = False

        self._seed()
        if self.respect_robots:
            self._load_robots()
        if self.sitemap:
            self._load_sitemap()

    # ------------------------------------------------------------------ util

    def _normalize(self, url: str) -> str:
        """Normalise une URL (minuscules hôte, suppression fragment/port par défaut)."""
        p = urllib.parse.urlsplit(url)
        scheme = p.scheme.lower() or "http"
        host = p.hostname.lower() if p.hostname else ""
        port = p.port
        if (scheme == "http" and port == 80) or (scheme == "https" and port == 443):
            netloc = host
        else:
            netloc = p.netloc.lower()
        path = p.path or "/"
        if p.query:
            path += "?" + p.query
        return urllib.parse.urlunsplit((scheme, netloc, path, "", ""))

    def _abs(self, base: str, ref: str) -> str:
        """Résout une référence en URL absolue normalisée."""
        url = urllib.parse.urljoin(base, ref)
        return self._normalize(url)

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

    def _local_path(self, url: str) -> str:
        """Convertit une URL en chemin relatif local (sans le dossier de sortie)."""
        p = urllib.parse.urlsplit(url)
        path = urllib.parse.unquote(p.path)
        is_dir = path.endswith("/") or (not path and not p.query)
        has_query = bool(p.query)
        ext = os.path.splitext(path)[1].lower()
        no_ext = not ext

        parts = []
        if path in ("", "/"):
            name = "index.html"
        else:
            parts = [sanitize_filename(seg) for seg in path.strip("/").split("/")]
            if no_ext and not is_dir and not has_query:
                # URL sans extension servie comme page : /a-propos -> a-propos.html
                parts[-1] += ".html"
                no_ext = False
            if is_dir or no_ext:
                parts.append("index.html")
            else:
                if has_query or parts[-1].startswith("index."):
                    # garder l'extension, marquer la variante
                    stem, fext = os.path.splitext(parts[-1])
                    parts[-1] = stem + fext
            name = parts[-1]
            parts = parts[:-1]

            if has_query:
                # variante avec paramètres : on ajoute un hash court pour éviter les collisions
                h = hashlib.md5(url.encode("utf-8")).hexdigest()[:8]
                stem, fext = os.path.splitext(name)
                fext = fext or (".html" if no_ext or is_dir else "")
                name = f"{stem}__{h}{fext}"

        rel = "/".join(parts + [name]) if parts else name
        return rel

    def _local_rel_for(self, url: str) -> str:
        """Chemin local déjà connu (manifest, dictionnaires) ou calculé."""
        return self.pages.get(url) or self.assets.get(url) or self._local_path(url)

    # ------------------------------------------------------------------ seed

    def _seed(self):
        self._seen.add(self.start_url)
        self._queue.append((self.start_url, 0))

    # ------------------------------------------------------------------ auth

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

    # ------------------------------------------------------------------ robots / sitemap

    def _load_robots(self):
        url = f"{self.base_scheme}://{self.base_host}/robots.txt"
        try:
            r = self.session.get(url, timeout=DEFAULT_TIMEOUT)
            if r.status_code != 200:
                return
        except requests.RequestException:
            return
        collecting = False
        for raw in r.text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition(":")
            k, v = k.strip().lower(), v.strip()
            if k == "user-agent":
                low = v.lower()
                collecting = (low == "*" or "copieursite" in low or "python" in low)
            elif k == "disallow" and collecting and v:
                self.robots_disallow.append(v)
            elif k == "crawl-delay" and collecting:
                try:
                    self.robots_delay = float(v)
                except ValueError:
                    pass

    def _robots_allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        path = urllib.parse.urlsplit(url).path or "/"
        for rule in self.robots_disallow:
            pattern = "^" + re.escape(rule).replace("\\*", ".*")
            if re.match(pattern, path):
                return False
        return True

    def _load_sitemap(self):
        """Bootstrap : ajoute les URLs du sitemap à la file."""
        candidates = []
        if self.sitemap is True:
            candidates = [f"{self.base_scheme}://{self.base_host}/sitemap.xml",
                          f"{self.base_scheme}://{self.base_host}/sitemap_index.xml"]
        elif isinstance(self.sitemap, str):
            candidates = [self._abs(self.start_url, self.sitemap)]

        found = set()
        for cand in candidates:
            try:
                r = self.session.get(cand, timeout=DEFAULT_TIMEOUT)
            except requests.RequestException:
                continue
            if r.status_code != 200:
                continue
            for loc in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", r.text, re.I):
                u = self._normalize(loc.strip())
                if self._in_scope(u) and u not in self._seen:
                    found.add(u)
            break  # premier sitemap accessible

        count = 0
        for u in sorted(found):
            if not self._robots_allowed(u):
                continue
            self._seen.add(u)
            self._queue.append((u, 1))
            count += 1
        if count:
            print(f"[*] Sitemap : {count} URL(s) ajoutée(s) au crawl")

    # ------------------------------------------------------------------ politesse

    def _throttle(self, url: str):
        if self.rate <= 0:
            return
        host = urllib.parse.urlsplit(url).hostname
        now = time.time()
        interval = 1.0 / self.rate
        wait = interval - (now - self._last_req.get(host, 0.0))
        if wait > 0:
            time.sleep(wait)
        self._last_req[host] = time.time()

    def _limits_reached(self, elapsed: float) -> bool:
        if self.max_urls and len(self._seen) >= self.max_urls:
            return True
        if self.max_time and elapsed >= self.max_time:
            return True
        return False
    # ------------------------------------------------------------------ HTTP

    def _fetch(self, url: str):
        """Télécharge une URL avec tentatives, retourne la réponse ou lève."""
        self._throttle(url)
        last = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                r = self.session.get(url, timeout=DEFAULT_TIMEOUT,
                                     allow_redirects=True, stream=True)
                if r.status_code in (404, 410):
                    r.close()
                    raise DeadLinkError(f"HTTP {r.status_code} (lien mort)")
                if r.status_code >= 400:
                    r.close()
                    raise RuntimeError(f"HTTP {r.status_code}")
                length = int(r.headers.get("Content-Length", 0) or 0)
                if length > self.max_size:
                    r.close()
                    raise RuntimeError("fichier trop volumineux")
                r.close()
                r = self.session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True)
                if len(r.content) > self.max_size:
                    raise RuntimeError("fichier trop volumineux")
                # Détection de redirection vers un login (clone incomplet)
                final = r.url or url
                if _LOGIN_PATH_RE.search(urllib.parse.urlsplit(final).path):
                    self.saw_login = True
                self.total_bytes += len(r.content)
                return r
            except DeadLinkError:
                self.dead_urls.add(url)
                raise
            except Exception as e:  # noqa: BLE001
                last = e
                time.sleep(1.0 * attempt)
        raise RuntimeError(f"{last}")

    def _save_bytes(self, data: bytes, rel_path: str) -> Path:
        dest = self.out_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    # ------------------------------------------------------------------ manifest

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
        # Replanifie tout le manifest pour compléter/valider (sans re-télécharger
        # les fichiers déjà présents : voir _process).
        for url in self.pages:
            self._queue.append((url, 1))
        for url in self.assets:
            self._queue.append((url, 1))
        if self.start_url not in self.pages:
            self._seen.add(self.start_url)
            self._queue.append((self.start_url, 0))
        print(f"[*] Reprise : {len(self.pages)} page(s), {len(self.assets)} asset(s) connus")

    def _save_manifest(self):
        files = {}
        for url, rel in self.pages.items():
            files[url] = {"rel": rel, "type": "page"}
        for url, rel in self.assets.items():
            files[url] = {"rel": rel, "type": "asset"}
        payload = {
            "copieursite": VERSION,
            "start_url": self.start_url,
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            "files": files,
        }
        try:
            self.manifest_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), "utf-8")
        except OSError:
            pass

    # ------------------------------------------------------------------ crawl

    def run(self):
        print(f"[*] Clone de {self.start_url} -> {self.out_dir}")
        print(f"[*] Profondeur max : {self.max_depth} | ouvriers : {self.workers}\n")
        if self.resume:
            self._load_manifest()
        t0 = time.time()

        try:
            with cf.ThreadPoolExecutor(max_workers=self.workers,
                                       thread_name_prefix="clone") as pool:
                futures = {}
                while self._queue or futures:
                    cancelled = self._limits_reached(time.time() - t0)
                    while self._queue:
                        url, depth = self._queue.pop(0)
                        if cancelled:
                            continue
                        try:
                            if self.delay and depth > 0:
                                time.sleep(self.delay)
                            f = pool.submit(self._process, url, depth)
                            futures[f] = url
                        except Exception as e:  # noqa: BLE001
                            self._note_failure(url, str(e))
                        self._progress()

                    if cancelled and not futures:
                        break

                    # On attend qu'au moins une tâche se termine.
                    done, _ = cf.wait(futures, return_when=cf.FIRST_COMPLETED)
                    for f in done:
                        url = futures.pop(f)
                        try:
                            f.result()
                        except Exception as e:  # noqa: BLE001
                            self._note_failure(url, str(e))
                        self._progress()
        finally:
            self._save_manifest()

        elapsed = time.time() - t0

        if self.menu:
            self._write_menu()
        if self.check:
            self._verify_integrity()
        if self.zip_file:
            self._make_zip(self.zip_file)
        self._summary(elapsed)

    def _process(self, url: str, depth: int):
        """Télécharge et traite une URL (page ou asset)."""
        # Reprise : fichier déjà présent -> re-traitement local, pas de réseau.
        if self.resume and url in self.pages and (self.out_dir / self.pages[url]).exists():
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
        if self.resume and url in self.assets and (self.out_dir / self.assets[url]).exists():
            self.stats["skipped"] += 1
            if self.verbose:
                print(f"   skip : {self.assets[url]} (déjà cloné)")
            return

        if self.respect_robots and not self._robots_allowed(url):
            self.stats["robot_excl"] += 1
            if self.verbose:
                print(f"   robots: {url} exclue (Disallow)")
            return

        r = self._fetch(url)
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()

        if self.render and not self.raw:
            rendered = self._render_with_playwright(url)
            if rendered is not None:
                r = _Rendered(rendered if isinstance(rendered, bytes) else rendered.encode("utf-8"), ctype)

        is_html = ("html" in ctype
                   or ((not ctype) and is_html_url(url))
                   or (is_html_url(url) and looks_like_html(r.content)))

        if is_html:
            self._process_page(r.content, url, depth)
        else:
            self._process_asset(r.content, url)

    # ------------------------------------------------------------------ pages

    def _process_page(self, content: bytes, url: str, depth: int, from_disk: bool = False):
        rel = self._local_path(url)
        # On enregistre le chemin AVANT la réécriture des liens, afin que les
        # chemins relatifs soient calculés depuis le bon dossier.
        self.pages[url] = rel

        if self.raw and not from_disk:
            self._process_page_raw(content, url, depth, rel)
            return

        soup = BeautifulSoup(content, "html.parser")

        # Le <base> est supprimé : tous les liens sont résolus en absolu puis
        # réécrits en relatif local, il n'est donc plus utile (et dangereux).
        for tag in soup.find_all("base"):
            tag.decompose()

        # Détection d'un formulaire de connexion (clone probablement incomplet)
        if soup.find("input", {"type": "password"}):
            self.saw_login = True

        # Réécriture des attributs porteurs d'URL
        for tag, attrs in HTML_ATTRS.items():
            for node in soup.find_all(tag):
                for attr in attrs:
                    val = node.get(attr)
                    if not val:
                        continue
                    val = val.strip()
                    if "srcset" in attr.lower() or attr.lower() == "imagesrcset":
                        node[attr] = self._rewrite_srcset(val, url)
                    else:
                        node[attr] = self._rewrite_url(val, url, depth + 1)

        # Métadonnées sociales : images og:/twitter:/msapplication
        for meta in soup.find_all("meta"):
            key = (meta.get("property") or meta.get("name") or "").lower()
            itemprop = (meta.get("itemprop") or "").lower()
            if key in META_IMAGE_PROPS or itemprop in ("image", "thumbnailurl"):
                val = meta.get("content")
                if val:
                    meta["content"] = self._rewrite_url(val, url, depth + 1)

        # Images lazy-load (data-*) et style="url(...)" inline
        for node in soup.find_all(True):
            for attr in LAZY_ATTRS:
                val = node.get(attr)
                if not val:
                    continue
                if attr.endswith("srcset") or "srcset" in attr:
                    node[attr] = self._rewrite_srcset(val, url)
                else:
                    node[attr] = self._rewrite_url(val, url, depth + 1)
            st = node.get("style")
            if st and ("url(" in st.lower() or "@import" in st.lower()):
                node["style"] = self._rewrite_inline_style(st, url)

        # <meta http-equiv="refresh"> -> URL réécrite
        for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh", re.I)}):
            c = meta.get("content") or ""
            m = re.search(r"url\s*=\s*(.+)$", c, re.I)
            if m:
                new_url = self._queue_url(m.group(1).strip(), url, depth + 1)
                meta["content"] = f"{c[:m.start(1)]}{new_url}"

        # Scripts inline : réécriture des URLs JS (fetch, import...)
        for script in soup.find_all("script", src=False):
            body = script.string or ""
            if body and any(p.search(body) for p in _JS_PATTERNS):
                script.string = self._rewrite_js_text(body, url)

        # Encodage : on conserve celui détecté dans la page
        enc = soup.original_encoding or "utf-8"
        data = soup.encode(enc)

        self._save_bytes(data, rel)
        self.pages[url] = rel
        self.stats["pages"] += 1
        if self.verbose:
            print(f"   page : {url} -> {rel}")

    def _process_page_raw(self, content: bytes, url: str, depth: int, rel: str):
        """Mode brut : réécrit les attributs sans reformater le HTML."""
        text, enc = _decode_any(content)

        def repl_tag(m):
            tag = m.group(0)
            lower_tag = tag.lower()
            # <base> supprimé
            if lower_tag.startswith("<base"):
                return ""
            is_refresh = re.search(r"http-equiv\s*=\s*[\"']?\s*refresh", lower_tag) is not None

            def repl_attr(am):
                name = am.group("name").lower()
                quote, val = am.group("quote"), am.group("val")
                stripped = val.strip()
                if name in ("href", "src", "action", "data", "poster", "cite") \
                        and not stripped.lower().startswith(("data:", "javascript:", "mailto:", "tel:", "whatsapp:", "sms:", "about:", "blob:", "ws:", "wss:", "#", "file:")):
                    return f'{name}={quote}{self._rewrite_url(val, url, depth + 1)}{quote}'
                if name in ("srcset", "imagesrcset") or (name.startswith("data-") and "srcset" in name):
                    return f'{name}={quote}{self._rewrite_srcset(stripped, url)}{quote}'
                if name in LAZY_ATTRS:
                    return f'{name}={quote}{self._rewrite_url(val, url, depth + 1)}{quote}'
                if name == "style" and ("url(" in val.lower() or "@import" in val.lower()):
                    return f'{name}={quote}{self._rewrite_inline_style(val, url)}{quote}'
                if name == "content" and is_refresh:
                    mm = re.search(r"url\s*=\s*(.+)$", val, re.I)
                    if mm:
                        new_url = self._queue_url(mm.group(1).strip(), url, depth + 1)
                        return f'{name}={quote}{val[:mm.start(1)]}{new_url}{quote}'
                return am.group(0)

            return _ATTR_RE.sub(repl_attr, tag)

        text = _TAG_RE.sub(repl_tag, text)

        def repl_script(ms):
            attrs, body = ms.group("attrs"), ms.group("body")
            if re.search(r"\bsrc\s*=", attrs, re.I):
                return ms.group(0)
            if body and any(p.search(body) for p in _JS_PATTERNS):
                return f"<script{attrs}>{self._rewrite_js_text(body, url)}</script>"
            return ms.group(0)

        text = _SCRIPT_BLOCK_RE.sub(repl_script, text)

        self._save_bytes(text.encode(enc, errors="replace"), rel)
        self.pages[url] = rel
        self.stats["pages"] += 1
        if self.verbose:
            print(f"   page : {url} -> {rel} (brut)")
    # ------------------------------------------------------------------ liens

    def _queue_url(self, raw: str, base_url: str, depth: int) -> str:
        """Planifie le téléchargement d'une URL et renvoie sa future destination locale."""
        raw = raw.strip()
        if not raw or raw.lower().startswith(_SKIP_SCHEMES):
            return raw

        url = self._abs(base_url, raw)
        if not self._in_scope(url):
            return raw  # lien externe laissé tel quel

        # Nouvelle tâche ?
        if url not in self._seen:
            self._seen.add(url)
            self._queue.append((url, depth))

        # Chemin local cible
        try:
            if is_static_url(url):
                if url not in self.assets:
                    self.assets[url] = self._local_path(url)
                return self._rel_to(base_url, self.assets[url])
            else:
                if url not in self.pages:
                    self.pages[url] = self._local_path(url)
                return self._rel_to(base_url, self.pages[url])
        except Exception:  # noqa: BLE001
            return raw

    def _rewrite_url(self, val: str, base_url: str, depth: int) -> str:
        return self._queue_url(val, base_url, depth)

    def _rewrite_srcset(self, srcset: str, base_url: str) -> str:
        """Réécrit un attribut srcset en préservant les URI data: (qui contiennent des virgules)."""
        chunks = []
        buf = ""
        for piece in srcset.split(","):
            piece = piece.strip()
            if buf:
                # on poursuit une URI data: tant que le morceau ressemble à du base64
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
            new = self._queue_url(url, base_url, 1)
            out.append(new + ((" " + desc) if desc else ""))
        return ", ".join(out)

    def _rel_to(self, base_url: str, target_rel: str) -> str:
        """Chemin relatif depuis le dossier de la page vers la cible."""
        base_rel = self.pages.get(base_url, "index.html")
        base_dir = os.path.dirname(base_rel)
        rel = os.path.relpath(target_rel, base_dir)
        return rel.replace("\\", "/")

    def _rewrite_inline_style(self, css: str, base_url: str) -> str:
        """Réécrit les url(...) d'un attribut style (chemin relatif depuis la page)."""
        def repl(m):
            quote, inner = m.group(1), m.group(2).strip()
            if not inner or inner.lower().startswith(("data:", "blob:", "#")):
                return m.group(0)
            return f"url({quote}{self._rewrite_url(inner, base_url, 1)}{quote})"
        return _CSS_URL_RE.sub(repl, css)

    # ------------------------------------------------------------------ assets

    def _process_asset(self, content: bytes, url: str):
        rel = self._local_path(url)

        if rel.endswith((".css", ".scss", ".less")):
            content = self._rewrite_css(content, url)
        elif rel.endswith((".js", ".mjs")):
            content = self._rewrite_js(content, url)

        self._save_bytes(content, rel)
        self.assets[url] = rel
        self.stats["assets"] += 1
        if self.verbose:
            print(f"   asset: {url} -> {rel}")

    def _rewrite_css(self, content: bytes, css_url: str) -> bytes:
        text = content.decode("utf-8", errors="replace")
        target_rel = self.assets.get(css_url) or self._local_path(css_url)
        base_dir = os.path.dirname(target_rel)

        def queue_path(raw: str) -> str:
            """Télécharge et réécrit une référence CSS en chemin relatif local."""
            raw = raw.strip()
            if not raw or raw.lower().startswith(("data:", "blob:", "#")):
                return raw
            try:
                absu = self._abs(css_url, raw)
                if not self._in_scope(absu):
                    return raw
                if absu not in self.assets:
                    self.assets[absu] = self._local_path(absu)
                    if absu not in self._seen:
                        self._seen.add(absu)
                        self._queue.append((absu, 1))
                return os.path.relpath(self.assets[absu], base_dir).replace("\\", "/")
            except Exception:  # noqa: BLE001
                return raw

        def repl_url(m):
            quote, inner = m.group(1), m.group(2).strip()
            new = queue_path(inner)
            if new == inner:
                return m.group(0)
            return f"url({quote}{new}{quote})"

        def repl_import(m):
            quote, inner = m.group(1), m.group(2).strip()
            new = queue_path(inner)
            if new == inner:
                return m.group(0)
            return f"@import '{new}';"

        def repl_sourcemap(m):
            new = queue_path(m.group(2))
            if new == m.group(2):
                return m.group(0)
            return f"{m.group(1)}{new}"

        text = _CSS_URL_RE.sub(repl_url, text)
        text = _CSS_IMPORT_RE.sub(repl_import, text)
        text = _CSS_SOURCEMAP_RE.sub(repl_sourcemap, text)
        return text.encode("utf-8")

    def _rewrite_js(self, content: bytes, js_url: str) -> bytes:
        text = content.decode("utf-8", errors="replace")
        target_rel = self.assets.get(js_url) or self._local_path(js_url)
        base_dir = os.path.dirname(target_rel)
        return self._rewrite_js_text(text, js_url, base_dir).encode("utf-8")

    def _rewrite_js_text(self, text: str, base_url: str, base_dir: str | None = None) -> str:
        """Réécrit les URLs string dans du code JavaScript (fetch, import, Worker...)."""
        if base_dir is None:
            base_dir = os.path.dirname(self.pages.get(base_url, "index.html"))
        for pat in _JS_PATTERNS:
            def repl(m):
                inner = m.group(3).strip()
                if not inner or inner.lower().startswith(("data:", "blob:", "javascript:", "#")):
                    return m.group(0)
                try:
                    absu = self._abs(base_url, inner)
                    if not self._in_scope(absu):
                        return m.group(0)
                    if absu not in self.assets:
                        self.assets[absu] = self._local_path(absu)
                        if absu not in self._seen:
                            self._seen.add(absu)
                            self._queue.append((absu, 1))
                    rel = os.path.relpath(self.assets[absu], base_dir).replace("\\", "/")
                    return f"{m.group(1)}{m.group(2)}{rel}{m.group(2)}"
                except Exception:  # noqa: BLE001
                    return m.group(0)
            text = pat.sub(repl, text)
        return text

    # ------------------------------------------------------------------ divers

    def _note_failure(self, url: str, reason: str):
        if "lien mort" in reason:
            self.stats["dead"] += 1
            self.dead_urls.add(url)
            return
        self.failures.append((url, reason))
        self.stats["failures"] += 1

    def _progress(self):
        done = self.stats["pages"] + self.stats["assets"]
        pending = len(self._queue)
        sys.stdout.write(f"\r[+] téléchargés : {done} | en attente : {pending} | échecs : {self.stats['failures']}   ")
        sys.stdout.flush()

    # ------------------------------------------------------------------ vérifications

    def _verify_integrity(self):
        """Contrôle d'intégrité : tous les liens locaux du clone existent-ils ?"""
        skip = {MANIFEST_NAME}
        if getattr(self, "menu_file", None):
            skip.add(self.menu_file)
        if self.zip_file:
            skip.add(Path(self.zip_file).name)

        url_attrs = {"href", "src", "srcset", "imagesrcset", "action", "data",
                     "poster", "cite"} | set(LAZY_ATTRS)
        missing = []
        checked = 0
        refs = 0

        def resolve(page_path: Path, raw: str) -> Path | None:
            raw = raw.strip()
            if not raw or raw.lower().startswith(_SKIP_SCHEMES) or raw.startswith("#"):
                return None
            if "#" in raw:
                raw = raw.split("#", 1)[0]
            if not raw:
                return None
            if raw.lower().startswith("http"):  # lien externe conservé
                return None
            return (page_path.parent / urllib.parse.unquote(raw)).resolve()

        def check(tmp_page: Path, raw: str, origin: str):
            nonlocal checked, refs
            refs += 1
            target = resolve(tmp_page, raw)
            if target is None:
                return
            checked += 1
            try:
                if not target.exists():
                    missing.append((str(tmp_page.relative_to(self.out_dir)), origin, raw))
            except OSError:
                missing.append((str(tmp_page.relative_to(self.out_dir)), origin, raw))

        for p in sorted(self.out_dir.rglob("*")):
            if not p.is_file() or p.name in skip or p.suffix.lower() == ".zip":
                continue
            try:
                data = p.read_bytes()
            except OSError:
                continue
            if p.suffix.lower() in (".html", ".htm"):
                soup = BeautifulSoup(data, "html.parser")
                for node in soup.find_all(True):
                    for attr, val in node.attrs.items():
                        al = attr.lower()
                        if al in url_attrs:
                            if al == "srcset" or al == "imagesrcset" or "srcset" in al:
                                first = val.strip().split(",")[0].split()[0] if val else ""
                                if first:
                                    check(p, first, f"{node.name}@{attr}")
                            else:
                                if val:
                                    check(p, val if isinstance(val, str) else str(val), f"{node.name}@{attr}")
                        elif al == "style" and isinstance(val, str) and "url(" in val.lower():
                            for m in _CSS_URL_RE.finditer(val):
                                check(p, m.group(2), f"{node.name}@style")
                    if node.name == "meta":
                        key = (node.get("property") or node.get("name") or "").lower()
                        if key in META_IMAGE_PROPS and node.get("content"):
                            check(p, node["content"], "meta@" + key)
                for meta in soup.find_all("meta", attrs={"http-equiv": re.compile("refresh", re.I)}):
                    m = re.search(r"url\s*=\s*(.+)$", (meta.get("content") or ""), re.I)
                    if m:
                        check(p, m.group(1), "meta refresh")
                for script in soup.find_all("script", src=False):
                    if script.string:
                        for m in re.finditer(r"['\"]([^'\"]+\.(?:js|json|mjs|png|jpg|css))['\"]", script.string or ""):
                            check(p, m.group(1), "script inline")
            elif p.suffix.lower() == ".css":
                txt = data.decode("utf-8", "replace")
                for m in _CSS_URL_RE.finditer(txt):
                    check(p, m.group(2), "css url()")
                for m in _CSS_IMPORT_RE.finditer(txt):
                    check(p, m.group(2), "css @import")
            elif p.suffix.lower() in (".js", ".mjs"):
                txt = data.decode("utf-8", "replace")
                for m in re.finditer(r"['\"]([^'\"]+\.(?:json|js|mjs|wasm|png|jpg|css|svg|woff2?))['\"]", txt):
                    check(p, m.group(1), "js string")

        print(f"\n--- CONTRÔLE D'INTÉGRITÉ ---")
        if missing:
            print(f"  {len(missing)} référence(s) locale(s) cassée(s) sur {refs} :")
            for page, origin, ref in missing[:20]:
                print(f"    - {page} [{origin}] -> {ref}")
            if len(missing) > 20:
                print(f"    ... et {len(missing) - 20} autres")
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
        print(f"[*] Menu des pages généré : {self.out_dir / name}")

    def _make_zip(self, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        skip = {self.out_dir / MANIFEST_NAME, dest.resolve()}
        if getattr(self, "menu_file", None):
            skip.add(self.out_dir / self.menu_file)
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(self.out_dir.rglob("*")):
                if p.is_file() and p.resolve() not in skip:
                    z.write(p, p.relative_to(self.out_dir).as_posix())
        self.zip_path = dest
        print(f"[*] Archive créée : {dest}")

    # ------------------------------------------------------------------ rendu JS

    def _render_with_playwright(self, url: str):
        """Rendu de la page avec un navigateur headless (Playwright) si disponible."""
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            if not self._render_warned:
                print("[!] --render nécessite Playwright : "
                      "pip install playwright && playwright install chromium")
                self._render_warned = True
            return None
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html_dom = page.content()
                browser.close()
            return html_dom.encode("utf-8")
        except Exception as e:  # noqa: BLE001
            if self.verbose:
                print(f"   render: {url} impossible ({e})")
            return None

    # ------------------------------------------------------------------ résumé

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
        if self.stats.get("skipped"):
            print(f"  Déjà clonés    : {self.stats['skipped']}")
        if self.stats.get("robot_excl"):
            print(f"  Exclus robots  : {self.stats['robot_excl']}")
        print(f"  Taille totale   : {self.total_bytes / 1024:.0f} Ko")
        print(f"  Durée          : {elapsed:.1f} s")
        print("-" * 60)
        if self.failures:
            print("  Échecs détaillés :")
            for url, reason in self.failures[:20]:
                print(f"    - {url}  ({reason})")
            if len(self.failures) > 20:
                print(f"    ... et {len(self.failures) - 20} autres")
        if self.saw_login:
            print("  [!] Le site a une page de connexion ou a redirigé vers un login :")
            print("      le clone est probablement incomplet (contenu protégé).")
        print("=" * 60)
        print("  Pour ouvrir le clone : double-cliquez sur "
              + str(self.out_dir / "index.html"))
        if self.zip_file:
            print("  Archive : " + str(Path(self.zip_file)))
        print("  Manifest (reprise --resume) : " + str(self.manifest_path))
        print("=" * 60)


class DeadLinkError(RuntimeError):
    """Lien mort (HTTP 404/410) : signalé mais non retenté."""


class _Rendered:
    """Réponse simulée pour une page rendue par navigateur headless."""
    def __init__(self, content: bytes, ctype: str):
        self.content = content
        self.headers = {"Content-Type": ctype or "text/html"}


def _decode_any(content: bytes):
    """Décode en UTF-8 si possible, sinon latin-1 (ne perd jamais d'octets)."""
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    return content.decode("latin-1"), "latin-1"


def main():
    # Console : affichage correct des accents
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    DEFAULTS = dict(
        output=None, depth=3, workers=8, include_subdomains=False, external=False,
        delay=0.0, max_size=DEFAULT_MAX_SIZE, user_agent=USER_AGENT, verbose=False,
        raw=False, render=False, cookies=None, cookie_file=None, header=None,
        zip=None, menu=False, check=True, resume=False, respect_robots=False,
        sitemap=None, rate=0.0, max_urls=0, max_time=0,
    )

    parser = argparse.ArgumentParser(
        prog="copieursite",
        description="Clone complet d'un site web en local (HTML, CSS, JS, images, polices...).",
        epilog="Exemples : python copieursite.py https://exemple.fr "
               "| --raw --zip clone.zip | --sitemap --respect-robots | --resume",
    )
    parser.add_argument("url", help="URL de départ du site à cloner (ex: https://exemple.fr)")
    parser.add_argument("-o", "--output", default=None,
                        help="Dossier de destination (défaut : nom du domaine)")
    parser.add_argument("-d", "--depth", type=int, default=None,
                        help="Profondeur maximale de navigation (défaut : 3)")
    parser.add_argument("-w", "--workers", type=int, default=None,
                        help="Nombre de téléchargements parallèles (défaut : 8)")
    parser.add_argument("--include-subdomains", action="store_true", default=None,
                        help="Inclure les sous-domaines du site")
    parser.add_argument("--external", action="store_true", default=None,
                        help="Suivre aussi les liens vers d'autres domaines (CDN, etc.)")
    parser.add_argument("--delay", type=float, default=None,
                        help="Délai fixe (s) avant chaque page (politesse)")
    parser.add_argument("--rate", type=float, default=None,
                        help="Limite de requêtes par seconde et par domaine")
    parser.add_argument("--max-size", type=int, default=None,
                        help="Taille maximale d'un fichier en octets (défaut : 50 Mo)")
    parser.add_argument("--max-urls", type=int, default=None,
                        help="Nombre maximal d'URLs à traiter (plafond)")
    parser.add_argument("--max-time", type=int, default=None,
                        help="Durée maximale du clone en secondes (plafond)")
    parser.add_argument("--user-agent", default=None, help="User-Agent HTTP")
    parser.add_argument("--cookies", default=None,
                        help="Cookies à envoyer, format 'nom=valeur; nom2=valeur2'")
    parser.add_argument("--cookie-file", default=None,
                        help="Fichier de cookies au format Netscape")
    parser.add_argument("--header", action="append", default=None, metavar="NAME: VALEUR",
                        help="En-tête HTTP supplémentaire (répétable)")
    parser.add_argument("--raw", action="store_true", default=None,
                        help="Mode brut : HTML préservé à l'octet près (regex)")
    parser.add_argument("--render", action="store_true", default=None,
                        help="Rendu JavaScript via Playwright (nécessite l'installation)")
    parser.add_argument("--check", dest="check", action="store_true", default=None,
                        help="Contrôle d'intégrité final (activé par défaut)")
    parser.add_argument("--no-check", dest="check", action="store_false",
                        help="Désactiver le contrôle d'intégrité final")
    parser.add_argument("--resume", action="store_true", default=None,
                        help="Reprendre un clone interrompu (via le manifest)")
    parser.add_argument("--respect-robots", action="store_true", default=None,
                        help="Respecter robots.txt (Disallow + Crawl-delay)")
    parser.add_argument("--sitemap", nargs="?", const=True, default=None,
                        help="Bootstrap via sitemap (auto sitemap.xml ou URL)")
    parser.add_argument("--zip", default=None, metavar="FICHIER.zip",
                        help="Emballer le clone dans une archive zip")
    parser.add_argument("--menu", action="store_true", default=None,
                        help="Générer une page menu listant toutes les pages clonées")
    parser.add_argument("--config", default=None, metavar="FICHIER.json",
                        help="Fichier de configuration JSON (valeurs par défaut)")
    parser.add_argument("-v", "--verbose", action="store_true", default=None,
                        help="Afficher chaque fichier téléchargé")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args()

    cfg = {}
    if args.config:
        try:
            cfg = json.loads(Path(args.config).read_text("utf-8"))
        except (OSError, ValueError) as e:
            print(f"[!] Fichier de configuration illisible : {e}", file=sys.stderr)
            sys.exit(2)

    final = dict(DEFAULTS)
    final.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    final.update({k: v for k, v in vars(args).items()
                  if v is not None and k not in ("config",)})

    # Coercition des valeurs numériques (JSON -> types Python)
    for k in ("depth", "workers", "max_size", "max_urls", "max_time"):
        final[k] = int(final[k])
    for k in ("delay", "rate"):
        final[k] = float(final[k])

    output = final["output"]
    if not output:
        host = urllib.parse.urlparse(args.url).netloc.replace(":", "_")
        output = re.sub(r"[^A-Za-z0-9_.-]", "_", host)

    try:
        cloner = SiteClone(
            args.url, output, max_depth=final["depth"], workers=final["workers"],
            include_subdomains=final["include_subdomains"], external=final["external"],
            user_agent=final["user_agent"], delay=final["delay"],
            max_size=final["max_size"], verbose=final["verbose"],
            raw=final["raw"], render=final["render"],
            cookies=final["cookies"], cookie_file=final["cookie_file"],
            headers=final["header"], check=final["check"], resume=final["resume"],
            respect_robots=final["respect_robots"], sitemap=final["sitemap"],
            zip_file=final["zip"], menu=final["menu"],
            rate=final["rate"], max_urls=final["max_urls"], max_time=final["max_time"],
        )
        cloner.run()
    except KeyboardInterrupt:
        print("\n[!] Interrompu par l'utilisateur. Le manifest permet de reprendre "
              "avec --resume.")
        sys.exit(130)
    except ValueError as e:
        print(f"[!] {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
