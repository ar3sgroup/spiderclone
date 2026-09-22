#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Publication d'un clone : serveur HTTP local (repli SPA) et tunnels ngrok / Cloudflare (Quick et nomme) avec repli local.
"""

import http.server
import json
import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import cs_ui
from cs_config import DEFAULT_PORT, TUNNEL_CHOICES

MIME_OVERRIDES = {
    ".html": "text/html", ".htm": "text/html",
    ".css": "text/css",
    ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".map": "application/json",
    ".webmanifest": "application/manifest+json",
    ".svg": "image/svg+xml", ".webp": "image/webp",
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".bmp": "image/bmp", ".ico": "image/x-icon",
    ".woff": "font/woff", ".woff2": "font/woff2",
    ".ttf": "font/ttf", ".otf": "font/otf", ".eot": "application/vnd.ms-fontobject",
    ".mp4": "video/mp4", ".webm": "video/webm",
    ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg",
    ".pdf": "application/pdf",
    ".txt": "text/plain", ".csv": "text/csv", ".md": "text/markdown",
    ".xml": "application/xml",
}


class TunnelError(RuntimeError):
    """Tunnel demandé indisponible : binaire absent ou lancement en échec."""


INSTALL_HINTS = {
    "ngrok": "winget install ngrok.ngrok  (ou https://ngrok.com/download)",
    "cloudflare": "winget install Cloudflare.cloudflared  (ou "
                  "https://developers.cloudflare.com/cloudflare-one/"
                  "connections/connect-networks/downloads/)",
}

NGROK_API = "http://127.0.0.1:4040/api/tunnels"
NGROK_TIMEOUT = 20.0
CLOUDFLARED_TIMEOUT = 30.0
CLOUDFLARED_NAMED_GRACE = 5.0
CLOUDFLARED_URL_RE = re.compile(r"https://[A-Za-z0-9-]+\.trycloudflare\.com")


def extract_cloudflared_url(text: str) -> str | None:
    """Extrait l'URL Quick Tunnel (trycloudflare.com) d'une sortie cloudflared."""
    m = CLOUDFLARED_URL_RE.search(text or "")
    return m.group(0) if m else None


class CloneHandler(http.server.SimpleHTTPRequestHandler):
    """Sert un dossier cloné ; repli SPA pour les routes sans extension.

    Une URL introuvable SANS extension de fichier (ex: /profil/xyz) retombe
    sur index.html si le clone en possède un : les sites à routage
    client-side restent navigables derrière le tunnel. Une ressource
    manquante AVEC extension (ex: /logo.png) reste un 404 normal.

    v2.5.1 — méthodes d'écriture (POST/PUT/PATCH/DELETE) : les SPA font des
    appels API relatifs (ex: /ajax/bz, /api/graphql) qui tombaient en 501
    « Unsupported method » (corps HTML), faisant échouer le parseur JSON du
    site. Désormais : si un asset du clone correspond au chemin (endpoint
    API cloné via la v2.4.0), il est servi ; sinon une réponse JSON vide est
    renvoyée (mock_api=True) pour que le JS ne casse pas sur un corps HTML.
    """

    spa_root = None
    verbose = False
    mock_api = True
    mock_payload = b"{}"

    def log_message(self, fmt, *args):
        if self.verbose:
            line = fmt % args
            print("   serve %s - %s" % (self.address_string(), line),
                  file=sys.stderr)

    def guess_type(self, path):
        ext = os.path.splitext(path)[1].lower()
        if ext in MIME_OVERRIDES:
            return MIME_OVERRIDES[ext]
        return super().guess_type(path)

    def send_head(self):
        path = self.translate_path(self.path)
        if (self.spa_root and not os.path.exists(path)
                and not os.path.splitext(path)[1]
                and os.path.isfile(os.path.join(self.spa_root, "index.html"))):
            self.path = "/index.html"
        return super().send_head()

    def _drain_body(self):
        """Lit le corps de la requête (keep-alive : ne pas laisser de bytes
        en attente dans la connexion avant de répondre)."""
        try:
            length = int(self.headers.get("Content-Length", 0) or 0)
        except (TypeError, ValueError):
            length = 0
        if length > 0:
            self.rfile.read(min(length, 1 << 20))

    def _answer_write_method(self):
        """Réponse aux méthodes d'écriture (POST/PUT/PATCH/DELETE).

        Priorité : 1) asset du clone présent pour ce chemin (endpoint API
        cloné) ; 2) mock JSON (mock_api=True) ; 3) 501 explicite.
        """
        self._drain_body()
        path = self.translate_path(self.path)
        # Les endpoints API clonés sont stockés avec un suffixe .json
        # (ex: /api/graphql -> api/graphql.json) : on essaie les deux.
        candidates = [path]
        if not os.path.splitext(path)[1]:
            candidates.append(path + ".json")
        for cand in candidates:
            if os.path.isfile(cand):
                try:
                    with open(cand, "rb") as f:
                        body = f.read()
                except OSError:
                    body = b""
                self.send_response(200)
                self.send_header("Content-Type", self.guess_type(cand))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        if self.mock_api:
            if self.verbose:
                print("   serve mock %s %s -> %r"
                      % (self.command, self.path, self.mock_payload),
                      file=sys.stderr)
            body = self.mock_payload
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_error(501, f"Unsupported method ({self.command!r})")

    def do_POST(self):
        self._answer_write_method()

    def do_PUT(self):
        self._answer_write_method()

    def do_PATCH(self):
        self._answer_write_method()

    def do_DELETE(self):
        self._answer_write_method()

    def do_OPTIONS(self):
        """Pré-vol CORS éventuel : 204 avec les méthodes acceptées."""
        self._drain_body()
        self.send_response(204)
        self.send_header("Allow",
                         "GET, HEAD, POST, PUT, PATCH, DELETE, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()


def find_free_port(preferred: int = DEFAULT_PORT) -> int:
    """Port libre : *preferred* s'il est disponible, sinon un port aléatoire."""
    try:
        preferred = int(preferred)
    except (TypeError, ValueError):
        preferred = DEFAULT_PORT
    if preferred <= 0:
        preferred = DEFAULT_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_handler(root: str, is_verbose: bool):
    """Fabrique une sous-classe de CloneHandler configurée pour un dossier.

    (SimpleHTTPRequestHandler.__init__ n'accepte que le kwarg `directory` :
    spa_root et verbose sont donc portés par des attributs de classe.
    Attention : le paramètre s'appelle is_verbose car dans un corps de
    classe, `verbose = verbose` lèverait un NameError.)
    """
    class _ConfiguredHandler(CloneHandler):
        spa_root = root
        verbose = is_verbose

        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=root, **kwargs)
    return _ConfiguredHandler


