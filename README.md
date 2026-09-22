# SpiderClone

Clone complet d'un site web en local : pages HTML, CSS, JavaScript, images,
polices, vidéos… avec réécriture des liens pour une navigation hors-ligne.

Version **2.5.1** — publication : serveur HTTP local du clone et exposition
au public via ngrok, un Quick Tunnel Cloudflare ou un tunnel nommé à domaine
fixe (repli SPA, port choisi, publication d'un dossier déjà cloné). Les
appels API relatifs des SPA (POST/PUT/PATCH/DELETE, ex. `/ajax/bz`,
`/api/graphql`) sont servis (asset cloné) ou répondus par un JSON vide au
lieu d'un 501 qui cassait le parseur JS.

## Fonctionnalités

- **Clone récursif** avec profondeur limitée (`--depth`) et téléchargements
  parallèles (`--workers`).
- **Réécriture des liens** (HTML, CSS, JS) : les références locales pointent
  vers les fichiers clonés ; les pages hors profondeur pointent vers l'URL
  absolue du site live ; les liens externes sont laissés tels quels.
- **Un seul GET par URL** : chaque ressource est téléchargée une unique fois
  (lecture bornée par chunks, taille maximale configurable).
- **Politesse** : délai fixe (`--delay`), limite de requêtes par seconde
  (`--rate`), respect de `robots.txt` (Disallow, Allow avec wildcards `*`/`$`,
  Crawl-delay) via `--respect-robots`.
- **Bootstrap sitemap** (`--sitemap`), suivi récursif des sitemaps index.
- **Rendu JavaScript** optionnel via Playwright (`--render`), navigateur
  lancé une seule fois et réutilisé, appliqué uniquement aux pages HTML.
- **Mode brut** (`--raw`) : HTML préservé à l'octet près (réécriture par
  regex, sans reformatage BeautifulSoup).
- **Reprise** (`--resume`) : manifest JSON sauvegardé périodiquement et à la
  fin, permettant de reprendre un clone interrompu (Ctrl+C inclus).
- **Contrôle d'intégrité** final : tous les liens locaux du clone sont
  vérifiés (srcset complet, `data:` URI comprises).
- **Robustesse Windows** : noms de fichiers sanitizés, collisions de casse
  résolues par suffixe de hash, noms réservés (CON, PRN…) préfixés.
- **Bannière ASCII** au démarrage : araignée + lettrage « spider »clone »,
  tagline et invite colorées (désactivables avec `--no-banner` / `--no-color`).
- **Mode interactif** : `python spiderclone.py` sans URL affiche la bannière
  puis l'invite `user@spiderclone:~$` — tapez `copy <site>` pour cloner,
  `exit` pour quitter.
- **Sortie marquée** : étapes `[+]`, informations `[*]` et avertissements `[!]`
  avec crochets rouges sur terminal compatible ANSI.
- **Publication** (`--serve` / `--tunnel`) : sert le clone en local
  (types MIME corrects, repli SPA sur `index.html`) et l'expose au public
  via ngrok, un Quick Tunnel Cloudflare ou un tunnel Cloudflare nommé à
  domaine fixe — pour tester votre page ou la montrer à quelqu'un. Un
  dossier déjà cloné se publie sans re-cloner avec `--serve-dir`. Si le
  binaire du tunnel manque ou échoue, repli automatique en publication
  locale.
- **Extras** : archive zip (`--zip`), page menu (`--menu`), cookies
  (`--cookies` / `--cookie-file`), en-têtes personnalisés (`--header`),
  proxy HTTP/SOCKS (`--proxy`), configuration JSON (`--config`).
- **Challenge Cloudflare** : les pages « Just a moment... » (anti-bot) sont
  détectées (en-têtes `cf-mitigated` / `Server: cloudflare` + marqueurs du
  challenge JS), retentées (backoff, Retry-After honoré) puis classées en
  ressources protégées (rapport) au lieu de polluer les échecs.

## Installation

Python 3.10+ requis.

```bash
pip install -r requirements.txt
```

Dépendances : `requests`, `beautifulsoup4`.

Optionnel (rendu JavaScript) :

```bash
pip install playwright && playwright install chromium
```

## Utilisation

