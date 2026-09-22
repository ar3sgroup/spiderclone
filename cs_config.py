#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Constantes, regex et tables de reecriture de SpiderClone (attributs porteurs d'URL, extensions statiques, motifs JS/CSS).
"""

import re

VERSION = "2.5.0"
USER_AGENT = f"spiderclone/{VERSION} (outil de clonage local)"
DEFAULT_TIMEOUT = 25
MAX_RETRIES = 3
DEFAULT_MAX_SIZE = 50 * 1024 * 1024
MANIFEST_NAME = "spiderclone_manifest.json"
REPORT_NAME = "spiderclone_report.txt"

SECOND_PASS_COOLDOWN = 5.0

ADAPTIVE_RATIO = 0.30
ADAPTIVE_MIN_DELAY = 1.0
ADAPTIVE_MAX_DELAY = 10.0

DEFAULT_PORT = 8000
TUNNEL_CHOICES = ("none", "ngrok", "cloudflare", "cloudflare-named")

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

LAZY_ATTRS = (
    "data-src", "data-original", "data-url", "data-lazy", "data-bg",
    "data-background", "data-image", "data-img", "data-href", "data-srcset",
    "data-original-src", "data-zoom-src", "data-thumb", "data-poster",
    "data-src-large", "data-full", "data-full-src", "data-src-small",
    "data-large", "data-original-uri", "data-fallback-src",
)

META_IMAGE_PROPS = {
    "og:image", "og:image:url", "og:image:secure_url",
    "twitter:image", "twitter:image:src", "msapplication-tileimage",
}

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

FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(
    r"@import\s+(?:url\(\s*)?(['\"])(.*?)\1\s*\)?\s*([^;]*);?",
    re.IGNORECASE)
CSS_SOURCEMAP_RE = re.compile(r"(sourceMappingURL\s*=\s*)([^\s\*]+)", re.IGNORECASE)

JS_PATTERNS = (
    (re.compile(r"(fetch\s*\(\s*)(['\"])(.*?)\2", re.I | re.S), True),
    (re.compile(r"(import\s*\(\s*)(['\"])(.*?)\2", re.I | re.S), True),
    (re.compile(r"(new\s+URL\s*\(\s*)(['\"])(.*?)\2(\s*,\s*[^)]*)?", re.I | re.S), True),
    (re.compile(r"(new\s+Worker\s*\(\s*)(['\"])(.*?)\2", re.I | re.S), True),
    (re.compile(r"(new\s+SharedWorker\s*\(\s*)(['\"])(.*?)\2", re.I | re.S), True),
    (re.compile(r"(navigator\.serviceWorker\.register\s*\(\s*)(['\"])(.*?)\2", re.I | re.S), True),
    (re.compile(r"(importScripts\s*\(\s*)(['\"])(.*?)\2", re.I), True),
    (re.compile(r"(require\s*\(\s*)(['\"])(.*?)\2", re.I | re.S), False),
    (re.compile(r"(\.open\s*\(\s*['\"](?:GET|POST|PUT|PATCH|DELETE|HEAD)['\"]\s*,\s*)(['\"])(.*?)\2", re.I), True),
    (re.compile(
        r"(\b(?:import|export)\s+(?:type\s+)?"
        r"(?:\*|\w+|\{[^{}]*\})(?:\s+as\s+\w+)?"
        r"(?:\s*,\s*(?:\*|\w+|\{[^{}]*\})(?:\s+as\s+\w+)?)*"
        r"\s*from\s*)(['\"])(.*?)\2", re.I | re.S), False),
)

TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")
ATTR_RE = re.compile(
    r"""(?P<name>[a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(?P<quote>["'])(?P<val>.*?)(?P=quote)""",
    re.S)
SCRIPT_BLOCK_RE = re.compile(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", re.I | re.S)

SKIP_SCHEMES = ("#", "data:", "javascript:", "mailto:", "tel:", "whatsapp:",
                "sms:", "about:", "blob:", "ws:", "wss:", "file:")
LOGIN_PATH_RE = re.compile(r"(login|log-in|connexion|signin|sign-in|auth)", re.I)