def serve_directory(out_dir, port: int = DEFAULT_PORT, verbose: bool = False):
    """Démarre un ThreadingHTTPServer sur le dossier cloné (thread daemon).

    Renvoie le serveur déjà en écoute (thread non bloquant) ; le port
    réellement utilisé est httpd.server_address[1] (un autre port est pris
    si *port* est occupé). Arrêt via `stop(server, None)`.
    """
    root = str(os.path.abspath(out_dir))
    if not os.path.isdir(root):
        raise ValueError(f"Dossier introuvable : {out_dir}")
    handler = _make_handler(root, verbose)
    try:
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError:
        httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", find_free_port(port)), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True,
                         name="spiderclone-serve")
    t.start()
    return httpd


def extract_ngrok_url(api_text: str) -> str | None:
    """Extrait l'URL publique https du JSON de l'API locale ngrok."""
    try:
        data = json.loads(api_text)
    except ValueError:
        return None
    for tunnel in data.get("tunnels", []):
        url = tunnel.get("public_url", "")
        if url.startswith("https://"):
            return url
    return None


def _read_lines(stream, out: queue.Queue):
    """Pousse chaque ligne d'un flux dans une queue (None pour la fin)."""
    try:
        for line in stream:
            out.put(line)
    finally:
        out.put(None)