```bash
# Clone simple (profondeur 3 par défaut, 8 téléchargements parallèles)
python spiderclone.py https://exemple.fr

# Dossier de sortie, profondeur et parallélisme
python spiderclone.py https://exemple.fr -o clone -d 2 -w 4

# Mode brut + archive zip
python spiderclone.py https://exemple.fr --raw --zip clone.zip

# Respect de robots.txt + bootstrap sitemap
python spiderclone.py https://exemple.fr --respect-robots --sitemap

# Reprendre un clone interrompu
python spiderclone.py https://exemple.fr -o clone --resume

# Rendu JavaScript (Playwright requis)
python spiderclone.py https://exemple.fr --render

# Configuration depuis un fichier JSON
python spiderclone.py https://exemple.fr --config config.json

# Sans bannière ni couleurs (scripts, CI, redirection de sortie)
python spiderclone.py https://exemple.fr --no-banner --no-color
```

### Mode interactif

Sans URL, SpiderClone affiche la bannière puis une invite :

```
$ python spiderclone.py
user@spiderclone:~$ copy https://exemple.fr
user@spiderclone:~$ copy https://autre-site.fr -o autre
user@spiderclone:~$ exit
```

Commandes : `copy <site>` (ou `clone <site>`) pour cloner, `exit`/`quit`
pour quitter, `help` pour l'aide. Toutes les options (`-o`, `-d`, `-w`,
`--raw`, `--resume`…) restent valables après `copy`. Ctrl+C pendant un
clone revient à l'invite (manifest sauvegardé).

Les couleurs sont activées automatiquement sur un terminal interactif et
coupées quand la sortie est redirigée (pipe, fichier).

Une URL sans schéma est acceptée (`exemple.fr` → `https://exemple.fr`).

### Options

| Option | Description | Défaut |
|---|---|---|
| `-o, --output` | Dossier de destination | nom du domaine |
| `-d, --depth` | Profondeur maximale de navigation | 3 |
| `-w, --workers` | Téléchargements parallèles | 8 |
| `--include-subdomains` | Inclure les sous-domaines | non |
| `--external` | Suivre les liens vers d'autres domaines | non |
| `--delay` | Délai fixe (s) avant chaque page | 0 |
| `--rate` | Limite de requêtes/s par domaine | 0 |
| `--max-size` | Taille max d'un fichier (octets) | 50 Mo |
| `--max-urls` | Nombre max d'URLs à traiter | illimité |
| `--max-time` | Durée max du clone (s) | illimité |
| `--user-agent` | User-Agent HTTP | spiderclone/2.5.0 |
| `--cookies` | Cookies `'nom=valeur; nom2=valeur2'` | — |
| `--cookie-file` | Fichier de cookies Netscape | — |
| `--header` | En-tête HTTP supplémentaire (répétable) | — |
| `--proxy URL` | Proxy HTTP/SOCKS pour les requêtes (v2.5.0) | — |
| `--raw` | HTML préservé à l'octet près | non |
| `--render` | Rendu JavaScript (Playwright) | non |
| `--check` / `--no-check` | Contrôle d'intégrité final | activé |
| `--resume` | Reprendre via le manifest | non |
| `--respect-robots` | Respecter robots.txt | non |
| `--sitemap [URL]` | Bootstrap sitemap (auto ou URL) | non |
| `--zip FICHIER.zip` | Emballer le clone en zip | — |
| `--menu` | Générer une page menu des pages clonées | non |
| `--only-assets` | Scanner sans sauvegarder les pages (v2.4.0) | non |
| `--only-pages` | Sauvegarder les pages sans ressources (v2.4.0) | non |
| `--screenshot` | Captures d'écran finales (Chromium headless) | non |
| `--serve` | Publier le clone : serveur HTTP local (v2.5.0) | non |
| `--tunnel {none,ngrok,cloudflare,cloudflare-named}` | Exposer le serveur au public (v2.5.0) | none |
| `--port` | Port du serveur de publication (v2.5.0) | 8000 |
| `--serve-dir DOSSIER` | Publier un dossier cloné, sans re-cloner (v2.5.0) | — |
| `--cf-token TOKEN` | Tunnel nommé : token du tunnel (dashboard Cloudflare) | — |
| `--cf-tunnel NOM` | Tunnel nommé : nom du tunnel local (credentials installés) | — |
| `--cf-config FICHIER.yml` | Tunnel nommé : fichier config.yml (ingress) | — |
| `--config FICHIER.json` | Configuration JSON | — |
| `--no-banner` | Ne pas afficher la bannière ASCII | non |
| `--no-color` | Désactiver les couleurs ANSI | non |
| `-v, --verbose` | Afficher chaque fichier téléchargé | non |

### Fichier de configuration

