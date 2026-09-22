#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Point d'entree CLI : argparse, orchestration du clone, mode interactif et publication (serveur + tunnels).
"""

import argparse
import json
import re
import sys
import urllib.parse
from pathlib import Path

from cs_config import (DEFAULT_MAX_SIZE, DEFAULT_PORT, TUNNEL_CHOICES,
                       USER_AGENT, VERSION)
from cs_crawl import SiteClone
from cs_serve import publish
import cs_ui

DEFAULTS = dict(
    output=None, depth=3, workers=8, include_subdomains=False, external=False,
    delay=0.0, max_size=DEFAULT_MAX_SIZE, user_agent=USER_AGENT, verbose=False,
    raw=False, render=False, cookies=None, cookie_file=None, header=None,
    proxy=None, zip=None, menu=False, check=True, resume=False,
    respect_robots=False,
    sitemap=None, rate=0.0, max_urls=0, max_time=0,
    no_banner=False, no_color=False,
    only_assets=False, only_pages=False, screenshot=False,
    serve=False, tunnel="none", port=DEFAULT_PORT, serve_dir=None,
    cf_token=None, cf_tunnel=None, cf_config=None,
)


def _reconfigure_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spiderclone",
        description="Clone complet d'un site web en local (HTML, CSS, JS, images, polices...).",
        epilog="Exemples : python spiderclone.py https://exemple.fr "
               "| --raw --zip clone.zip | --sitemap --respect-robots | --resume",
    )
    parser.add_argument("url", nargs="?", default=None,
                        help="URL de départ du site à cloner (ex: https://exemple.fr) — "
                             "sans URL : mode interactif (copy <site>)")
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
    parser.add_argument("--proxy", default=None, metavar="URL",
                        help="Proxy HTTP/SOCKS pour les requêtes "
                             "(ex: http://127.0.0.1:8080, socks5://127.0.0.1:9050)")
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
    parser.add_argument("--only-assets", action="store_true", default=None,
                        help="v2.4.0 — télécharger uniquement les ressources "
                             "(CSS/JS/images), pas les pages HTML")
    parser.add_argument("--only-pages", action="store_true", default=None,
                        help="v2.4.0 — télécharger uniquement les pages HTML, "
                             "ignorer les assets")
    parser.add_argument("--screenshot", action="store_true", default=None,
                        help="v2.4.0 — captures d'écran finales des pages "
                             "clonées (Chromium headless, dossier captures/)")
    parser.add_argument("--serve", action="store_true", default=None,
                        help="v2.5.0 — publier le clone : serveur HTTP local "
                             "(bloquant, Ctrl+C pour arrêter)")
    parser.add_argument("--tunnel", default=None, choices=list(TUNNEL_CHOICES),
                        metavar="{none,ngrok,cloudflare,cloudflare-named}",
                        help="v2.5.0 — exposer le serveur local au public "
                             "(ngrok, Quick Tunnel Cloudflare ou tunnel "
                             "nommé Cloudflare ; implique --serve)")
    parser.add_argument("--cf-token", default=None, metavar="TOKEN",
                        help="v2.5.0 — tunnel nommé Cloudflare : token du "
                             "tunnel distant (dashboard Cloudflare)")
    parser.add_argument("--cf-tunnel", default=None, metavar="NOM",
                        help="v2.5.0 — tunnel nommé Cloudflare : nom du "
                             "tunnel local déjà créé (credentials installés)")
    parser.add_argument("--cf-config", default=None, metavar="FICHIER.yml",
                        help="v2.5.0 — tunnel nommé Cloudflare : fichier "
                             "config.yml (ingress + credentials)")
    parser.add_argument("--port", type=int, default=None,
                        help="v2.5.0 — port du serveur local (défaut : 8000)")
    parser.add_argument("--serve-dir", default=None, metavar="DOSSIER",
                        help="v2.5.0 — publier un dossier déjà cloné sans "
                             "re-cloner (avec --tunnel / --port)")
    parser.add_argument("--config", default=None, metavar="FICHIER.json",
                        help="Fichier de configuration JSON (valeurs par défaut)")
    parser.add_argument("--no-banner", action="store_true", default=None,
                        help="Ne pas afficher la bannière ASCII au démarrage")
    parser.add_argument("--no-color", action="store_true", default=None,
                        help="Désactiver les couleurs ANSI dans la sortie")
    parser.add_argument("-v", "--verbose", action="store_true", default=None,
                        help="Afficher chaque fichier téléchargé")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def _normalize_url(site: str) -> str:
    """Ajoute https:// si le schéma manque (ex: exemple.fr -> https://exemple.fr)."""
    site = site.strip()
    if "://" not in site:
        site = "https://" + site
    return site


