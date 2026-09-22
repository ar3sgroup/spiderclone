# BUILD_NOTES — Packaging SpiderClone

Notes de packaging (launcher Windows + executable PyInstaller).
Cette tache de packaging n'a modifie aucun fichier source Python : seuls des
nouveaux fichiers ont ete ajoutes (voir « Fichiers crees »). L'executable a
ete reconstruit le 22/09/2026 a partir de la version v2.5.0 (publication
serveur local + tunnels ngrok/Cloudflare + tunnel nomme Cloudflare et repli
local automatique, voir sections 3 et 5).

Version packagee : **spiderclone 2.5.0**
Plateforme du build : Windows 11 (10.0.26200), Python 3.13.15
PyInstaller : **6.22.3** (installe via `pip install pyinstaller`)

---

## 1. Launcher double-clic : `spiderclone.bat`

Place a la racine du projet, a executer par double-clic. Il se place dans le
dossier du `.bat`, lance `python spiderclone.py` (=> mode interactif, invite
`user@spiderclone:~$`) puis met en pause pour que la fenetre ne se ferme pas.

Encodage : ASCII pur, fins de ligne CRLF (compatible cmd.exe / cp1252).

Contenu exact de `spiderclone.bat` :

```bat
@echo off
rem SpiderClone - lanceur Windows
rem by xyrek from ar3s - 2026-09-22
setlocal
title SpiderClone
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH. Install Python 3 and retry.
    pause
    exit /b 1
)

python spiderclone.py

echo.
pause
endlocal
```

Le `where python` permet d'afficher un message clair (au lieu d'une fenetre qui
clignote) si Python n'est pas dans le PATH.

---

## 2. Build PyInstaller

### Etat

Le build **a reussi** (aucun echec a documenter).

| Element | Valeur |
|---|---|
| Statut | OK (exit 0) |
| Entree | `spiderclone.py` |
| Spec | `spiderclone.spec` (onefile, name=spiderclone) |
| Executable | `dist/spiderclone.exe` |
| Taille | 19 479 318 octets (~18,6 Mio / ~19 Mo) |
| Smoke-test (rebuilt v2.5.0) | `dist\spiderclone.exe --version` => `spiderclone 2.5.0` ; `--help` affiche les options `--tunnel/--cf-token/--cf-tunnel/--cf-config` ; `--serve-dir` + `--tunnel cloudflare-named` sert le site en local (repli) |
| Source post-traitement | commentaires supprimes + en-tete `by xyrek from ar3s — 2026-09-22` sur tous les fichiers (voir README) |

### Comment relancer le build

Deux methodes equivalentes, depuis la racine du projet (`copieursite`) :

```bat
:: via le spec (recommande, configuration fige)
python -m PyInstaller spiderclone.spec --noconfirm

:: ou en ligne de commande directe
python -m PyInstaller --onefile --name spiderclone --console ^
    --exclude-module playwright --exclude-module tkinter --exclude-module pytest ^
    spiderclone.py --noconfirm
```

Si PyInstaller n'est pas installe :

```bat
pip install pyinstaller
```

### Ou est l'executable produit

- `dist/spiderclone.exe` — executable autonome (un seul fichier).
- Dossier de travail temporaire : `build/` (peut etre supprime/regenere).
- Journal du build : `pyinstaller_build.log`.

### Contenu du spec (resume)

`spiderclone.spec` definit un build **onefile** :
- `Analysis` sur `spiderclone.py` ;
- `hiddenimports` listant les modules du projet (`cs_config`, `cs_utils`,
  `cs_fetch`, `cs_render`, `cs_rewrite`, `cs_crawl`, `cs_ui`) ;
- `excludes` pour alleger le binaire : `playwright` (rendu JS optionnel),
  les toolkits GUI (`tkinter`, `PyQt5/6`, `PySide2/6`), la stack scientifique
  (`matplotlib`, `numpy`, `pandas`, `scipy`) et les tests (`pytest`, `tests`) ;