Les clés correspondent aux options longues (sans `--`). Les clés inconnues
sont signalées et ignorées.

```json
{
  "depth": 2,
  "workers": 4,
  "delay": 0.5,
  "rate": 2.0,
  "max_size": 10485760,
  "max_urls": 500,
  "max_time": 600,
  "respect_robots": true,
  "sitemap": true,
  "raw": false,
  "render": false,
  "menu": true,
  "zip": "clone.zip",
  "user_agent": "MonBot/1.0",
  "header": ["Accept-Language: fr-FR,fr;q=0.9"],
  "serve": true,
  "tunnel": "ngrok",
  "port": 8080
}
```

La ligne de commande prime sur le fichier de configuration, qui prime sur les
défauts.

### Publication (ngrok / Cloudflare)

Après un clone, SpiderClone peut servir le site en local puis l'exposer au
public via un tunnel — pour tester votre page depuis un autre appareil ou la
montrer à quelqu'un.

```bash
# 1. Clone puis publication locale (Ctrl+C pour arrêter) :
python spiderclone.py --serve https://exemple.fr

# 2. Exposé au public via ngrok (URL https://xxx.ngrok-free.app) :
python spiderclone.py --tunnel ngrok https://exemple.fr

# 3. Via un Quick Tunnel Cloudflare (URL trycloudflare.com, sans inscription) :
python spiderclone.py --tunnel cloudflare https://exemple.fr

# 4. Publier un dossier déjà cloné, sans re-cloner (avec tunnel / port) :
python spiderclone.py --serve-dir exemple.fr
python spiderclone.py --serve-dir exemple.fr --tunnel ngrok --port 9000

# 5. Tunnel Cloudflare NOMMÉ à domaine fixe, via le token du dashboard :
python spiderclone.py --tunnel cloudflare-named --cf-token TOKEN https://exemple.fr

# 6. Via le nom d'un tunnel local déjà créé (credentials installés) :
python spiderclone.py --serve-dir exemple.fr --tunnel cloudflare-named --cf-tunnel mon-tunnel

# 7. Via un fichier config.yml (ingress + credentials) :
python spiderclone.py --serve-dir exemple.fr --tunnel cloudflare-named --cf-config tunnel.yml
```

En mode interactif : `serve <dossier> [ngrok|cloudflare|cloudflare-named]`
(alias `publish`) — les réglages du tunnel nommé passent par les flags
`--cf-token` / `--cf-tunnel` / `--cf-config` de la ligne de commande.

Comportement du serveur :
- types MIME corrects (HTML, CSS, JS/ESM, JSON, SVG, images, polices,
  vidéos…) — indépendants du registre Windows ;
- repli SPA : une route introuvable sans extension (ex. `/profil/xyz`)
  retombe sur `index.html` — les sites à routage client-side restent
  navigables ; une ressource manquante avec extension (`/logo.png`) reste un
  404 normal ;
- écoute uniquement sur `127.0.0.1` : le tunnel est le seul accès public.

Tunnels :
- **ngrok** : compte gratuit requis ; le token passe par `NGROK_AUTHTOKEN` ou
  `ngrok config add-authtoken <TOKEN>` ;
- **cloudflared** : Quick Tunnel Cloudflare, aucune inscription requise ;
- **cloudflare-named** : tunnel Cloudflare à domaine DNS fixe (créé sur le
  dashboard ou en CLI). Trois façons de le régler (au moins une requise,
  sinon erreur claire avant le démarrage) : token `--cf-token`, nom de tunnel
  local `--cf-tunnel`, ou fichier `--cf-config` (config.yml). L'URL publique
  est le domaine DNS rattaché au tunnel sur votre compte — pas une URL
  aléatoire ;
