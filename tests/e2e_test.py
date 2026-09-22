#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Test E2E : clone complet d'un site de test local via la CLI, avec verification des fichiers et des reecritures.
"""

import base64
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI = ROOT / "spiderclone.py"
SERVER = Path(__file__).resolve().parent / "test_server.py"

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

EXPECTED_FILES = [
    "index.html", "page2.html", "page3.html",
    "styles.css", "extra.css",
    "app.js", "chunk.js",
    "logo.png", "logo_big.png",
    "flaky.css", "always429.css",
    os.path.join("api", "data.json"),
]

EXPECTED_GETS = [
    "/", "/page2.html", "/page3.html", "/styles.css", "/extra.css",
    "/chunk.js", "/app.js", "/logo.png", "/logo_big.png",
    "/flaky.css", "/always429.css", "/api/data.json",
]

RATE_LIMITED_PATHS = {"/flaky.css", "/always429.css"}


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def build_site(site_dir: Path):
    (site_dir / "index.html").write_text(
        """<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>Accueil</title>
<link rel="stylesheet" href="styles.css">
<link rel="stylesheet" href="flaky.css">
<link rel="stylesheet" href="always429.css"></head>
<body>
<a href="page2.html">Page 2</a>
<a href="page3.html">Page 3</a>
<a href="https://externe.example/track">Externe</a>
<img src="logo.png" alt="logo">
<img srcset="logo.png 1x, logo_big.png 2x" alt="logo2">
<script src="app.js"></script>
</body>
</html>
""", encoding="utf-8")
    (site_dir / "page2.html").write_text(
        """<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>Page 2</title></head>
<body><a href="page3.html">Page 3 (hors profondeur depuis ici)</a></body>
</html>
""", encoding="utf-8")
    (site_dir / "page3.html").write_text(
        """<!DOCTYPE html>
