#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Tests unitaires de SpiderClone : correctifs v2.1.0+ et publication v2.5.0 (serveur, tunnels, repli local).
"""

import io
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import warnings
import shutil
import requests
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cs_config import (CSS_IMPORT_RE, DEFAULT_PORT, LOGIN_PATH_RE,
                      REPORT_NAME,
                      VERSION)
from cs_crawl import (PNG_PLACEHOLDER, SVG_PLACEHOLDER,
                      SiteClone)
import cs_ui
from spiderclone import build_parser
from cs_fetch import (CloudflareChallengeError, DeadLinkError, FetchResult,
                      ProtectedError, RateLimitedError, Throttle, fetch_once,
                      is_cloudflare_challenge, parse_robots, pick_robots_group,
                      robots_path_allowed)
from cs_serve import (TunnelError, build_cloudflared_named_args,
                      extract_cloudflared_url, extract_ngrok_url,
                      find_free_port, publish, serve_directory,
                      start_cloudflared, start_cloudflared_named,
                      start_ngrok, stop)
from cs_utils import (decode_any, is_html_url,
                      is_static_url, looks_like_html, sanitize_filename,
                      srcset_urls)

SITE = "https://example.com/"
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_cloner(max_depth=3, **kw):
    tmp = tempfile.mkdtemp(prefix="spiderclone_test_")
    return SiteClone(SITE, tmp, max_depth=max_depth, workers=2, **kw)


class TestSanitize(unittest.TestCase):
    def test_htaccess_preserved(self):
        self.assertEqual(sanitize_filename(".htaccess"), ".htaccess")
        self.assertEqual(sanitize_filename("  .env"), ".env")

    def test_forbidden_chars(self):
        self.assertNotIn("<", sanitize_filename("a<b:c"))
        self.assertNotIn("*", sanitize_filename("x*y"))

    def test_reserved_names(self):
        self.assertEqual(sanitize_filename("CON.txt"), "_CON.txt")
        self.assertEqual(sanitize_filename("NUL"), "_NUL")

    def test_trailing_dots_spaces(self):
        self.assertEqual(sanitize_filename("nom. "), "nom")
        self.assertEqual(sanitize_filename("page 1.html"), "page 1.html")


class TestClassify(unittest.TestCase):
    def test_html(self):
        self.assertTrue(is_html_url("https://x/"))
        self.assertTrue(is_html_url("https://x/article"))
        self.assertTrue(is_html_url("https://x/page.php"))
        self.assertFalse(is_html_url("https://x/logo.png"))

    def test_static(self):
        self.assertTrue(is_static_url("https://x/a.css"))
        self.assertTrue(is_static_url("https://x/a.js?v=1"))
        self.assertFalse(is_static_url("https://x/about"))

    def test_looks_like_html(self):
        self.assertTrue(looks_like_html(b"<!DOCTYPE html><html>"))
        self.assertTrue(looks_like_html(b"<html><head></head></html>"))
        self.assertFalse(looks_like_html(b"\x89PNG\r\n\x1a\n data"))


class TestSrcset(unittest.TestCase):
    def test_multiple_urls(self):
        self.assertEqual(srcset_urls("a.jpg 1x, b.jpg 2x"),
                         ["a.jpg", "b.jpg"])

    def test_data_uri_kept(self):
        data = ("data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYA"
                "AAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg== 1x")
        out = srcset_urls(f"{data}, pic.jpg 2x")
        self.assertEqual(len(out), 2)
        self.assertTrue(out[0].startswith("data:image/png;base64,"))
        self.assertEqual(out[1], "pic.jpg")

    def test_no_descriptors(self):
        self.assertEqual(srcset_urls("x.png, y.png"), ["x.png", "y.png"])


class TestCss(unittest.TestCase):
    def test_import_media_reemitted(self):
        css = '@import "theme.css" screen and (min-width: 600px);'
        m = CSS_IMPORT_RE.search(css)
        self.assertIsNotNone(m)
        self.assertIn("screen", m.group(3))

        cloner = make_cloner()
        out = cloner.rewriter.rewrite_css(css.encode("utf-8"),
                                          "https://example.com/style.css")
        txt = out.decode("utf-8")
        self.assertIn("theme.css", txt)
        self.assertIn("screen and (min-width: 600px)", txt)
        self.assertTrue(txt.startswith("@import"))

        out2 = cloner.rewriter.rewrite_css(
            b'@import "/lib/theme.css" screen and (min-width: 600px);',
            "https://example.com/css/style.css")
        txt2 = out2.decode("utf-8")
        self.assertIn("screen and (min-width: 600px)", txt2)
        self.assertIn("../lib/theme.css", txt2)

    def test_import_plain(self):
        cloner = make_cloner()
        out = cloner.rewriter.rewrite_css(b'@import "/lib/a.css";',
                                          "https://example.com/css/style.css")
        txt = out.decode("utf-8")
        self.assertIn("a.css", txt)
        self.assertIn("../lib/a.css", txt)
        self.assertEqual(txt.count("@import"), 1)

    def test_url_rewritten(self):
        cloner = make_cloner()
        out = cloner.rewriter.rewrite_css(
            b"body{background:url(img/bg.png)}",
            "https://example.com/style.css")
        txt = out.decode("utf-8")
        self.assertIn("url(img/bg.png)", txt)

    def test_sourcemap_rewritten(self):
        cloner = make_cloner()
        out = cloner.rewriter.rewrite_css(
            b"/*# sourceMappingURL=style.css.map */",
            "https://example.com/style.css")
        self.assertIn("style.css.map", out.decode("utf-8"))


class TestJs(unittest.TestCase):
    def test_new_url_second_arg_kept(self):
        cloner = make_cloner()
        js = 'const u = new URL("./chunk.js", import.meta.url);'
        out = cloner.rewriter.rewrite_js_text(js, "https://example.com/app.js", ".")
        self.assertEqual(out, 'const u = new URL("chunk.js", import.meta.url);')

    def test_from_literal_not_touched(self):
        cloner = make_cloner()
        js = 'const s = "please come from over there";'
        out = cloner.rewriter.rewrite_js_text(js, "https://example.com/app.js", ".")
        self.assertEqual(out, js)

    def test_bare_specifiers_preserved(self):
        cloner = make_cloner()
        js = ('import React from "react";\n'
              'import { useState } from "react";\n'
              'require("lodash");')
        out = cloner.rewriter.rewrite_js_text(js, "https://example.com/app.js", ".")
        self.assertIn('import React from "react";', out)
        self.assertIn('import { useState } from "react";', out)
        self.assertIn('require("lodash");', out)

    def test_relative_require_rewritten(self):
        cloner = make_cloner()
        out = cloner.rewriter.rewrite_js_text(
            'require("./local.js");', "https://example.com/app.js", ".")
        self.assertEqual(out, 'require("local.js");')

    def test_fetch_rewritten(self):
        cloner = make_cloner()
        out = cloner.rewriter.rewrite_js_text(
            'fetch("/api/data.json");', "https://example.com/app.js", ".")
        self.assertIn("api/data.json", out)
        self.assertNotIn("https://example.com", out)


class TestDepth(unittest.TestCase):
    def test_page_out_of_depth_linked_live(self):
        cloner = make_cloner(max_depth=1)
        out = cloner.rewriter.queue_url("page3.html",
                                        "https://example.com/page2.html", 2)
        self.assertEqual(out, "https://example.com/page3.html")
        self.assertNotIn("https://example.com/page3.html", cloner._seen)
        self.assertTrue(all("page3" not in u for u, _, _ in cloner._queue))

    def test_page_in_depth_queued(self):
        cloner = make_cloner(max_depth=1)
        out = cloner.rewriter.queue_url("page2.html",
                                        "https://example.com/", 1)
        self.assertEqual(out, "page2.html")
        self.assertIn("https://example.com/page2.html", cloner._seen)

    def test_assets_always_cloned(self):
        cloner = make_cloner(max_depth=0)
        out = cloner.rewriter.queue_url("logo.png",
                                        "https://example.com/", 99)
        self.assertEqual(out, "logo.png")
        self.assertIn("https://example.com/logo.png", cloner._seen)

    def test_process_defensive_skip(self):
        cloner = make_cloner(max_depth=1)
        with mock.patch("cs_crawl.fetch_once") as m:
            cloner._process("https://example.com/deep.html", 5, None)
        m.assert_not_called()
        self.assertEqual(cloner.stats["depth_cut"], 1)


class TestLoginPath(unittest.TestCase):
    def test_account_not_matched(self):
        self.assertIsNone(LOGIN_PATH_RE.search("/account"))

    def test_real_login_paths(self):
        for p in ("/login", "/connexion", "/signin", "/admin/auth"):
            self.assertIsNotNone(LOGIN_PATH_RE.search(p), p)


class TestRobots(unittest.TestCase):
    TEXT = (
        "User-agent: *\n"
        "Disallow: /private/\n"
        "Allow: /private/open\n"
        "Disallow: /tmp/*.gif\n"
        "Disallow: /gone$\n"
        "Crawl-delay: 2\n"
    )

    def test_parse(self):
        groups = parse_robots(self.TEXT)
        rules = groups["*"]
        self.assertIn("/private/", rules.disallow)
        self.assertIn("/private/open", rules.allow)
        self.assertEqual(rules.crawl_delay, 2.0)

    def test_pick_fallback_star(self):
        groups = parse_robots(self.TEXT)
        r = pick_robots_group(groups, "spiderclone/2.3.0 (test)")
        self.assertIs(r, groups["*"])

    def test_allow_priority(self):
        groups = parse_robots(self.TEXT)
        rules = groups["*"]
        self.assertFalse(rules.allowed("https://x/private/secret"))
        self.assertTrue(rules.allowed("https://x/private/open"))

    def test_wildcard_and_end_anchor(self):
        groups = parse_robots(self.TEXT)
        rules = groups["*"]
        self.assertFalse(rules.allowed("https://x/tmp/pic.gif"))
        self.assertTrue(rules.allowed("https://x/tmp/pic.png"))
        self.assertFalse(rules.allowed("https://x/gone"))
        self.assertTrue(rules.allowed("https://x/gone/"))

    def test_no_rules_allows(self):
        self.assertTrue(robots_path_allowed(None, "https://x/anything"))
        self.assertTrue(robots_path_allowed(parse_robots("").get("*"),
                                            "https://x/"))


class TestFetch(unittest.TestCase):
    class _Resp:
        def __init__(self, status=200, body=b"ok", ctype="text/html",
                     retry_after=None):
            self.status_code = status
            self.headers = {"Content-Type": ctype}
            if retry_after:
                self.headers["Retry-After"] = retry_after
            self.url = "http://test/"
            self._body = body
            self.closed = False

        def iter_content(self, chunk_size=65536):
            for i in range(0, len(self._body), chunk_size):
                yield self._body[i:i + chunk_size]

        def close(self):
            self.closed = True

    class _Session:
        def __init__(self, resp):
            self.resp = resp
            self.calls = []

        def get(self, url, **kw):
            self.calls.append(kw)
            return self.resp

    def test_single_get_stream(self):
        sess = self._Session(self._Resp(body=b"x" * 100))
        r = fetch_once(sess, "http://test/", 1024)
        self.assertEqual(len(sess.calls), 1)
        self.assertTrue(sess.calls[0]["stream"])
        self.assertEqual(r.content, b"x" * 100)

    def test_dead_link_404(self):
        sess = self._Session(self._Resp(status=404))
        with self.assertRaises(DeadLinkError):
            fetch_once(sess, "http://test/page", 1024)

    def test_max_size_reached(self):
        sess = self._Session(self._Resp(body=b"x" * 100))
        with self.assertRaises(RuntimeError):
            fetch_once(sess, "http://test/", 50)

    def test_on_login_called(self):
        r = self._Resp()
        r.url = "https://site/login?next=/"
        sess = self._Session(r)
        seen = []
        fetch_once(sess, "http://test/", 1024, on_login=lambda: seen.append(1))
        self.assertEqual(seen, [1])


    @mock.patch("cs_fetch.time.sleep")
    def test_protected_401_no_retry(self, sleep):
        sess = self._Session(self._Resp(status=401))
        with self.assertRaises(ProtectedError):
            fetch_once(sess, "http://test/private", 1024)
        self.assertEqual(len(sess.calls), 1)
        sleep.assert_not_called()

    @mock.patch("cs_fetch.time.sleep")
    def test_protected_403_no_retry(self, sleep):
        sess = self._Session(self._Resp(status=403))
        with self.assertRaises(ProtectedError):
            fetch_once(sess, "http://test/private", 1024)
        self.assertEqual(len(sess.calls), 2)
        sleep.assert_not_called()

    @mock.patch("cs_fetch.time.sleep")
    def test_protected_403_with_referer_no_retry(self, sleep):
        sess = self._Session(self._Resp(status=403))
        with self.assertRaises(ProtectedError):
            fetch_once(sess, "http://test/private", 1024, referer="http://test/")
        self.assertEqual(len(sess.calls), 1)
        sleep.assert_not_called()

    @mock.patch("cs_fetch.time.sleep")
    def test_rate_limited_429_retries_then_raises(self, sleep):
        sess = self._Session(self._Resp(status=429))
        with self.assertRaises(RateLimitedError):
            fetch_once(sess, "http://test/assets/x.js", 1024)
        from cs_config import MAX_RETRIES
        self.assertEqual(len(sess.calls), MAX_RETRIES)
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)

    @mock.patch("cs_fetch.time.sleep")
    def test_rate_limited_honors_retry_after(self, sleep):
        sess = self._Session(self._Resp(status=429,
                                        ctype="text/html",
                                        retry_after="2"))
        with self.assertRaises(RateLimitedError):
            fetch_once(sess, "http://test/assets/x.js", 1024)
        for call in sleep.call_args_list:
            args, _ = call
            self.assertLessEqual(args[0], 2.0)

    @mock.patch("cs_fetch.time.sleep")
    def test_503_rate_limited(self, sleep):
        sess = self._Session(self._Resp(status=503))
        with self.assertRaises(RateLimitedError):
            fetch_once(sess, "http://test/", 1024)


    def _cf_resp(self, status=403, body=b"just a moment...",
                 cf_mitigated=""):
        r = self._Resp(status=status, body=body)
        r.headers["Server"] = "cloudflare"
        if cf_mitigated:
            r.headers["cf-mitigated"] = cf_mitigated
        return r

    def test_is_cloudflare_challenge_detection(self):
        self.assertTrue(is_cloudflare_challenge(
            {"Server": "cloudflare", "cf-mitigated": "challenge"},
            b"<html>cdn-cgi/challenge-platform/scripts/jsd/main.js</html>"))
        self.assertTrue(is_cloudflare_challenge(
            {"Server": "cloudflare"},
            b"<title>Just a moment...</title>"))
        self.assertTrue(is_cloudflare_challenge(
            {"Server": "cloudflare"}, b"_cf_chl_opt = {...}"))
        self.assertFalse(is_cloudflare_challenge(
            {"Server": "cloudflare"}, b"<html>Bienvenue</html>"))
        self.assertFalse(is_cloudflare_challenge(
            {"Server": "nginx"}, b"just a moment..."))
        self.assertFalse(is_cloudflare_challenge({"Server": "cloudflare"}, b""))

    @mock.patch("cs_fetch.time.sleep")
    def test_challenge_403_retries_then_raises(self, sleep):
        sess = self._Session(self._cf_resp(status=403))
        with self.assertRaises(CloudflareChallengeError):
            fetch_once(sess, "http://test/", 1024)
        from cs_config import MAX_RETRIES
        self.assertEqual(len(sess.calls), MAX_RETRIES)
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)

    @mock.patch("cs_fetch.time.sleep")
    def test_challenge_503_cf_mitigated(self, sleep):
        r = self._Resp(status=503, body=b"<html>jschl_vc=abc123</html>")
        r.headers["cf-mitigated"] = "challenge"
        sess = self._Session(r)
        with self.assertRaises(CloudflareChallengeError):
            fetch_once(sess, "http://test/", 1024)
        from cs_config import MAX_RETRIES
        self.assertEqual(len(sess.calls), MAX_RETRIES)

    @mock.patch("cs_fetch.time.sleep")
    def test_challenge_200_cf_mitigated(self, sleep):
        r = self._cf_resp(status=200,
                          body=b"<html>cdn-cgi/challenge-platform</html>",
                          cf_mitigated="challenge")
        sess = self._Session(r)
        with self.assertRaises(CloudflareChallengeError):
            fetch_once(sess, "http://test/", 1024)
        from cs_config import MAX_RETRIES
        self.assertEqual(len(sess.calls), MAX_RETRIES)

    @mock.patch("cs_fetch.time.sleep")
    def test_challenge_recovers_after_retry(self, sleep):
        class _SeqSession:
            def __init__(self, resp_list):
                self.resp_list = list(resp_list)
                self.calls = []

            def get(self, url, **kw):
                self.calls.append(kw)
                r = self.resp_list.pop(0)
                return r

        challenge = self._cf_resp(status=403)
        ok = self._Resp(status=200, body=b"<html>page reelle</html>")
        sess = _SeqSession([challenge, ok])
        r = fetch_once(sess, "http://test/", 1024)
        self.assertEqual(r.content, b"<html>page reelle</html>")
        self.assertEqual(len(sess.calls), 2)

    def test_403_plain_not_challenge(self):
        r = self._Resp(status=403, body=b"<html>forbidden</html>")
        r.headers["Server"] = "cloudflare"
        sess = self._Session(r)
        with self.assertRaises(ProtectedError):
            fetch_once(sess, "http://test/private", 1024)
        self.assertEqual(len(sess.calls), 2)

    def test_500_is_hard_failure(self):
        sess = self._Session(self._Resp(status=500))
        with self.assertRaises(RuntimeError) as ctx:
            fetch_once(sess, "http://test/", 1024)
        self.assertNotIsInstance(ctx.exception, RateLimitedError)


class TestThrottle(unittest.TestCase):
    def test_per_host_delay(self):
        th = Throttle(rate=0)
        th.set_delay("example.com", 0.05)
        t0 = time.monotonic()
        th.wait("https://example.com/a")
        th.wait("https://example.com/b")
        self.assertGreaterEqual(time.monotonic() - t0, 0.04)

    def test_no_delay_no_wait(self):
        th = Throttle(rate=0)
        t0 = time.monotonic()
        th.wait("https://example.com/a")
        self.assertLess(time.monotonic() - t0, 0.02)

    def test_rate_combined(self):
        th = Throttle(rate=50)
        t0 = time.monotonic()
        th.wait("https://a.com/x")
        th.wait("https://a.com/y")
        self.assertGreaterEqual(time.monotonic() - t0, 0.01)

    def test_different_hosts_independent(self):
        th = Throttle(rate=0)
        th.set_delay("a.com", 0.05)
        th.set_delay("b.com", 0.05)
        t0 = time.monotonic()
        th.wait("https://a.com/x")
        th.wait("https://b.com/y")
        self.assertLess(time.monotonic() - t0, 0.04)


class TestCollisions(unittest.TestCase):
    def test_case_insensitive_collision(self):
        cloner = make_cloner()
        p1 = cloner._local_path("https://example.com/Page.html")
        p2 = cloner._local_path("https://example.com/page.html")
        self.assertEqual(p1, "Page.html")
        self.assertNotEqual(p1, p2)
        self.assertIn("__", p2)
        self.assertEqual(cloner._local_path("https://example.com/Page.html"), p1)

    def test_query_variants(self):
        cloner = make_cloner()
        q1 = cloner._local_path("https://example.com/index.html?ref=a")
        q2 = cloner._local_path("https://example.com/index.html?ref=b")
        self.assertNotEqual(q1, q2)
        self.assertIn("__", q2)


class TestManifest(unittest.TestCase):
    def test_roundtrip_and_registry(self):
        tmp = tempfile.mkdtemp(prefix="spiderclone_test_")
        cloner = SiteClone(SITE, tmp, max_depth=3, workers=2)
        cloner.pages[SITE] = "index.html"
        cloner.assets["https://example.com/a.css"] = "a.css"
        cloner._save_manifest()
        self.assertTrue(cloner.manifest_path.exists())

        cloner2 = SiteClone(SITE, tmp, max_depth=3, workers=2)
        with mock.patch("sys.stdout", new_callable=io.StringIO):
            cloner2._load_manifest()
        self.assertEqual(cloner2.pages, {SITE: "index.html"})
        self.assertEqual(cloner2.assets,
                         {"https://example.com/a.css": "a.css"})
        self.assertEqual(cloner2._path_registry.get("index.html"), SITE)
        p = cloner2._local_path("https://example.com/INDEX.HTML")
        self.assertIn("__", p)


class TestEncoding(unittest.TestCase):
    def test_utf8(self):
        text, enc = decode_any("héllo".encode("utf-8"))
        self.assertEqual(enc, "utf-8")
        self.assertEqual(text, "héllo")

    def test_latin1_fallback(self):
        text, enc = decode_any("caf\xe9".encode("latin-1"))
        self.assertEqual(enc, "latin-1")
        self.assertEqual(text, "café")


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


class TestUI(unittest.TestCase):
    """Bannière v2.2.0 : araignée, lettrage, couleurs, marqueurs."""

    def setUp(self):
        cs_ui.set_color(False)

    def tearDown(self):
        cs_ui.set_color(None)

    def test_spider_25_rows_max_58(self):
        self.assertEqual(len(cs_ui.SPIDER), 25)
        for r in cs_ui.SPIDER:
            self.assertLessEqual(len(r.rstrip()), cs_ui.BANNER_WIDTH)

    def test_spider_ascii_only(self):
        for r in cs_ui.SPIDER:
            self.assertTrue(all(ord(c) < 128 for c in r), r)

    def test_red_spans_in_bounds_non_space(self):
        for row, spans in cs_ui._RED_SPANS.items():
            for a, b in spans:
                self.assertLessEqual(b, len(cs_ui.SPIDER[row]))
                self.assertTrue(cs_ui.SPIDER[row][a:b].strip(),
                                (row, a, b))

    def test_wordmark_width_and_split(self):
        wm = cs_ui._wordmark()
        self.assertEqual(len(wm), 5)
        for line in wm:
            self.assertEqual(len(line), 54)
        self.assertEqual(cs_ui.CLONE_START, 30)
        spider = cs_ui._render("spider")
        clone = cs_ui._render("clone")
        for i in range(5):
            self.assertEqual(wm[i][:30], spider[i] + " ")
            self.assertEqual(wm[i][30:], clone[i])

    def test_wordmark_ascii_only(self):
        for glyph in cs_ui._FONT.values():
            for line in glyph:
                self.assertTrue(all(ord(c) < 128 for c in line), line)

    def test_banner_plain_no_ansi(self):
        cs_ui.set_color(False)
        for line in cs_ui.banner_lines():
            self.assertNotIn("\x1b", line)

    def test_banner_width_max_58(self):
        for line in cs_ui.banner_lines():
            self.assertLessEqual(len(line), cs_ui.BANNER_WIDTH)

    def test_colored_strips_to_plain(self):
        cs_ui.set_color(True)
        colored = cs_ui.banner_lines()
        cs_ui.set_color(False)
        plain = cs_ui.banner_lines()
        self.assertEqual([ANSI_RE.sub("", l) for l in colored], plain)

    def test_markers_plain(self):
        cs_ui.set_color(False)
        self.assertEqual(cs_ui.step("msg"), "[+] msg")
        self.assertEqual(cs_ui.info("msg"), "[*] msg")
        self.assertEqual(cs_ui.warn("msg"), "[!] msg")

    def test_markers_colored(self):
        cs_ui.set_color(True)
        self.assertEqual(cs_ui.step("msg"), "\x1b[1;31m[+]\x1b[0m msg")
        self.assertEqual(cs_ui.info("msg"), "\x1b[1;31m[*]\x1b[0m msg")
        self.assertEqual(cs_ui.warn("msg"), "\x1b[1;31m[!]\x1b[0m msg")

    def test_banner_contains_wordmark_and_prompt(self):
        lines = cs_ui.banner_lines()
        joined = "\n".join(lines)
        self.assertIn("user@spiderclone:~$", joined)
        self.assertIn("Bonne exploration !", joined)


class TestInteractive(unittest.TestCase):
    """Mode interactif v2.3.0 : copy <site>, URL optionnelle, invite."""

    def test_copy_command(self):
        from spiderclone import parse_copy_command
        self.assertEqual(parse_copy_command("copy https://exemple.fr"),
                         "https://exemple.fr")
        self.assertEqual(parse_copy_command("clone exemple.fr"), "exemple.fr")
        self.assertEqual(parse_copy_command("COPY  https://x/"), "https://x/")

    def test_non_copy_commands(self):
        from spiderclone import parse_copy_command
        self.assertIsNone(parse_copy_command("exit"))
        self.assertIsNone(parse_copy_command("quit"))
        self.assertIsNone(parse_copy_command("help"))
        self.assertIsNone(parse_copy_command(""))
        self.assertIsNone(parse_copy_command("   "))
        self.assertIsNone(parse_copy_command("copy"))
        self.assertIsNone(parse_copy_command("ls"))

    def test_normalize_url(self):
        from spiderclone import _normalize_url
        self.assertEqual(_normalize_url("exemple.fr"), "https://exemple.fr")
        self.assertEqual(_normalize_url("https://x"), "https://x")
        self.assertEqual(_normalize_url("  http://x/  "), "http://x/")

    def test_url_optional(self):
        args = build_parser().parse_args([])
        self.assertIsNone(args.url)
        args = build_parser().parse_args(["https://x"])
        self.assertEqual(args.url, "https://x")

    def test_prompt_plain(self):
        cs_ui.set_color(False)
        self.assertEqual(cs_ui.prompt(), "user@spiderclone:~$ ")


class TestCLI(unittest.TestCase):
    """Options v2.2.0 : --no-banner / --no-color, version ; flags v2.4.0 et
    v2.5.0 (--serve / --tunnel / --port / --serve-dir)."""

    def test_version_251(self):
        self.assertEqual(VERSION, "2.5.1")

    def test_flags_parsed(self):
        args = build_parser().parse_args(
            ["https://x", "--no-banner", "--no-color"])
        self.assertTrue(args.no_banner)
        self.assertTrue(args.no_color)

    def test_flags_default_none(self):
        args = build_parser().parse_args(["https://x"])
        self.assertIsNone(args.no_banner)
        self.assertIsNone(args.no_color)

    def test_defaults_include_flags(self):
        from spiderclone import DEFAULTS
        self.assertFalse(DEFAULTS["no_banner"])
        self.assertFalse(DEFAULTS["no_color"])

    def test_flags_240_parsed(self):
        args = build_parser().parse_args(
            ["https://x", "--only-assets", "--only-pages", "--screenshot"])
        self.assertTrue(args.only_assets)
        self.assertTrue(args.only_pages)
        self.assertTrue(args.screenshot)

    def test_flags_240_defaults(self):
        from spiderclone import DEFAULTS
        self.assertFalse(DEFAULTS["only_assets"])
        self.assertFalse(DEFAULTS["only_pages"])
        self.assertFalse(DEFAULTS["screenshot"])

    def test_defaults_240_in_config_schema(self):
        from spiderclone import DEFAULTS, _load_config
        self.assertIn("only_assets", DEFAULTS)
        self.assertIn("only_pages", DEFAULTS)
        self.assertIn("screenshot", DEFAULTS)

    def test_flags_250_parsed(self):
        args = build_parser().parse_args(
            ["https://x", "--serve", "--tunnel", "ngrok", "--port", "9000"])
        self.assertTrue(args.serve)
        self.assertEqual(args.tunnel, "ngrok")
        self.assertEqual(args.port, 9000)

    def test_flags_250_serve_dir(self):
        args = build_parser().parse_args(
            ["--serve-dir", "mon-site", "--tunnel", "cloudflare"])
        self.assertEqual(args.serve_dir, "mon-site")
        self.assertEqual(args.tunnel, "cloudflare")

    def test_tunnel_rejected_value(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["https://x", "--tunnel", "tor"])

    def test_flags_250_defaults(self):
        from spiderclone import DEFAULTS
        self.assertFalse(DEFAULTS["serve"])
        self.assertEqual(DEFAULTS["tunnel"], "none")
        self.assertEqual(DEFAULTS["port"], DEFAULT_PORT)
        self.assertIsNone(DEFAULTS["serve_dir"])

    def test_serve_command_parsed(self):
        from spiderclone import parse_serve_command
        self.assertEqual(parse_serve_command("serve site-x ngrok"),
                         ("site-x", "ngrok"))
        self.assertEqual(parse_serve_command("publish site-x"),
                         ("site-x", "none"))
        self.assertEqual(parse_serve_command("serve site-x toto"), ("", ""))
        self.assertIsNone(parse_serve_command("copy https://x"))

    def test_tunnel_named_choice_accepted(self):
        args = build_parser().parse_args(
            ["--serve-dir", "x", "--tunnel", "cloudflare-named"])
        self.assertEqual(args.tunnel, "cloudflare-named")

    def test_flags_cf_parsed(self):
        args = build_parser().parse_args(
            ["--serve-dir", "x", "--tunnel", "cloudflare-named",
             "--cf-token", "TOK123", "--cf-tunnel", "mon-tunnel",
             "--cf-config", "tun.yml"])
        self.assertEqual(args.cf_token, "TOK123")
        self.assertEqual(args.cf_tunnel, "mon-tunnel")
        self.assertEqual(args.cf_config, "tun.yml")

    def test_flags_cf_defaults(self):
        from spiderclone import DEFAULTS
        self.assertIsNone(DEFAULTS["cf_token"])
        self.assertIsNone(DEFAULTS["cf_tunnel"])
        self.assertIsNone(DEFAULTS["cf_config"])

    def test_serve_command_named_parsed(self):
        from spiderclone import parse_serve_command
        self.assertEqual(parse_serve_command("serve site-x cloudflare-named"),
                         ("site-x", "cloudflare-named"))

    def test_flag_proxy_parsed(self):
        args = build_parser().parse_args(
            ["https://x", "--proxy", "http://127.0.0.1:8080"])
        self.assertEqual(args.proxy, "http://127.0.0.1:8080")

    def test_flag_proxy_default(self):
        from spiderclone import DEFAULTS
        self.assertIsNone(DEFAULTS["proxy"])

    def test_proxy_wired_to_session(self):
        cloner = make_cloner(proxy="socks5://127.0.0.1:9050")
        self.assertEqual(cloner.session.proxies,
                         {"http": "socks5://127.0.0.1:9050",
                          "https": "socks5://127.0.0.1:9050"})
        self.assertEqual(make_cloner().session.proxies, {})



class TestServe(unittest.TestCase):
    """v2.5.0 : publication (serveur local, repli SPA, parseurs tunnels,
    binaire manquant, publish non bloquant)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="sc_serve_")
        (Path(self.tmp) / "index.html").write_text(
            "<!DOCTYPE html><html><body>accueil</body></html>", "utf-8")
        (Path(self.tmp) / "styles.css").write_text("body{color:red}", "utf-8")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_free_port(self):
        port = find_free_port(0)
        self.assertTrue(0 < port <= 65535)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            s.listen(1)
            busy = s.getsockname()[1]
            self.assertNotEqual(find_free_port(busy), busy)

    def test_serve_directory_get(self):
        server = serve_directory(self.tmp, 0)
        port = server.server_address[1]
        r = requests.get(f"http://127.0.0.1:{port}/index.html", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("text/html", r.headers["Content-Type"])
        self.assertIn("accueil", r.text)
        r2 = requests.get(f"http://127.0.0.1:{port}/styles.css", timeout=5)
        self.assertIn("text/css", r2.headers["Content-Type"])
        r3 = requests.get(f"http://127.0.0.1:{port}/inconnu.css", timeout=5)
        self.assertEqual(r3.status_code, 404)
        stop(server, None)

    def test_spa_fallback(self):
        server = serve_directory(self.tmp, 0)
        port = server.server_address[1]
        r = requests.get(f"http://127.0.0.1:{port}/profil/xyz", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("accueil", r.text)
        stop(server, None)

    def test_directory_listing_without_index(self):
        d = tempfile.mkdtemp(prefix="sc_list_")
        os.makedirs(os.path.join(d, "sous"))
        (Path(d) / "sous" / "f.txt").write_text("x", "utf-8")
        server = serve_directory(d, 0)
        port = server.server_address[1]
        r = requests.get(f"http://127.0.0.1:{port}/", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("sous", r.text)
        stop(server, None)
        shutil.rmtree(d, ignore_errors=True)

    def test_post_mock_json(self):
        """v2.5.1 : un POST API inconnu renvoie un JSON vide (plus de 501)."""
        server = serve_directory(self.tmp, 0)
        port = server.server_address[1]
        r = requests.post(f"http://127.0.0.1:{port}/ajax/bz?__a=1", data="x",
                          timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIn("application/json", r.headers["Content-Type"])
        self.assertEqual(r.json(), {})
        stop(server, None)

    def test_post_serves_cloned_asset(self):
        """v2.5.1 : un POST vers un endpoint cloné sert l'asset (absent auparavant)."""
        api_dir = Path(self.tmp) / "api"
        api_dir.mkdir(parents=True, exist_ok=True)
        (api_dir / "graphql.json").write_text(
            '{"data":{"ok":true}}', "utf-8")
        server = serve_directory(self.tmp, 0)
        port = server.server_address[1]
        r = requests.post(f"http://127.0.0.1:{port}/api/graphql", data="{}",
                          timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"data": {"ok": True}})
        stop(server, None)

    def test_post_501_when_mock_disabled(self):
        """v2.5.1 : mock_api=False restaure le 501 explicite."""
        from cs_serve import CloneHandler
        old = CloneHandler.mock_api
        CloneHandler.mock_api = False
        try:
            server = serve_directory(self.tmp, 0)
            port = server.server_address[1]
            r = requests.post(f"http://127.0.0.1:{port}/ajax/bz", data="x",
                              timeout=5)
            self.assertEqual(r.status_code, 501)
            stop(server, None)
        finally:
            CloneHandler.mock_api = old

    def test_options_preflight(self):
        """v2.5.1 : OPTIONS répond 204 avec la liste des méthodes."""
        server = serve_directory(self.tmp, 0)
        port = server.server_address[1]
        r = requests.options(f"http://127.0.0.1:{port}/ajax/bz", timeout=5)
        self.assertEqual(r.status_code, 204)
        self.assertIn("POST", r.headers.get("Allow", ""))
        stop(server, None)

    def test_extract_ngrok_url(self):
        js = ('{"tunnels":[{"public_url":"http://abc.ngrok-free.app"},'
              '{"public_url":"https://abc.ngrok-free.app"}]}')
        self.assertEqual(extract_ngrok_url(js), "https://abc.ngrok-free.app")
        self.assertIsNone(extract_ngrok_url("pas du json"))

    def test_extract_cloudflared_url(self):
        line = "2026-09-22 INFO |  https://abc-xyz.trycloudflare.com"
        self.assertEqual(extract_cloudflared_url(line),
                         "https://abc-xyz.trycloudflare.com")
        self.assertIsNone(extract_cloudflared_url("rien ici"))

    def test_tunnel_missing_binary_hint(self):
        with mock.patch("cs_serve.subprocess.Popen",
                        side_effect=FileNotFoundError):
            with self.assertRaises(TunnelError) as ctx:
                start_ngrok(59999)
            self.assertIn("ngrok", str(ctx.exception))
            with self.assertRaises(TunnelError) as ctx2:
                start_cloudflared(59999)
            self.assertIn("cloudflared", str(ctx2.exception))

    def test_publish_non_blocking(self):
        server, proc, url = publish(self.tmp, "none", 0, block=False)
        port = server.server_address[1]
        r = requests.get(f"http://127.0.0.1:{port}/index.html", timeout=5)
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(proc)
        self.assertIsNone(url)
        stop(server, None)

    def test_publish_invalid_tunnel_and_dir(self):
        with self.assertRaises(ValueError):
            publish(self.tmp, "tor", 0, block=False)
        with self.assertRaises(ValueError):
            publish(os.path.join(self.tmp, "inexistant"), "none", 0,
                    block=False)

    def test_build_cloudflared_named_args_token(self):
        args = build_cloudflared_named_args(8000, token="TOK123")
        self.assertEqual(args, ["cloudflared", "--no-autoupdate", "tunnel",
                                "run", "--token", "TOK123"])

    def test_build_cloudflared_named_args_name(self):
        args = build_cloudflared_named_args(8012, tunnel_name="mon-tunnel")
        self.assertEqual(args, ["cloudflared", "--no-autoupdate", "tunnel",
                                "run", "--url", "http://127.0.0.1:8012",
                                "mon-tunnel"])

    def test_build_cloudflared_named_args_config(self):
        args = build_cloudflared_named_args(8000, config="tun.yml")
        self.assertEqual(args, ["cloudflared", "--no-autoupdate", "tunnel",
                                "--config", "tun.yml", "run"])

    def test_build_cloudflared_named_args_no_setting(self):
        with self.assertRaises(ValueError) as ctx:
            build_cloudflared_named_args(8000)
        self.assertIn("--cf-token", str(ctx.exception))

    def test_named_tunnel_missing_binary(self):
        with mock.patch("cs_serve.subprocess.Popen",
                        side_effect=FileNotFoundError):
            with self.assertRaises(TunnelError) as ctx:
                start_cloudflared_named(59999, token="TOK")
            self.assertIn("cloudflared", str(ctx.exception))

    def test_named_tunnel_crash_in_grace(self):
        fake = mock.Mock()
        fake.wait.return_value = 1
        with mock.patch("cs_serve.subprocess.Popen", return_value=fake):
            with self.assertRaises(TunnelError) as ctx:
                start_cloudflared_named(59999, tunnel_name="t")
            self.assertIn("s'est arrêté", str(ctx.exception))

    def test_named_tunnel_alive_after_grace(self):
        fake = mock.Mock()
        fake.wait.side_effect = subprocess.TimeoutExpired(cmd="c", timeout=5)
        with mock.patch("cs_serve.subprocess.Popen", return_value=fake):
            proc, url = start_cloudflared_named(59999,
                                                config="cfg.yml")
            self.assertIs(proc, fake)
            self.assertIsNone(url)

    def test_publish_named_without_setting(self):
        with self.assertRaises(ValueError) as ctx:
            publish(self.tmp, "cloudflare-named", 0, block=False)
        self.assertIn("--cf-token", str(ctx.exception))

    def test_publish_fallback_local(self):
        with mock.patch("cs_serve.start_ngrok",
                        side_effect=TunnelError("ngrok introuvable")):
            server, proc, url = publish(self.tmp, "ngrok", 0, block=False)
        try:
            port = server.server_address[1]
            r = requests.get(f"http://127.0.0.1:{port}/index.html", timeout=5)
            self.assertEqual(r.status_code, 200)
            self.assertIsNone(proc)
            self.assertIsNone(url)
        finally:
            stop(server, None)

    def test_publish_no_fallback_raises(self):
        with mock.patch("cs_serve.start_cloudflared",
                        side_effect=TunnelError("cloudflared introuvable")):
            with self.assertRaises(TunnelError):
                publish(self.tmp, "cloudflare", 0, block=False,
                        fallback=False)

    def test_publish_cloudflare_named_mocked(self):
        fake = mock.Mock()
        with mock.patch("cs_serve.start_cloudflared_named",
                        return_value=(fake, None)) as m:
            server, proc, url = publish(self.tmp, "cloudflare-named", 0,
                                        block=False, cf_token="TOK")
        try:
            m.assert_called_once()
            port = server.server_address[1]
            r = requests.get(f"http://127.0.0.1:{port}/index.html", timeout=5)
            self.assertEqual(r.status_code, 200)
            self.assertIs(proc, fake)
            self.assertIsNone(url)
        finally:
            stop(server, proc)

class TestNoteFailure(unittest.TestCase):
    """v2.3.0 : tri des échecs 401/403/429 dans des buckets séparés."""

    def _cloner(self):
        return make_cloner()

    def test_rate_limited_429_buckets(self):
        cloner = self._cloner()
        cloner._note_failure("http://test/a.js",
                             "HTTP 429 (limite de débit)")
        self.assertEqual(cloner.rate_limited,
                         [("http://test/a.js", "HTTP 429 (limite de débit)")])
        self.assertEqual(cloner.stats["rate_limited"], 1)
        self.assertEqual(cloner.failures, [])

    def test_protected_401_buckets(self):
        cloner = self._cloner()
        cloner._note_failure("http://test/secret",
                             "HTTP 401 (ressource protégée)")
        self.assertEqual(cloner.protected,
                         [("http://test/secret", "HTTP 401 (ressource protégée)")])
        self.assertEqual(cloner.stats["protected"], 1)
        self.assertEqual(cloner.failures, [])

    def test_dead_link_buckets(self):
        cloner = self._cloner()
        cloner._note_failure("http://test/missing", "HTTP 404 (lien mort)")
        self.assertEqual(cloner.stats["dead"], 1)
        self.assertEqual(cloner.failures, [])
        self.assertEqual(cloner.rate_limited, [])
        self.assertEqual(cloner.protected, [])

    def test_dedup_rate_limited(self):
        cloner = self._cloner()
        cloner._note_failure("http://test/a.js", "HTTP 429 (limite de débit)")
        cloner._note_failure("http://test/a.js", "HTTP 429 (limite de débit)")
        self.assertEqual(len(cloner.rate_limited), 1)
        self.assertEqual(cloner.stats["rate_limited"], 1)

    def test_dedup_protected(self):
        cloner = self._cloner()
        cloner._note_failure("http://test/secret", "HTTP 401 (ressource protégée)")
        cloner._note_failure("http://test/secret", "HTTP 401 (ressource protégée)")
        self.assertEqual(len(cloner.protected), 1)
        self.assertEqual(cloner.stats["protected"], 1)

    def test_challenge_cloudflare_buckets(self):
        cloner = self._cloner()
        cloner._note_failure("http://test/challenged",
                             CloudflareChallengeError(
                                 "HTTP 403 (challenge Cloudflare)"))
        self.assertEqual(len(cloner.protected), 1)
        self.assertEqual(cloner.stats["protected"], 1)
        self.assertEqual(cloner.failures, [])
        self.assertEqual(cloner.protected[0][1], "HTTP 403 (challenge Cloudflare)")

    def test_challenge_string_dedupe(self):
        cloner = self._cloner()
        cloner._note_failure("http://test/c2", "HTTP 503 (challenge Cloudflare)")
        cloner._note_failure("http://test/c2", "HTTP 503 (challenge Cloudflare)")
        self.assertEqual(len(cloner.protected), 1)
        self.assertEqual(cloner.stats["protected"], 1)

    def test_second_pass_urls_recovered(self):
        cloner = self._cloner()
        cloner._second_pass_urls.add("http://example.com/app.js")
        cloner.stats["recovered"] = 0
        cloner._second_pass_urls.discard("http://example.com/app.js")
        self.assertEqual(cloner.stats["recovered"], 0)


class TestPlaceholders(unittest.TestCase):
    """v2.4.0 : fichiers de remplacement des ressources non téléchargées."""

    def _cloner(self):
        return make_cloner()

    def test_placeholder_kind_by_extension(self):
        cloner = self._cloner()
        self.assertEqual(cloner._placeholder_for("img/photo.png"),
                         PNG_PLACEHOLDER)
        self.assertEqual(cloner._placeholder_for("img/icon.svg"),
                         SVG_PLACEHOLDER.encode("utf-8"))
        for rel in ("css/style.css", "js/app.mjs", "app.js.map"):
            self.assertTrue(
                cloner._placeholder_for(rel).startswith(b"/* spiderclone"))
        self.assertTrue(
            cloner._placeholder_for("page.html").startswith(b"<!DOCTYPE"))
        self.assertEqual(cloner._placeholder_for("fonts/x.woff2"), b"")

    def test_write_placeholders_creates_files(self):
        cloner = self._cloner()
        cloner.rate_limited.append(
            ("http://example.com/img/a.png", "HTTP 429 (limite de débit)"))
        cloner.protected.append(
            ("http://example.com/js/priv.js", "HTTP 401 (ressource protégée)"))
        cloner._write_placeholders()
        png = cloner.out_dir / "img" / "a.png"
        js = cloner.out_dir / "js" / "priv.js"
        self.assertTrue(png.is_file())
        self.assertEqual(png.read_bytes(), PNG_PLACEHOLDER)
        self.assertTrue(js.read_text("utf-8").startswith("/* spiderclone"))
        self.assertEqual(cloner.stats["placeholders"], 2)

    def test_write_placeholders_skip_existing(self):
        cloner = self._cloner()
        cloner.rate_limited.append(
            ("http://example.com/a.png", "HTTP 429 (limite de débit)"))
        dest = cloner.out_dir / "a.png"
        dest.write_bytes(b"REAL")
        cloner._write_placeholders()
        self.assertEqual(dest.read_bytes(), b"REAL")
        self.assertEqual(cloner.stats["placeholders"], 0)


class TestAdaptiveBackoff(unittest.TestCase):
    """v2.4.0 : backoff adaptatif si le taux de 429 dépasse le seuil."""

    def _cloner(self):
        return make_cloner(rate=0.0)

    def test_no_backoff_under_threshold(self):
        cloner = self._cloner()
        cloner._done_count = 20
        cloner.stats["rate_limited"] = 2
        cloner._maybe_adaptive_backoff()
        self.assertEqual(cloner.throttle.delay_for(cloner.base_host), 0.0)
        self.assertEqual(cloner.stats["adaptive_backoff"], 0)

    def test_backoff_over_threshold(self):
        cloner = self._cloner()
        cloner._done_count = 20
        cloner.stats["rate_limited"] = 10
        cloner._maybe_adaptive_backoff()
        self.assertGreater(cloner.throttle.delay_for(cloner.base_host), 0.0)
        self.assertEqual(cloner.stats["adaptive_backoff"], 1)

    def test_backoff_bounded(self):
        from cs_config import ADAPTIVE_MAX_DELAY
        cloner = self._cloner()
        cloner._done_count = 20
        cloner.stats["rate_limited"] = 20
        for _ in range(5):
            cloner._maybe_adaptive_backoff()
        self.assertLessEqual(cloner.throttle.delay_for(cloner.base_host),
                             ADAPTIVE_MAX_DELAY + 1e-9)

    def test_guard_small_done(self):
        cloner = self._cloner()
        cloner._done_count = 3
        cloner.stats["rate_limited"] = 3
        cloner._maybe_adaptive_backoff()
        self.assertEqual(cloner.throttle.delay_for(cloner.base_host), 0.0)


class TestApiEndpoints(unittest.TestCase):
    """v2.4.0 : endpoints API (fetch/XHR) détectés et planifiés."""

    def _cloner(self, **kw):
        return make_cloner(**kw)

    def test_queue_relative_endpoint(self):
        cloner = self._cloner()
        js = b'fetch("/api/data.json").then(r => r.json());'
        cloner._queue_api_endpoints(js, "http://example.com/app.js")
        self.assertIn("http://example.com/api/data.json", cloner.assets)
        self.assertEqual(
            cloner.assets["http://example.com/api/data.json"], "api/data.json")
        self.assertIn("http://example.com/api/data.json", cloner._seen)

    def test_xhr_endpoint(self):
        cloner = self._cloner()
        js = b'var x = new XMLHttpRequest(); x.open("GET", "/api/users");'
        cloner._queue_api_endpoints(js, "http://example.com/app.js")
        self.assertIn("http://example.com/api/users", cloner.assets)
        self.assertEqual(cloner.assets["http://example.com/api/users"],
                         "api/users.json")

    def test_out_of_scope_and_skips(self):
        cloner = self._cloner()
        js = b'fetch("https://autre-site.example/x");fetch("data:text/plain,hi");'
        cloner._queue_api_endpoints(js, "http://example.com/app.js")
        self.assertEqual(cloner.assets, {})

    def test_only_pages_guard(self):
        cloner = self._cloner(only_pages=True)
        cloner._queue_api_endpoints(b'fetch("/api/x")',
                                    "http://example.com/app.js")
        self.assertEqual(cloner.assets, {})

    def test_api_local_path_json(self):
        cloner = self._cloner()
        self.assertEqual(cloner._api_local_path("http://example.com/api/x"),
                         "api/x.json")
        self.assertEqual(
            cloner._api_local_path("http://example.com/api/data.json"),
            "api/data.json")


class TestParseMarkup(unittest.TestCase):
    """v2.5.1 : pages XML/XHTML traitées sans XMLParsedAsHTMLWarning."""

    def _cloner(self, **kw):
        return make_cloner(**kw)

    XML = (b'<?xml version="1.0" encoding="UTF-8"?>\n'
           b'<rss version="2.0"><channel><title>Flux</title>'
           b'<link>http://example.com/</link>'
           b'<item><title>Item</title>'
           b'<link>http://example.com/i</link></item>'
           b'</channel></rss>\n')

    def _xml_warns(self, fn):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            fn()
        from bs4 import XMLParsedAsHTMLWarning
        return [w for w in caught
                if issubclass(w.category, XMLParsedAsHTMLWarning)]

    def test_process_page_xml_no_warning(self):
        cloner = self._cloner()
        warns = self._xml_warns(
            lambda: cloner._process_page(self.XML,
                                         "http://example.com/feed.xml", 1))
        self.assertEqual(warns, [])
        self.assertEqual(cloner.pages["http://example.com/feed.xml"],
                         "feed.xml")
        self.assertTrue((cloner.out_dir / "feed.xml").is_file())
        self.assertEqual(cloner.stats["pages"], 1)

    def test_scan_page_xml_no_warning(self):
        cloner = self._cloner(only_assets=True)
        warns = self._xml_warns(
            lambda: cloner._scan_page_for_assets(
                self.XML, "http://example.com/feed.xml", 1))
        self.assertEqual(warns, [])
        self.assertEqual(cloner.stats["pages_scanned"], 1)

    def test_helper_xml_no_warning(self):
        from cs_crawl import _parse_markup
        warns = self._xml_warns(lambda: _parse_markup(self.XML))
        self.assertEqual(warns, [])


class TestOnlyModes(unittest.TestCase):
    """v2.4.0 : --only-assets (scan sans sauvegarde) / --only-pages."""

    def test_scan_page_for_assets_soups(self):
        cloner = make_cloner(only_assets=True)
        html = (b"<!DOCTYPE html><html><head></head><body>"
                b"<img src=\"logo.png\"><script src=\"app.js\"></script>"
                b"</body></html>")
        cloner._scan_page_for_assets(html, "http://example.com/", 0)
        self.assertEqual(cloner.stats["pages_scanned"], 1)
        self.assertNotIn("http://example.com/", cloner.pages)
        self.assertIn("http://example.com/logo.png", cloner.assets)

    def test_scan_page_for_assets_raw(self):
        cloner = make_cloner(only_assets=True, raw=True)
        html = (b"<!DOCTYPE html><html><head></head><body>"
                b"<img src=\"logo.png\"></body></html>")
        cloner._scan_page_for_assets(html, "http://example.com/", 0)
        self.assertEqual(cloner.stats["pages_scanned"], 1)
        self.assertIn("http://example.com/logo.png", cloner.assets)


class TestReport(unittest.TestCase):
    """v2.4.0 : rapport exportable spiderclone_report.txt."""

    def test_write_report(self):
        cloner = make_cloner()
        cloner.stats["pages"] = 2
        cloner.stats["assets"] = 5
        cloner.rate_limited.append(
            ("http://example.com/a.png", "HTTP 429 (limite de débit)"))
        cloner.failures.append(("http://example.com/b.js", "RuntimeError"))
        cloner._write_report(1.5)
        path = cloner.out_dir / REPORT_NAME
        self.assertTrue(path.is_file())
        text = path.read_text("utf-8")
        self.assertIn(f"SpiderClone {VERSION} — rapport de clone", text)
        self.assertIn("http://example.com/a.png", text)
        self.assertIn("1x RuntimeError", text)
        self.assertNotIn("Recovered", text)

    def test_write_report_empty_resources(self):
        cloner = make_cloner()
        cloner._write_report(0.1)
        text = (cloner.out_dir / REPORT_NAME).read_text("utf-8")
        self.assertIn("Statistiques :", text)
        self.assertNotIn("pages", text)
        self.assertIn("URL de départ :", text)


class TestScreenshots(unittest.TestCase):
    """v2.4.0 : captures finales, dégradation propre sans Chromium."""

    def test_no_pages_warns(self):
        cloner = make_cloner(screenshot=True)
        cloner._take_screenshots()
        self.assertEqual(cloner.stats["screenshots"], 0)

    def test_screenshots_mocked(self):
        cloner = make_cloner(screenshot=True)
        cloner.pages = {"http://example.com/": "index.html"}
        with mock.patch("cs_crawl.screenshot_pages", return_value=2) as m:
            cloner._take_screenshots()
        m.assert_called_once()
        self.assertEqual(cloner.stats["screenshots"], 2)

    def test_screenshots_zero_graceful(self):
        cloner = make_cloner(screenshot=True)
        cloner.pages = {"http://example.com/": "index.html"}
        with mock.patch("cs_crawl.screenshot_pages", return_value=0):
            cloner._take_screenshots()
        self.assertEqual(cloner.stats["screenshots"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
