#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Serveur HTTP local minimal pour les tests E2E : sert SITE_DIR et journalise chaque requete.
"""

import functools
import http.server
import sys
import threading


class LoggingHandler(http.server.SimpleHTTPRequestHandler):
    """SimpleHTTPRequestHandler qui journalise chaque requête.

    v2.4.0 — rate-limit simulé pour tester la 2e passe et les placeholders :
    - /flaky.css      : 429 sur les 3 PREMIERS GET (Retry-After: 0), puis 200 ;
    - /always429.css  : 429 en permanence (placeholder attendu en sortie).
    Les compteurs sont par chemin et persistent entre les runs E2E (le
    serveur n'est pas redémarré entre deux clones).
    """

    _lock = threading.Lock()
    _counters = {}
    _FLAKY = "/flaky.css"
    _ALWAYS = "/always429.css"

    def __init__(self, *args, log_path=None, **kwargs):
        self.log_path = log_path
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        pass

    def _log(self, method: str):
        if not self.log_path:
            return
        with self._lock:
            with open(self.log_path, "a", encoding="utf-8") as f:
                f.write(f"{method} {self.path}\n")

    def _intercept(self):
        """Rate-limit simulé. Retourne un statut HTTP (int) à émettre sans
        servir le fichier, ou None pour un service normal."""
        path = self.path.split("?")[0]
        with self._lock:
            n = self._counters.get(path, 0) + 1
            self._counters[path] = n
            if path == self._FLAKY and n <= 3:
                return 429
            if path == self._ALWAYS:
                return 429
        return None

    def do_GET(self):
        self._log("GET")
        status = self._intercept()
        if status is not None:
            self.send_response(status)
            self.send_header("Retry-After", "0")
            self.end_headers()
            return
        super().do_GET()

    def do_HEAD(self):
        self._log("HEAD")
        super().do_HEAD()


def main():
    port = int(sys.argv[1])
    site_dir = sys.argv[2]
    log_path = sys.argv[3]
    handler = functools.partial(LoggingHandler, directory=site_dir,
                                log_path=log_path)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