<html lang="fr">
<head><meta charset="utf-8"><title>Page 3</title></head>
<body>Page 3</body>
</html>
""", encoding="utf-8")
    (site_dir / "styles.css").write_text(
        '@import "extra.css" screen and (min-width: 600px);\n'
        "body { background: url(logo.png); }\n", encoding="utf-8")
    (site_dir / "extra.css").write_text("p { color: red; }\n", encoding="utf-8")
    (site_dir / "app.js").write_text(
        'import React from "react";\n'
        'const url = new URL("./chunk.js", import.meta.url);\n'
        'fetch("/api/data.json").then(r => r.json());\n'
        "console.log(url);\n", encoding="utf-8")
    (site_dir / "chunk.js").write_text("export const x = 1;\n", encoding="utf-8")
    (site_dir / "logo.png").write_bytes(PNG_1PX)
    (site_dir / "logo_big.png").write_bytes(PNG_1PX)
    (site_dir / "flaky.css").write_text(
        "/* flaky.css — récupérée en 2e passe */\n.flaky { color: teal; }\n",
        encoding="utf-8")
    (site_dir / "always429.css").write_text(
        "/* always429.css — jamais servie */\n.a429 { color: red; }\n",
        encoding="utf-8")
    (site_dir / "api").mkdir()
    (site_dir / "api" / "data.json").write_text(
        '{"status": "ok"}\n', encoding="utf-8")


def wait_server(url: str, timeout: float = 10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(url, timeout=1) as r:
                r.read()
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"serveur de test injoignable : {url}")


def main() -> int:
    keep = "--keep" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="e2e_spiderclone_"))
    site_dir = tmp / "site"
    out_dir = tmp / "out"
    site_dir.mkdir()
    build_site(site_dir)

    port = free_port()
    log_path = tmp / "reqs.log"
    server = subprocess.Popen(
        [sys.executable, str(SERVER), str(port), str(site_dir), str(log_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    proc = None
    failures = []

    try:
        wait_server(url)
        log_path.write_text("", encoding="utf-8")

        cmd = [sys.executable, str(CLI), "-o", str(out_dir), "-d", "1",
               "-w", "2", "--no-check", url]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True,
                                  timeout=180, cwd=str(ROOT))
        except subprocess.TimeoutExpired:
            failures.append("CLI en timeout (180 s)")

        if proc is not None and proc.returncode != 0:
            failures.append(f"CLI exit code {proc.returncode}")

        for name in EXPECTED_FILES:
            if not (out_dir / name).is_file():
                failures.append(f"fichier manquant : {name}")

        p2 = (out_dir / "page2.html").read_text(encoding="utf-8",
                                                errors="replace")
        if f"http://127.0.0.1:{port}/page3.html" not in p2:
            failures.append("page2.html : lien page3 non réécrit en URL "
                            "absolue live")

        css = (out_dir / "styles.css").read_text(encoding="utf-8",
                                                 errors="replace")
        if '@import "extra.css" screen and (min-width: 600px);' not in css:
            failures.append("styles.css : @import avec media query modifié")
        if "url(logo.png)" not in css:
            failures.append("styles.css : url(logo.png) modifié")

        js = (out_dir / "app.js").read_text(encoding="utf-8", errors="replace")
        if 'new URL("chunk.js", import.meta.url)' not in js:
            failures.append("app.js : new URL mal réécrit")
        if 'import React from "react";' not in js:
            failures.append("app.js : specifier npm 'react' modifié")

        idx = (out_dir / "index.html").read_text(encoding="utf-8",
                                                 errors="replace")
        if "https://externe.example/track" not in idx:
            failures.append("index.html : lien externe modifié")

        if log_path.exists():
            lines = [ln.strip() for ln in
                     log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
            paths = [g.split(" ", 1)[1] for g in lines if g.startswith("GET ")]
            dupes = sorted({p for p in paths
                            if paths.count(p) > 1
                            and p not in RATE_LIMITED_PATHS})
            missing = sorted(set(EXPECTED_GETS) - set(paths))
            if dupes:
                failures.append(f"URLs demandées plusieurs fois : {dupes}")
            if missing:
                failures.append(f"URLs jamais demandées : {missing}")
            if any(not g.startswith("GET ") for g in lines):
                nb = [g for g in lines if not g.startswith("GET ")]
                failures.append(f"requêtes non-GET inattendues : {nb[:10]}")
        else:
            failures.append("log serveur absent")

        out2 = tmp / "out_interactive"
        cmd2 = [sys.executable, str(CLI), "-o", str(out2), "-d", "1",
                "-w", "2", "--no-check"]
        try:
            proc2 = subprocess.run(cmd2, capture_output=True, text=True,
                                   timeout=180, cwd=str(ROOT),
                                   input=f"copy {url}\nexit\n")
        except subprocess.TimeoutExpired:
            proc2 = None
            failures.append("CLI interactive en timeout (180 s)")
        if proc2 is not None and proc2.returncode != 0:
            failures.append(f"CLI interactive exit code {proc2.returncode}")
        for name in EXPECTED_FILES:
            if not (out2 / name).is_file():
                failures.append(f"mode interactif : fichier manquant : {name}")
        if proc2 is not None and "user@spiderclone:~$" not in (proc2.stdout or ""):
            failures.append("mode interactif : invite user@spiderclone:~$ absente")

        report = out_dir / "spiderclone_report.txt"
        if not report.is_file():
            failures.append("spiderclone_report.txt absent")
        else:
            rt = report.read_text(encoding="utf-8", errors="replace")
            if "2.5.1" not in rt:
                failures.append("rapport : version 2.5.1 absente")
            if "/always429.css" not in rt:
                failures.append("rapport : always429.css absent de la liste "
                                "des non téléchargées")
            if "/flaky.css" in rt:
                failures.append("rapport : flaky.css listée à tort "
                                "(récupérée en 2e passe)")
        flaky = out_dir / "flaky.css"
        if not flaky.is_file():
            failures.append("flaky.css absente (2e passe ?)")
        else:
            ftext = flaky.read_text(encoding="utf-8", errors="replace")
            if ftext.startswith("/* spiderclone"):
                failures.append("flaky.css : placeholder au lieu du contenu "
                                "réel (2e passe a échoué)")
            elif ".flaky" not in ftext:
                failures.append("flaky.css : contenu inattendu")
        always = out_dir / "always429.css"
        if not always.is_file():
            failures.append("always429.css absente (placeholder attendu)")
        elif not always.read_text(encoding="utf-8",
                                  errors="replace").startswith("/* spiderclone"):
            failures.append("always429.css : placeholder non écrit")
        api = out_dir / "api" / "data.json"
        if not api.is_file():
            failures.append("api/data.json absent (endpoint API non détecté)")
        elif "\"status\"" not in api.read_text(encoding="utf-8",
                                                 errors="replace"):
            failures.append("api/data.json : contenu inattendu")
        if "2e passe" not in (proc.stdout or ""):
            failures.append("stdout : « 2e passe » non affichée")

        out3 = tmp / "out_only_pages"
        proc3 = subprocess.run(
            [sys.executable, str(CLI), "-o", str(out3), "-d", "1",
             "-w", "2", "--no-check", "--only-pages", url],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        if proc3.returncode != 0:
            failures.append(f"--only-pages : exit code {proc3.returncode}")
        if not (out3 / "index.html").is_file():
            failures.append("--only-pages : index.html manquant")
        for extra in ("logo.png", "app.js", "styles.css"):
            if (out3 / extra).exists():
                failures.append(f"--only-pages : ressource {extra} "
                                "injustement sauvegardée")

        out4 = tmp / "out_only_assets"
        proc4 = subprocess.run(
            [sys.executable, str(CLI), "-o", str(out4), "-d", "1",
             "-w", "2", "--no-check", "--only-assets", url],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        if proc4.returncode != 0:
            failures.append(f"--only-assets : exit code {proc4.returncode}")
        if (out4 / "index.html").exists():
            failures.append("--only-assets : page index.html injustement "
                            "sauvegardée")
        for asset in ("logo.png", "app.js", "styles.css",
                      os.path.join("api", "data.json")):
            if not (out4 / asset).is_file():
                failures.append(f"--only-assets : ressource {asset} manquante")

        out5 = tmp / "out_screenshot"
        proc5 = subprocess.run(
            [sys.executable, str(CLI), "-o", str(out5), "-d", "1",
             "-w", "2", "--no-check", "--screenshot", url],
            capture_output=True, text=True, timeout=180, cwd=str(ROOT))
        if proc5.returncode != 0:
            failures.append(f"--screenshot : exit code {proc5.returncode}")
        if "capture" not in (proc5.stdout or "").lower():
            failures.append("--screenshot : ni capture ni dégradation signalées")

        sport = free_port()
        sproc = subprocess.Popen(
            [sys.executable, str(CLI), "--serve-dir", str(out_dir),
             "--no-banner", "--no-color", "--port", str(sport)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            cwd=str(ROOT))
        served = False
        try:
            deadline = time.time() + 25
            while time.time() < deadline and not served:
                if sproc.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{sport}/index.html",
                            timeout=2) as resp:
                        body = resp.read().decode("utf-8", "replace")
                    if "<html" in body.lower():
                        served = True
                except (urllib.error.URLError, OSError):
                    pass
                time.sleep(0.5)
            if not served:
                failures.append("--serve-dir : index.html non servi (25 s)")
            else:
                try:
                    with urllib.request.urlopen(
                            f"http://127.0.0.1:{sport}/route/inconnue",
                            timeout=2) as resp2:
                        body2 = resp2.read().decode("utf-8", "replace")
                except (urllib.error.URLError, OSError):
                    body2 = ""
                if "<html" not in body2.lower():
                    failures.append("--serve-dir : repli SPA inactif "
                                    "(route inconnue -> index.html)")
        finally:
            sproc.terminate()
            try:
                sproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sproc.kill()

    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()

    if failures:
        print("ÉCHEC E2E :")
        for f in failures:
            print(f"  - {f}")
        if proc is not None:
            print("\n--- stdout CLI ---")
            print((proc.stdout or "")[:4000])
            print("--- stderr CLI ---")
            print((proc.stderr or "")[:4000])
        print(f"\nDossier de test conservé : {tmp}")
        return 1

    shutil.rmtree(tmp, ignore_errors=True)
    print("E2E OK : tous les fichiers clonés, liens réécrits, aucun double "
          "GET, 2e passe/placeholders/rapport/API/modes v2.4.0 et "
          "publication --serve-dir v2.5.1 validés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