def start_ngrok(port: int, verbose: bool = False):
    """Lance `ngrok http PORT` et récupère l'URL publique via l'API locale.

    Renvoie (Popen, URL https). Lève TunnelError si le binaire manque ou si
    l'URL n'apparaît pas dans le délai NGROK_TIMEOUT.
    """
    try:
        proc = subprocess.Popen(
            ["ngrok", "http", str(port), "--log", "stderr",
             "--log-format", "logfmt"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        raise TunnelError(
            "ngrok introuvable. Installez-le : " + INSTALL_HINTS["ngrok"]) from None
    deadline = time.time() + NGROK_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            raise TunnelError(
                f"ngrok s'est arrêté (code {proc.returncode}) — token manquant ? "
                "Définissez NGROK_AUTHTOKEN ou lancez : ngrok config "
                "add-authtoken <TOKEN>")
        try:
            with urllib.request.urlopen(NGROK_API, timeout=2) as resp:
                url = extract_ngrok_url(
                    resp.read(65536).decode("utf-8", "replace"))
            if url:
                return proc, url
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    proc.terminate()
    raise TunnelError("URL ngrok non obtenue en "
                      f"{NGROK_TIMEOUT:.0f} s (l'API locale sur le port 4040 "
                      "ne répond pas ?)")


def start_cloudflared(port: int, verbose: bool = False):
    """Lance un Quick Tunnel Cloudflare (`cloudflared tunnel --url ...`).

    Renvoie (Popen, URL trycloudflare.com). Lève TunnelError si le binaire
    manque ou si l'URL n'apparaît pas dans le délai CLOUDFLARED_TIMEOUT.
    """
    try:
        proc = subprocess.Popen(
            ["cloudflared", "tunnel", "--url", f"http://127.0.0.1:{port}",
             "--no-autoupdate"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise TunnelError(
            "cloudflared introuvable. Installez-le : "
            + INSTALL_HINTS["cloudflare"]) from None
    lines = queue.Queue()
    reader = threading.Thread(target=_read_lines, args=(proc.stdout, lines),
                              daemon=True, name="cloudflared-reader")
    reader.start()
    deadline = time.time() + CLOUDFLARED_TIMEOUT
    while True:
        try:
            line = lines.get(timeout=0.5)
        except queue.Empty:
            if proc.poll() is not None:
                break
            if time.time() > deadline:
                break
            continue
        if line is None:
            break
        url = extract_cloudflared_url(line)
        if url:
            return proc, url
    proc.terminate()
    raise TunnelError("URL Cloudflare non obtenue en "
                      f"{CLOUDFLARED_TIMEOUT:.0f} s — cloudflared a-t-il "
                      "réussi à démarrer ?")


def build_cloudflared_named_args(port: int, token: str | None = None,
                                 tunnel_name: str | None = None,
                                 config: str | None = None) -> list:
    """Construit la ligne de commande `cloudflared` d'un tunnel nommé.

    Trois modes (mutuellement prioritaires, dans cet ordre) :
      1. token        : `cloudflared ... tunnel run --token TOK`
         (tunnel distant déjà créé sur le dashboard Cloudflare) ;
      2. tunnel_name  : `cloudflared ... tunnel run --url LOCAL NAME`
         (tunnel local existant — credentials du compte déjà installés) ;
      3. config       : `cloudflared ... tunnel --config FILE run`
         (fichier config.yml décrivant ingress et credentials).

    Lève ValueError si aucun réglage n'est fourni.
    """
    base = ["cloudflared", "--no-autoupdate", "tunnel"]
    if token:
        return base + ["run", "--token", str(token)]
    if tunnel_name:
        return base + ["run", "--url", f"http://127.0.0.1:{port}",
                       str(tunnel_name)]
    if config:
        return base + ["--config", str(config), "run"]
    raise ValueError("Tunnel nommé Cloudflare : fournir --cf-token, "
                     "--cf-tunnel ou --cf-config")


def start_cloudflared_named(port: int, token: str | None = None,
                            tunnel_name: str | None = None,
                            config: str | None = None,
                            verbose: bool = False):
    """Lance un tunnel Cloudflare NOMMÉ (domaine fixe du compte).

    Contrairement au Quick Tunnel, l'URL publique n'est pas affichée par
    cloudflared : c'est le domaine DNS configuré sur le compte (via
    `cloudflared tunnel route dns ...`). On renvoie donc (Popen, None) dès
    que le process est vivant après la fenêtre de grâce.

    Lève ValueError si aucun réglage, TunnelError si le binaire manque ou
    si le process s'arrête dans le délai CLOUDFLARED_NAMED_GRACE.
    """
    args = build_cloudflared_named_args(port, token, tunnel_name, config)
    try:
        proc = subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace")
    except FileNotFoundError:
        raise TunnelError(
            "cloudflared introuvable. Installez-le : "
            + INSTALL_HINTS["cloudflare"]) from None
    try:
        proc.wait(timeout=CLOUDFLARED_NAMED_GRACE)
    except subprocess.TimeoutExpired:
        return proc, None
    raise TunnelError(
        f"cloudflared (tunnel nommé) s'est arrêté (code {proc.returncode}) "
        "— token/credentials invalides ou tunnel inexistant ?")


def publish(out_dir, tunnel: str = "none", port: int = DEFAULT_PORT,
            verbose: bool = False, block: bool = True,
            cf_token: str | None = None, cf_tunnel: str | None = None,
            cf_config: str | None = None, fallback: bool = True):
    """Sert un dossier cloné et, si demandé, l'expose au public.

    block=True  : imprime les URLs puis attend Ctrl+C (arrêt propre) ;
    block=False : renvoie (server, proc, url) sans bloquer (tests/usage
    programmatique — arrêter ensuite avec `stop(server, proc)`).

    tunnel="cloudflare-named" : cf_token / cf_tunnel / cf_config doivent
    être fournis (au moins un), sinon ValueError.

    fallback=True (défaut) : si le binaire du tunnel est absent ou ne
    démarre pas (TunnelError), avertir et publier en LOCAL uniquement.
    fallback=False : re-lever le TunnelError (serveur local arrêté).

    Renvoie (ThreadingHTTPServer, Popen|None, str|None).
    """
    if tunnel not in TUNNEL_CHOICES:
        raise ValueError(f"Tunnel inconnu : {tunnel!r} (choix : "
                         + ", ".join(TUNNEL_CHOICES) + ")")
    if tunnel == "cloudflare-named":
        build_cloudflared_named_args(port, cf_token, cf_tunnel, cf_config)
    if not os.path.isdir(out_dir):
        raise ValueError(f"Dossier introuvable : {out_dir}")
    server = serve_directory(out_dir, port, verbose)
    port = server.server_address[1]
    proc, public = None, None

    def _start(label, fn):
        """Démarre le tunnel ; en échec, repli local (fallback) sinon raise."""
        nonlocal proc, public
        try:
            proc, public = fn()
        except TunnelError as exc:
            if not fallback:
                stop(server, None)
                raise
            print(cs_ui.warn(f"{label} indisponible ({exc})"))
            print(cs_ui.info("Publication en LOCAL uniquement (serveur "
                             "http://127.0.0.1:%d)." % port))

    if tunnel == "ngrok":
        print(cs_ui.step("Ouverture du tunnel ngrok (URL publique en "
                         "quelques secondes)..."))
        _start("ngrok", lambda: start_ngrok(port, verbose))
    elif tunnel == "cloudflare":
        print(cs_ui.step("Ouverture du Quick Tunnel Cloudflare (URL publique "
                         "en quelques secondes)..."))
        _start("cloudflared", lambda: start_cloudflared(port, verbose))
    elif tunnel == "cloudflare-named":
        print(cs_ui.step("Ouverture du tunnel nommé Cloudflare..."))
        _start("cloudflared (nommé)",
               lambda: start_cloudflared_named(port, cf_token, cf_tunnel,
                                               cf_config, verbose))

    print()
    print(cs_ui.step(f"Site publié : {os.path.abspath(out_dir)}"))
    if public:
        print(cs_ui.step(f"URL publique : {public}"))
    elif proc is not None and tunnel == "cloudflare-named":
        print(cs_ui.step("Tunnel nommé actif : URL = domaine DNS configuré "
                         "sur votre compte Cloudflare (ex: "
                         "https://mon-tunnel.mon-domaine.com)"))
    print(cs_ui.step(f"URL locale  : http://127.0.0.1:{port}"))
    print(cs_ui.info("Ctrl+C pour arrêter la publication."))

    if not block:
        return server, proc, public
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n" + cs_ui.info("Arrêt de la publication..."))
    finally:
        stop(server, proc)
    return None, None, None


def stop(server, proc=None):
    """Arrêt propre : tunnel puis serveur."""
    if proc is not None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
            except OSError:
                pass
    if server is not None:
        try:
            server.shutdown()
        except OSError:
            pass
        server.server_close()