- `console=True` (l'outil est en ligne de commande).

---

## 3. Verification — suite de tests

Apres la refonte v2.4.0 (modules `cs_*.py`, `spiderclone.py`, `tests/*.py`), la
suite complete confirme l'integrite :

```
python tests/test_units.py
```

Resultat (mise a jour v2.5.0) : **138 tests unitaires, OK** + **E2E OK**
(tests/e2e_test.py : 2e passe, placeholders, rapport, endpoint API,
--only-pages/--only-assets/--screenshot, mode interactif, publication
--serve-dir v2.5.0 avec fallback SPA).

Couverture v2.5.0 ajoutee : detection challenge Cloudflare (403/503 et
`cf-mitigated: challenge`, retry, recuperation, 403 simple non classe),
flag `--proxy` (session.requests.proxies), bucket `protected` dans le
rapport, tunnel nomme Cloudflare (`cloudflare-named` + `--cf-token` /
`--cf-tunnel` / `--cf-config`) et repli local automatique quand le binaire
du tunnel manque ou echoue.

`python spiderclone.py --version` => `spiderclone 2.5.0`.
`dist\spiderclone.exe --version` => `spiderclone 2.5.0` (reconstruit).
Le mode crawl n'a pas ete execute (aucune requete reseau vers un site reel).
Les tunnels ngrok/cloudflared ne sont pas installes sur la machine de build :
couverture par tests unitaires seulement (message binaire manquant, extraction
d'URL, repli local, ligne de commande du tunnel nomme, process vivant apres la
fenetre de grace), aucun tunnel reel lance.

---

## 4. Fichiers crees par cette tache

- `spiderclone.bat` — launcher double-clic.
- `spiderclone.spec` — specification PyInstaller onefile.
- `BUILD_NOTES.md` — ce fichier.
- `dist/spiderclone.exe` — executable genere (artefact de build).
- `build/` — dossier temporaire PyInstaller (artefact).
- `pyinstaller_build.log` — journal du build.

---

## 5. Publication v2.5.0 — `cs_serve.py` (serveur local + tunnels)

Nouveaute v2.5.0 : publier un dossier clone en local (HTTP) et l'exposer
publiquement via un tunnel (ngrok, Quick Tunnel Cloudflare ou tunnel NOMME
Cloudflare a domaine DNS fixe), pour tester la page ou la montrer au public.
Si le binaire du tunnel manque ou que le lancement echoue, repli automatique
en publication LOCALE (fallback=True par defaut).

### Module `cs_serve.py`

- `CloneHandler` — sous-classe de `SimpleHTTPRequestHandler` :
  - fallback SPA dans `send_head` (route inconnue => sert index.html) ;
  - `MIME_OVERRIDES` applique dans `guess_type` (corrections de types) ;
  - `log_message` silencieux (pas de pollution console).
- `find_free_port(preferred)` — renvoie un port libre ; si `preferred` <= 0
  ou deja occupe, tire un port aleatoire (`s.getsockname()[1]`).
- `serve_directory(root, port, spa, verbose)` — serveur thread sur `root`,
  via la factory `_make_handler(root, is_verbose)` : sous-classe qui force
  `directory=root` dans `__init__` (sinon `SimpleHTTPRequestHandler` ecrase
  l'attribut avec le cwd du processus).
- `start_ngrok(...)` — lance `ngrok http <port>` puis interroge l'API
  locale `http://127.0.0.1:4040/api/tunnels` jusqu'a obtenir l'URL publique
  (delai NGROK_TIMEOUT).
- `start_cloudflared(...)` — Quick Tunnel (`cloudflared tunnel --url ...`),
  extraction de l'URL par regex `https://[A-Za-z0-9-]+\.trycloudflare\.com`
  (delai CLOUDFLARED_TIMEOUT).
- `build_cloudflared_named_args(port, token, tunnel_name, config)` — helper
  PURE (testable) qui construit la ligne de commande du tunnel nomme selon
  la priorite : token (`run --token TOK`) > nom (`run --url LOCAL NAME`) >
  config (`--config FILE run`) ; `ValueError` si aucun reglage.
- `start_cloudflared_named(port, token, tunnel_name, config, verbose)` —
  lance le tunnel nomme puis attend CLOUDFLARED_NAMED_GRACE (5 s) : process
  toujours vivant => (Popen, None) ; mort dans la grace => `TunnelError`
  (token/credentials invalides). L'URL publique n'est pas affichee par
  cloudflared : c'est le domaine DNS du compte.
- `publish(out_dir, tunnel, port, verbose, cf_token, cf_tunnel, cf_config,
  fallback)` / `stop()` — point d'entree unique, non bloquant ; valide le
  reglage du tunnel nomme AVANT de demarrer le serveur ; leve `TunnelError`
  si le binaire manque (avec `INSTALL_HINTS`) ; helper interne `_start` :
  tunnel en echec => avertissement + publication locale (fallback=True, defaut)
  ou re-raise apres `stop(server, None)` (fallback=False).

### Integration CLI (`spiderclone.py`)

- Flags : `--serve`, `--tunnel {none,ngrok,cloudflare,cloudflare-named}`,
  `--port`, `--serve-dir DOSSIER`, `--proxy URL`, et les reglages du tunnel
  nomme : `--cf-token TOKEN`, `--cf-tunnel NOM`, `--cf-config FICHIER.yml`.
  Entrees `cf_token/cf_tunnel/cf_config` ajoutees a DEFAULTS.
- `publish(output, tunnel, port, verbose, cf_token=..., cf_tunnel=...,
  cf_config=...)` appele dans `run_clone` apres le clone ; mode interactif :
  commandes `serve|publish <dir> [tunnel]` (`parse_serve_command` /
  SERVE_RE, tunnel `cloudflare-named` accepte).

### Proxy (`--proxy URL`)

- `SiteClone.__init__` applique `self.session.proxies =
  {"http": proxy, "https": proxy}` (HTTP/SOCKS, ex. Burp ou reseau
  d'entreprise) ; `--proxy` cote CLI, cle `proxy` dans la config JSON.
  Defaut : `session.proxies == {}` (comportement requests).

### Detection challenge Cloudflare (`cs_fetch.py`)

- `CloudflareChallengeError` + `is_cloudflare_challenge(headers, content)` :
  en-tete `cf-mitigated: challenge` ou `Server: cloudflare` + marqueurs du
  challenge JS (`cdn-cgi/challenge-platform`, `jschl_vc`, `cf_chl_`,
  `_cf_chl_opt`, `cf-browser-verification`, « just a moment »,
  « attention required »).
- Sonde bornee (CHALLENGE_PROBE_MAX = 256 Ko) lue sur 403/503 ou
  `cf-mitigated` avant classification du statut ; retry avec backoff
  (`_retry_after_delay`, Retry-After honore) jusqu'a MAX_RETRIES ; l'exception
  finale inclut `CloudflareChallengeError`.
- `cs_crawl._note_failure` classe ces pages dans le bucket `protected`
  (dedupe, stats["protected"], raison « challenge Cloudflare ») — le rapport
  les liste comme ressources protegees au lieu d'echecs bruts.

### Build

`cs_serve.py` est importe par `spiderclone.py` : il est inclus
automatiquement dans l'exe par l'`Analysis` PyInstaller, sans changement du
spec.

### Tests

- Unitaires (classe `TestServe` dans `tests/test_units.py`) : fallback SPA,
  extraction d'URL cloudflared, message binaire manquant, publish non
  bloquant, publish tunnel/dossier invalide, GET simple, listing sans
  index.html, find_free_port ; v2.5.0 ajoute : `build_cloudflared_named_args`
  (3 modes + ValueError sans reglage), `start_cloudflared_named` (binaire
  manquant, crash dans la grace, vivant apres la grace), `publish` en repli
  local (tunnel mocke en echec => serveur 200 quand meme), fallback=False
  (re-raise), `cloudflare-named` moque => (Popen, None) ; classes `TestFetch`
  (challenge Cloudflare, retry, recuperation) et `TestCLI` (`--proxy` branche
  sur la session, defaut `{}`, flags `--cf-*` et choice `cloudflare-named`).
- E2E (`tests/e2e_test.py`) : scenario `--serve-dir OUT --port P` =>
  GET `/index.html` puis route inconnue => fallback index.html (SPA).

---

## 6. Correctif v2.5.1 — méthodes d'écriture (mock API JSON)

Contexte : sur le clone Instagram servi localement, la console navigateur
remontait des erreurs intermittentes :
- `501 Unsupported method ('POST')` sur `POST /ajax/bz`, `POST /api/graphql`
  et `POST /ajax/bulk-route-definitions/` — `CloneHandler` n'implémentait
  aucune méthode d'écriture ; `SimpleHTTPRequestHandler` répondait 501 avec
  un corps HTML, ce qui faisait échouer le parseur JSON du JS (Instagram)
  : `Unexpected token '<', "<!DOCTYPE "... is not valid JSON`.
- Variabilité « des fois » : le JS réel d'Instagram (chargé depuis
  `static.cdninstagram.com`, hors périmètre de clone) ne boote que s'il est
  accessible ; sans CDN, aucun appel API → aucune erreur.

Correctif (commit v2.5.1, `cs_serve.py`) :
- `CloneHandler.do_POST/do_PUT/do_PATCH/do_DELETE` → `_answer_write_method()` :
  1. asset du clone présent pour le chemin (suffixe `.json` essayé en plus,
     les endpoints API sont clonés en `api/x.json` par la v2.4.0) → servi ;
  2. sinon mock JSON `{}` (200 `application/json`, `mock_api=True` par défaut) ;
  3. `mock_api=False` → 501 explicite (comportement d'origine conservé).
- `do_OPTIONS` → 204 + en-tête `Allow` (pré-vol CORS).
- `_drain_body()` → lit le corps (`Content-Length`, borné à 1 Mo) pour ne pas
  désynchroniser le keep-alive.
- Attributs de classe `mock_api = True`, `mock_payload = b"{}"`.
- Version : `cs_config.VERSION = "2.5.1"`.

Limites restantes (documentées) :
- les appels ABSOLUS hors périmètre (`https://www.facebook.com/ig_xsite_user_info/`,
  `https://static.cdninstagram.com/...`) restent bloqués côté navigateur
  (CORS) — non corrigeable côté serveur de clone ; re-cloner avec
  `--include-subdomains` pour les ramener dans le périmètre ;
- le mock ne reproduit pas la logique métier de l'API (pas de persistance).

Tests :
- `tests/test_units.py` : +3 tests `TestServe` (mock JSON 200, asset cloné
  servi, 501 si `mock_api=False`) et `test_version_251` → **142 tests OK** ;
- `tests/e2e_test.py` : assertion de version rapport passée à 2.5.1 → E2E OK ;
- vérification live sur le clone `instagram.com/` : POST `/ajax/bz`,
  `/api/graphql`, `/ajax/bulk-route-definitions/` → 200 `application/json`
  `{}` ; DELETE/PUT/PATCH → 200 ; OPTIONS → 204 ; GET `/index.html` intact
  (412 537 octets).

## 7. Correctif v2.5.1 — XMLParsedAsHTMLWarning (pages XML/XHTML)

Contexte : lors d'un clone (Instagram), la console affichait
`cs_crawl.py:553: XMLParsedAsHTMLWarning: It looks like you're using an HTML
parser to parse an XML document.` — des pages XML (flux RSS, sitemaps, XHTML
servi avec un Content-Type HTML) sont classées comme pages par
`looks_like_html`/`is_html_url` puis passées dans
`BeautifulSoup(content, "html.parser")`, qui émet le warning (bs4 ≥ 4.12).

Correctif (commit v2.5.1, `cs_crawl.py`) :
- helper `_parse_markup(content)` : construit le BeautifulSoup sous
  `warnings.catch_warnings()` + `filterwarnings("ignore", category=
  XMLParsedAsHTMLWarning)` — le parsing reste en mode HTML (la réécriture
  d'attributs `cs_rewrite` fonctionne pour HTML et XHTML, les deux sont des
  variantes markup ; pas de dépendance lxml ajoutée) ;
- les 3 points de construction sont passés par le helper : `_process_page`
  (553), `_scan_page_for_assets` (677) et le scan d'intégrité (749).

Tests :
- `tests/test_units.py` : +3 tests `TestParseMarkup` (XML via `_process_page`
  → page sauvegardée + aucun warning ; XML via `_scan_page_for_assets` ;
  helper direct) → **145 tests OK** ;
- `tests/e2e_test.py` : E2E OK (RC=0, `--serve-dir v2.5.1`).