- binaire absent ou lancement en échec → SpiderClone affiche la commande
  d'installation (`winget install ngrok.ngrok` / `winget install
  Cloudflare.cloudflared`) puis poursuit en **local** (repli automatique) —
  la publication locale fonctionne toujours.

## Structure du projet

```
spiderclone/
├── spiderclone.py          # Point d'entrée : argparse + orchestration
├── cs_ui.py                # Bannière ASCII + couleurs ANSI + marqueurs [+]/[*]/[!]
├── cs_config.py            # Constantes, regex et tables de réécriture
├── cs_utils.py             # Helpers purs (sanitize, encodage, classification)
├── cs_fetch.py             # HTTP : fetch unique, throttle, robots.txt
├── cs_render.py            # Rendu JavaScript via Playwright
├── cs_rewrite.py           # Réécriture des liens (HTML, CSS, JS)
├── cs_crawl.py             # Orchestrateur (SiteClone) : file BFS, manifest…
├── cs_serve.py             # v2.5.1 : serveur local + tunnels (ngrok, Cloudflare,
│                          #          nommé à domaine fixe, repli SPA, mock API)
├── tests/
│   ├── test_units.py       # 145 tests unitaires (aucun réseau)
│   ├── test_server.py      # Serveur HTTP local journalisant les requêtes
│   └── e2e_test.py         # Test E2E : clone d'un site local via la CLI
└── requirements.txt
```

## Nouveautés v2.5.1

- **Méthodes d'écriture servies** : POST/PUT/PATCH/DELETE et OPTIONS sont
  gérés par le serveur. Priorité : si un asset du clone correspond au chemin
  (endpoint API cloné, suffixe `.json` essayé en plus), il est servi ; sinon
  une réponse **JSON vide `{}`** (200, `application/json`) est renvoyée — les
  SPA (ex. Instagram) qui appellent `/ajax/bz`, `/api/graphql`, etc. ne
  reçoivent plus un corps HTML `501 Unsupported method` qui faisait échouer
  `JSON.parse` dans le navigateur.
- OPTIONS répond 204 avec l'en-tête `Allow` (pré-vol CORS local).
- Corps des requêtes d'écriture drainé (lecture bornée à 1 Mo) pour ne pas
  casser le keep-alive.

## Nouveautés v2.5.0

- **Publication** : `--serve` sert le clone cloné avec un serveur HTTP local
  (types MIME explicites, listing de répertoire, repli SPA sur `index.html`
  pour les routes inconnues sans extension). Ctrl+C arrête proprement.
- **Tunnels publics** : `--tunnel ngrok` (URL `ngrok-free.app`, token via
  `NGROK_AUTHTOKEN` ou `ngrok config add-authtoken`), `--tunnel cloudflare`
  (Quick Tunnel `trycloudflare.com`, sans inscription) et
  `--tunnel cloudflare-named` (tunnel à domaine DNS fixe, réglé via
  `--cf-token` / `--cf-tunnel` / `--cf-config` — au moins un des trois).
- **Repli local automatique** : binaire manquant ou tunnel qui échoue →
  avertissement + publication en local uniquement (le serveur continue de
  servir le clone au lieu de tout stopper).
- **Port choisi** : `--port N` (défaut 8000 ; un autre port est pris s'il est
  occupé).
- **Sans re-cloner** : `--serve-dir DOSSIER` publie un dossier déjà cloné
  (en interactif : `serve <dossier> [ngrok|cloudflare|cloudflare-named]`,
  alias `publish`).
- **Proxy** : `--proxy URL` (HTTP/SOCKS) — toutes les requêtes de crawl
  passent par le proxy (test via Burp, réseau d'entreprise, etc.).
- **Challenge Cloudflare** : pages « Just a moment... » détectées via
  `cf-mitigated: challenge` / `Server: cloudflare` + marqueurs du challenge
  JS ; retry borné puis classement en ressource protégée (rapport).

## Nouveautés v2.4.0

- **Modes limités** : `--only-assets` (scan des ressources sans sauvegarde
  des pages) et `--only-pages` (pages sans ressources).
- **Captures d'écran** : `--screenshot` (Chromium headless, dossier
  `captures/`, dégradation propre si le navigateur est absent).
- **Rapport** `spiderclone_report.txt` : statistiques + ressources non
  téléchargées (protégées / rate-limitées) et leurs raisons.
- **2e passe** : les ressources temporairement limitées (429/503) sont
  retentées après le clone ; backoff adaptatif si le taux de 429 dépasse le
  seuil.
- **Endpoints API** : les `fetch()` / XHR des JS sont planifiés et clonés.

## Nouveautés v2.3.0

- **Mode interactif** : `python spiderclone.py` sans URL affiche la bannière
  puis l'invite `user@spiderclone:~$` (colorée : `user@spiderclone` en vert,
  `:~$` en bleu). Commandes : `copy <site>` / `clone <site>` pour cloner,
  `exit`/`quit` pour quitter, `help` pour l'aide. Ctrl+C pendant un clone
  revient à l'invite (manifest sauvegardé, reprise possible avec `--resume`).
- **URL optionnelle** : la ligne de commande accepte désormais zéro ou une
  URL — sans URL, le mode interactif démarre.

## Nouveautés v2.2.0

- **Bannière ASCII** : araignée symétrique (miroir de l'art « tahl »
  d'asciiart.eu) avec accents rouges (yeux, bouts de pattes), lettrage 5×4
  « spider » (blanc) + « clone » (rouge), tagline `> Clone · Mirror · Preserve <`,
  lignes de boot et invite `user@spiderclone:~$`.
- **`--no-banner`** : démarrage sans bannière (scripts, CI).
- **`--no-color`** : sortie sans séquences ANSI ; les couleurs sont aussi
  coupées automatiquement quand la sortie n'est pas un terminal.
- **Marqueurs colorés** : `[+]` (démarrage, clonage), `[*]` (sitemap, reprise,
  menu, archive), `[!]` (avertissements) — module `cs_ui`.

## Corrections v2.1.0

Bugs corrigés par rapport à la v2.0.0 :

- **`--depth` réellement appliqué** : une page au-delà de la profondeur max
  n'est plus clonée — son lien pointe vers l'URL absolue du site live ; les
  assets restent clonés. File BFS en `deque` de tuples `(url, depth, referer)`
  avec vérification défensive dans le traitement.
- **Un seul GET par URL** : l'ancien code sondait chaque URL (stream) puis la
  retéléchargeait. `fetch_once` fait un GET unique, lu par chunks bornés.
- **`new URL("x", import.meta.url)`** : le 2ᵉ argument était avalé par la
  regex, cassant le JS à l'exécution — il est désormais réémis.
- **`import/export … from "…"`** : la regex ne matche plus les littéraux
  contenant `from` et ignore les specifiers nus (npm : `react`, `lodash`).
- **`@import "x.css" screen`** : la media query était détachée, produisant du
  CSS invalide — elle est réémise.
- **robots.txt** : ancrage `$` correctement échappé (`^/gone\Z`), priorité à
  `Allow`, wildcards `*`, Crawl-delay appliqué via le throttle par domaine.
- **Collisions Windows** : chemins insensibles à la casse gérés par un
  registre + suffixe de hash (`stem__<md5>ext`) en cas de collision réelle.
- **Manifest périodique** : sauvegardé toutes les 25 tâches (et à la fin),
  `--raw` respecté aussi au resume, Ctrl+C non bloquant.
- **Rendu HTML-only** : le navigateur n'est lancé que pour les pages HTML.
- **srcset** : toutes les URLs vérifiées au contrôle d'intégrité, URI
  `data:` (avec virgules base64) reconstituées correctement.
- **`sanitize_filename`** : les fichiers "pointés" (`.htaccess`, `.env`) ne
  sont plus détruits par l'ancien `lstrip(" .")`.
- **Config validée** : clés inconnues signalées, URL sans schéma acceptée.

## Tests

```bash
# Tests unitaires (145 tests, aucun réseau)
python tests/test_units.py

