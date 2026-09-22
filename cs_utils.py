#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Helpers purs : noms de fichiers surs, classification des URLs, decodage d'octets, extraction d'endpoints API.
"""

import os
import re
import urllib.parse

from cs_config import FORBIDDEN, RESERVED, STATIC_EXT


def sanitize_filename(name: str) -> str:
    """Nettoie un nom de fichier pour qu'il soit valide partout (Windows inclus).

    Les noms commençant par un point (.htaccess, .env) sont préservés :
    seul l'espace de tête est retiré, pas les points.
    """
    name = urllib.parse.unquote(name)
    name = name.lstrip(" ")
    name = FORBIDDEN.sub("_", name)
    name = name.rstrip(" .")
    if not name:
        name = "index"
    root, ext = os.path.splitext(name)
    if root.upper() in RESERVED:
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


def srcset_urls(srcset: str):
    """Extrait toutes les URLs d'un srcset.

    Les URI data: contiennent des virgules (base64) ; elles sont reconstituées
    avant la découpe. (L'ancien contrôle d'intégrité ne vérifiait que la
    première URL d'un srcset.)
    """
    urls = []
    pending = None
    for piece in srcset.split(","):
        piece = piece.strip()
        if pending is not None:
            head = piece.split(None, 1)[0] if piece else ""
            if re_fullmatch_base64(head):
                pending += "," + piece
                continue
            urls.append(pending)
            pending = None
        if piece.startswith("data:"):
            pending = piece
        elif piece:
            urls.append(piece.split()[0])
    if pending is not None:
        urls.append(pending)
    return urls


def re_fullmatch_base64(piece: str) -> bool:
    return all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
               for c in piece) and bool(piece)


def decode_any(content: bytes):
    """Décode en UTF-8 si possible, sinon latin-1 (ne perd jamais d'octets)."""
    try:
        return content.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    return content.decode("latin-1"), "latin-1"


API_ENDPOINT_RE = re.compile(
    r"""fetch\s*\(\s*(?P<fq>['"])(?P<fetch_url>.*?)(?P=fq)(?P<fetch_tail>[^,)\s;]*)"""
    r"""|\.open\s*\(\s*['"](?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)['"]\s*,\s*(?P<xq>['"])(?P<xhr_url>.*?)(?P=xq)(?P<xhr_tail>[^)\s;]*)""",
    re.I | re.S)


def extract_api_endpoints(text: str):
    """Extrait les endpoints API littéraux d'un code JS, dans l'ordre du fichier.

    Ne conserve que les chaînes statiques : gabarits (``${``), backticks,
    concaténations (``"…"+id``) et schémas non-HTTP sont ignorés ; chaque
    endpoint n'apparaît qu'une fois (la 1re occurrence gagne).
    """
    seen = set()
    out = []
    for m in API_ENDPOINT_RE.finditer(text):
        url = (m.group("fetch_url") or m.group("xhr_url") or "").strip()
        tail = m.group("fetch_tail") or m.group("xhr_tail") or ""
        if not url or "${" in url:
            continue
        if tail.lstrip().startswith(("+", ".")):
            continue
        if url.lower().startswith(("data:", "blob:", "javascript:", "#", "//")):
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out