def _load_config(path):
    """Charge et valide le fichier de configuration JSON ({} si absent)."""
    if not path:
        return {}
    try:
        cfg = json.loads(Path(path).read_text("utf-8"))
    except (OSError, ValueError) as e:
        print(cs_ui.warn(f"Fichier de configuration illisible : {e}"),
              file=sys.stderr)
        sys.exit(2)
    unknown = sorted(set(cfg) - set(DEFAULTS))
    if unknown:
        print(cs_ui.warn(f"Clés inconnues dans {path} "
                         f"(ignorées) : {', '.join(unknown)}"),
              file=sys.stderr)
    return cfg


def run_clone(start_url: str, args, cfg: dict) -> None:
    """Exécute un clone complet pour une URL (une passe)."""
    final = dict(DEFAULTS)
    final.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    final.update({k: v for k, v in vars(args).items()
                  if v is not None and k not in ("config", "url")})

    for k in ("depth", "workers", "max_size", "max_urls", "max_time"):
        final[k] = int(final[k])
    for k in ("delay", "rate"):
        final[k] = float(final[k])
    final["port"] = int(final["port"])

    output = final["output"]
    if not output:
        host = urllib.parse.urlparse(start_url).netloc.replace(":", "_")
        output = re.sub(r"[^A-Za-z0-9_.-]", "_", host)

    cloner = SiteClone(
        start_url, output, max_depth=final["depth"], workers=final["workers"],
        include_subdomains=final["include_subdomains"], external=final["external"],
        user_agent=final["user_agent"], delay=final["delay"],
        max_size=final["max_size"], verbose=final["verbose"],
        raw=final["raw"], render=final["render"],
        cookies=final["cookies"], cookie_file=final["cookie_file"],
        headers=final["header"], proxy=final["proxy"],
        check=final["check"], resume=final["resume"],
        respect_robots=final["respect_robots"], sitemap=final["sitemap"],
        zip_file=final["zip"], menu=final["menu"],
        rate=final["rate"], max_urls=final["max_urls"], max_time=final["max_time"],
        only_assets=final["only_assets"], only_pages=final["only_pages"],
        screenshot=final["screenshot"],
    )
    cloner.run()

    if final["serve"] or final["tunnel"] != "none":
        publish(output, final["tunnel"], final["port"], final["verbose"],
                cf_token=final["cf_token"], cf_tunnel=final["cf_tunnel"],
                cf_config=final["cf_config"])


COPY_RE = re.compile(r"\s*(?:copy|clone)\s+(.+?)\s*$", re.IGNORECASE)
COPY_VERB_RE = re.compile(r"\s*(?:copy|clone)\b", re.IGNORECASE)
SERVE_RE = re.compile(r"\s*(?:serve|publish)\s+(.+?)\s*$", re.IGNORECASE)
SERVE_VERB_RE = re.compile(r"\s*(?:serve|publish)\b", re.IGNORECASE)

INTERACTIVE_HELP = """\
Commandes :
  copy <site>    Clone un site (ex: copy https://exemple.fr)
  clone <site>   Alias de copy
  serve <dir> [tunnel]
                 Publie un dossier cloné (tunnel : ngrok, cloudflare,
                 cloudflare-named, none — réglages nommé : cf-token/cf-tunnel/
                 cf-config via la ligne de commande)
  exit / quit    Quitter SpiderClone
  help           Afficher cette aide
"""


