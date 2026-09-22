#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Rendu JavaScript via Playwright (Chromium headless reutilise) pour --render et --screenshot.
"""

import threading

_RENDER_LOCK = threading.Lock()
_SCREENSHOT_LOCK = threading.Lock()


def render_html(url: str, verbose: bool = False) -> bytes | None:
    """Rend une URL avec Chromium headless et renvoie le HTML final.

    Retourne None si Playwright n'est pas installé ou si le rendu échoue.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        if verbose:
            print("[!] --render nécessite Playwright : "
                  "pip install playwright && playwright install chromium")
        return None
    try:
        with _RENDER_LOCK, sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                html_dom = page.content()
            finally:
                browser.close()
        return html_dom.encode("utf-8")
    except Exception as e:
        if verbose:
            print(f"   render: {url} impossible ({e})")
        return None


def screenshot_pages(out_dir, pages, verbose: bool = False,
                     captures_dir: str = "captures") -> int:
    """Capture d'écran des pages clonées (option --screenshot, v2.4.0).

    Rend chaque page locale via Chromium headless réutilisé (une seule
    instance pour tout le lot) et enregistre des PNG dans
    ``out_dir/captures_dir``. Retourne le nombre de captures réussies, ou 0
    si Playwright n'est pas installé (dégradation propre : jamais bloquant).
    """
    if not pages:
        return 0
    try:
        import pathlib
        from playwright.sync_api import sync_playwright
    except ImportError:
        if verbose:
            print("[!] --screenshot nécessite Playwright : "
                  "pip install playwright && playwright install chromium")
        return 0
    import os

    captures_dir = os.path.join(out_dir, captures_dir)
    try:
        os.makedirs(captures_dir, exist_ok=True)
    except OSError:
        return 0
    ok = 0
    try:
        with _SCREENSHOT_LOCK, sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                for url, rel in list(pages.items()):
                    local = os.path.join(out_dir, rel)
                    if not os.path.isfile(local):
                        continue
                    name = (os.path.splitext(rel)[0]
                            .replace("\\", "_").replace("/", "_"))
                    target = os.path.join(captures_dir, name + ".png")
                    page = browser.new_page(
                        viewport={"width": 1280, "height": 900})
                    try:
                        page.goto(pathlib.Path(local).resolve().as_uri(),
                                  wait_until="load", timeout=15000)
                        page.screenshot(path=target, full_page=True)
                        ok += 1
                    finally:
                        page.close()
            finally:
                browser.close()
    except Exception as e:
        if verbose:
            print(f"   screenshot: échec ({e})")
    return ok