# Test E2E : site local auto-généré, serveur HTTP, clone via la CLI
python tests/e2e_test.py
```

Le test E2E vérifie : le clonage de toutes les pages/assets attendus, la
réécriture du lien hors profondeur en URL absolue live, la préservation des
media queries CSS, de `new URL("chunk.js", import.meta.url)` et des
specifiers npm dans le JS, le lien externe laissé tel quel, l'absence de
double GET (chaque URL demandée une seule fois, via le journal du serveur),
le mode interactif (`copy <url>` + `exit`) et, pour la v2.5.0, la
publication `--serve-dir` (index.html servi localement + repli SPA ; les
tunnels ngrok/Cloudflare sont couverts par les parseurs unitaires) et, pour
la v2.5.1, le mock JSON des POST API.

## Limites

- Le serveur répond **simplement** aux méthodes d'écriture : un asset cloné
  est servi, sinon un JSON `{}` factice — pas de persistance ni de logique
  métier. Les appels API **absolus** vers des domaines hors périmètre
  (ex. `static.cdninstagram.com`, `www.facebook.com`) restent bloqués par le
  CORS du navigateur : re-clonez le site avec `--include-subdomains` pour
  ramener ces hôtes dans le périmètre.
- Le clone est **statique** : les contenus chargés dynamiquement ne sont
  capturés qu'avec `--render` (et uniquement le HTML final, pas les requêtes
  réseau du navigateur).
- Les pages protégées par connexion produisent un clone incomplet (un
  avertissement est affiché si une page de login est détectée).
- Le respect de `robots.txt` est désactivé par défaut (comportement d'un
  outil de clonage) ; activez `--respect-robots` pour un usage conforme.