def parse_copy_command(line: str):
    """« copy <site> » / « clone <site> » -> l'URL (str), sinon None."""
    m = COPY_RE.match(line)
    if not m:
        return None
    return m.group(1)


def parse_serve_command(line: str):
    """v2.5.0 — « serve <dir> [ngrok|cloudflare|cloudflare-named|none] »
    -> (dir, tunnel), sinon None (tunnel invalide -> ("", ""))."""
    m = SERVE_RE.match(line)
    if not m:
        return None
    parts = m.group(1).split()
    directory = parts[0]
    tunnel = parts[1].lower() if len(parts) > 1 else "none"
    if tunnel not in TUNNEL_CHOICES:
        return ("", "")
    return directory, tunnel


def interactive_loop(args, cfg: dict) -> None:
    """Boucle interactive : invite user@spiderclone:~$, commande copy <site>."""
    print(cs_ui.info("Mode interactif — tapez 'copy <site>' pour cloner, "
                     "'exit' pour quitter."))
    while True:
        try:
            line = input(cs_ui.prompt())
        except (EOFError, KeyboardInterrupt):
            print()
            break
        line = line.strip()
        if not line:
            continue
        low = line.lower()
        if low in ("exit", "quit", "q"):
            break
        if low in ("help", "?"):
            print(INTERACTIVE_HELP)
            continue
        served = parse_serve_command(line)
        if served is not None:
            directory, tunnel = served
            if not directory:
                print(cs_ui.warn("Tunnel inconnu : ngrok, cloudflare, "
                                 "cloudflare-named ou none."))
                continue
            try:
                publish(directory, tunnel, args.port or DEFAULT_PORT,
                        args.verbose, cf_token=args.cf_token,
                        cf_tunnel=args.cf_tunnel, cf_config=args.cf_config)
            except ValueError as e:
                print(cs_ui.warn(str(e)), file=sys.stderr)
            print()
            continue
        site = parse_copy_command(line)
        if site is not None:
            try:
                run_clone(_normalize_url(site), args, cfg)
            except KeyboardInterrupt:
                print("\n" + cs_ui.warn("Clone interrompu. Le manifest permet "
                                        "de reprendre avec --resume."))
            except ValueError as e:
                print(cs_ui.warn(str(e)), file=sys.stderr)
            print()
            continue
        if SERVE_VERB_RE.match(line):
            print(cs_ui.warn("Précisez un dossier : serve mon-site"))
            continue
        if COPY_VERB_RE.match(line):
            print(cs_ui.warn("Précisez une URL : copy https://exemple.fr"))
            continue
        print(cs_ui.warn("Commande inconnue. Tapez 'help' ou 'copy <site>'."))


def main():
    _reconfigure_console()
    parser = build_parser()
    args = parser.parse_args()

    cs_ui.set_color(not args.no_color and sys.stdout.isatty())
    if not args.no_banner:
        cs_ui.print_banner()

    cfg = _load_config(args.config)

    if args.serve_dir:
        final = dict(DEFAULTS)
        final.update({k: v for k, v in cfg.items() if k in DEFAULTS})
        final.update({k: v for k, v in vars(args).items()
                      if v is not None and k not in ("config", "url")})
        try:
            publish(final["serve_dir"], final["tunnel"], int(final["port"]),
                    final["verbose"], cf_token=final["cf_token"],
                    cf_tunnel=final["cf_tunnel"],
                    cf_config=final["cf_config"])
        except ValueError as e:
            print(cs_ui.warn(str(e)), file=sys.stderr)
            sys.exit(2)
        return

    if args.url:
        try:
            run_clone(_normalize_url(args.url), args, cfg)
        except KeyboardInterrupt:
            print("\n" + cs_ui.warn("Interrompu par l'utilisateur. Le manifest "
                                    "permet de reprendre avec --resume."))
            sys.exit(130)
        except ValueError as e:
            print(cs_ui.warn(str(e)), file=sys.stderr)
            sys.exit(2)
    else:
        interactive_loop(args, cfg)


if __name__ == "__main__":
    main()
