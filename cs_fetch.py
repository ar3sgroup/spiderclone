#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Couche HTTP : fetch unique borne par chunks, throttle par domaine thread-safe, robots.txt, detection de page de login.
"""

import re
import threading
import time
import urllib.parse

import requests

from cs_config import DEFAULT_TIMEOUT, LOGIN_PATH_RE, MAX_RETRIES


class DeadLinkError(RuntimeError):
    """Lien mort (HTTP 404/410) : signalé mais non retenté."""


class ProtectedError(RuntimeError):
    """Ressource protégée (HTTP 401/403) : non retentée, ignorée proprement.

    Ce n'est pas un échec : le contenu exige une authentification que le
    cloner n'a pas (ou ne veut pas) fournir. On copie ce qu'on peut.
    """


class RateLimitedError(RuntimeError):
    """Limite de débit (HTTP 429/503) : retentée avec backoff, puis ignorée.

    Après épuisement des retries, la ressource est comptée à part (pas un
    échec dur) : le serveur refuse temporairement, pas un lien cassé.
    """


class CloudflareChallengeError(RuntimeError):
    """v2.5.0 — page challenge Cloudflare (« Just a moment... », anti-bot).

    Détectée via cf-mitigated: challenge ou Server: cloudflare + marqueurs du
    challenge JS (cdn-cgi/challenge-platform, jschl_vc, cf_chl_...). Le GET
    est retenté (backoff, Retry-After honoré) : un cookie cf_clearance peut
    arriver entre-temps. À l'épuisement des retries, la ressource est classée
    comme protégée (ignorée proprement, pas un échec dur).
    """


RATE_LIMIT_BACKOFF_MAX = 10.0

CHALLENGE_PROBE_MAX = 262144
CHALLENGE_MARKER_RE = re.compile(
    r"cdn-cgi/challenge-platform|jschl_vc|cf_chl_|_cf_chl_opt"
    r"|cf-browser-verification|just a moment|attention required",
    re.IGNORECASE)


def is_cloudflare_challenge(headers, content: bytes) -> bool:
    """v2.5.0 — True si la réponse est une page challenge Cloudflare.

    Deux conditions : la réponse provient bien de Cloudflare (en-tête
    ``cf-mitigated: challenge`` ou ``Server: cloudflare``) ET le corps
    contient un marqueur du challenge JS. Sans ces deux conditions, une
    page quelconque contenant « just a moment » n'est PAS classée challenge
    (pas de faux positif hors Cloudflare).
    """
    hdrs = headers or {}
    cf_mitigated = str(hdrs.get("cf-mitigated") or "").lower()
    server = str(hdrs.get("Server") or "").lower()
    is_cf = "cloudflare" in server or "challenge" in cf_mitigated
    if not is_cf or not content:
        return False
    head = content[:CHALLENGE_PROBE_MAX].decode("utf-8", errors="replace")
    return bool(CHALLENGE_MARKER_RE.search(head))


class FetchResult:
    """Réponse HTTP simplifiée (contenu + en-têtes + URL finale)."""

    def __init__(self, content: bytes, headers, url: str):
        self.content = content
        self.headers = headers
        self.url = url


class RobotsRules:
    """Règles robots.txt d'un user-agent (Allow/Disallow + Crawl-delay)."""

    def __init__(self):
        self.allow = []
        self.disallow = []
        self.crawl_delay = None

    @staticmethod
    def _pattern(rule: str) -> re.Pattern:
        """Convertit une règle robots en regex (wildcards * et $ de fin)."""
        anchored = rule.endswith("$")
        if anchored:
            rule = rule[:-1]
        pattern = "^" + re.escape(rule).replace(r"\*", ".*")
        if anchored:
            pattern += r"\Z"
        return re.compile(pattern)

    def allowed(self, url: str) -> bool:
        """True si l'URL n'est pas exclue (priorité à Allow, comme les bots)."""
        path = urllib.parse.urlsplit(url).path or "/"
        for rule in self.allow:
            if self._pattern(rule).match(path):
                return True
        for rule in self.disallow:
            if self._pattern(rule).match(path):
                return False
        return True


def parse_robots(text: str) -> dict:
    """Parse robots.txt -> {user_agent: RobotsRules}."""
    groups = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        k, _, v = line.partition(":")
        k, v = k.strip().lower(), v.strip()
        if k == "user-agent":
            current = v.lower() or "*"
            groups.setdefault(current, RobotsRules())
        elif current is not None:
            rules = groups[current]
            if k == "allow" and v:
                rules.allow.append(v)
            elif k == "disallow" and v:
                rules.disallow.append(v)
            elif k == "crawl-delay":
                try:
                    rules.crawl_delay = float(v)
                except ValueError:
                    pass
    return groups


def pick_robots_group(groups: dict, user_agent: str) -> RobotsRules | None:
    """Choisit le groupe robots le plus spécifique pour un user-agent."""
    ua_low = user_agent.lower()
    for key in (ua_low, "python", "*"):
        if key in groups:
            return groups[key]
    return None


def robots_path_allowed(rules: RobotsRules | None, url: str) -> bool:
    if rules is None:
        return True
    return rules.allowed(url)


def load_robots(session: requests.Session, scheme: str, host: str,
                user_agent: str) -> RobotsRules | None:
    """Télécharge robots.txt et renvoie le groupe applicable (None si absent)."""
    try:
        r = session.get(f"{scheme}://{host}/robots.txt", timeout=DEFAULT_TIMEOUT)
        if r.status_code != 200:
            return None
    except requests.RequestException:
        return None
    return pick_robots_group(parse_robots(r.text), user_agent)


class Throttle:
    """Limiteur de débit par domaine, sûr en multithread.

    Combine --rate (req/s) et les Crawl-delay robots par hôte. Le slot est
    réservé sous verrou, le sommeil se fait hors verrou.
    """

    def __init__(self, rate: float = 0.0):
        self.rate = max(0.0, rate)
        self._lock = threading.Lock()
        self._last = {}
        self._delays = {}

    def set_delay(self, host: str, seconds: float):
        with self._lock:
            self._delays[host] = max(0.0, seconds)

    def delay_for(self, host: str) -> float:
        """Délai en vigueur pour un hôte (sert au backoff adaptatif v2.4.0)."""
        with self._lock:
            interval = self._delays.get(host, 0.0)
            if self.rate > 0:
                interval = max(interval, 1.0 / self.rate)
            return interval

    def wait(self, url: str):
        host = urllib.parse.urlsplit(url).hostname or ""
        with self._lock:
            interval = self._delays.get(host, 0.0)
            if self.rate > 0:
                interval = max(interval, 1.0 / self.rate)
            if interval <= 0:
                return
            now = time.time()
            wait = interval - (now - self._last.get(host, 0.0))
            if wait > 0:
                self._last[host] = now + wait
            else:
                self._last[host] = now
        if wait > 0:
            time.sleep(wait)


def _retry_after_delay(response, attempt: int) -> float:
    """Délai avant retry d'un 429/503 : honore Retry-After (secondes), sinon
    backoff exponentiel borné. Le résultat est plafonné pour ne jamais
    bloquer le clone trop longtemps sur une ressource."""
    if response is not None:
        ra = response.headers.get("Retry-After")
        if ra:
            try:
                return min(max(0.0, float(ra)), RATE_LIMIT_BACKOFF_MAX)
            except ValueError:
                pass
    return min(2.0 ** attempt, RATE_LIMIT_BACKOFF_MAX)


def fetch_once(session: requests.Session, url: str, max_size: int,
               throttle: Throttle | None = None, referer: str | None = None,
               on_login=None) -> FetchResult:
    """Télécharge une URL en UN SEUL GET, lu par chunks bornés, avec retries.

    Classification des statuts HTTP :
    - 404/410  -> DeadLinkError (lien mort, non retenté) ;
    - 401      -> ProtectedError (ressource protégée, non retentée) ;
    - 403      -> retenté une fois avec `Referer` (anti-hotlink), puis
                  ProtectedError ;
    - 429/503  -> RateLimitedError, retenté avec backoff (Retry-After honoré) ;
    - challenge Cloudflare (v2.5.0 : cf-mitigated/Server + marqueurs « Just a
                  moment... ») -> CloudflareChallengeError, retenté avec
                  backoff puis classé comme ressource protégée ;
    - autres >=400 -> RuntimeError (après retries).
    `on_login` est appelé si la réponse finale pointe vers une page de login.
    """
    if throttle:
        throttle.wait(url)
    last = None
    referer_via_403 = False
    for attempt in range(1, MAX_RETRIES + 1):
        r = None
        try:
            headers = {"Referer": referer} if referer else None
            r = session.get(url, timeout=DEFAULT_TIMEOUT, allow_redirects=True,
                            stream=True, headers=headers)
            try:
                if r.status_code in (404, 410):
                    raise DeadLinkError(f"HTTP {r.status_code} (lien mort)")
                if (r.status_code in (403, 503)
                        or "challenge" in str(r.headers.get("cf-mitigated")
                                              or "").lower()):
                    probe = b""
                    for chunk in r.iter_content(chunk_size=65536):
                        probe += chunk
                        if len(probe) > CHALLENGE_PROBE_MAX:
                            break
                    if is_cloudflare_challenge(r.headers, probe):
                        raise CloudflareChallengeError(
                            f"HTTP {r.status_code} "
                            f"(challenge Cloudflare)")
                if (r.status_code == 403 and referer is None
                        and not referer_via_403):
                    referer_via_403 = True
                    referer = url
                    last = ProtectedError(
                        "HTTP 403 sans Referer (essai avec Referer)")
                    continue
                if r.status_code in (401, 403):
                    raise ProtectedError(
                        f"HTTP {r.status_code} (ressource protégée)")
                if r.status_code in (429, 503):
                    raise RateLimitedError(
                        f"HTTP {r.status_code} (limite de débit)")
                if r.status_code >= 400:
                    raise RuntimeError(f"HTTP {r.status_code}")
                chunks = []
                total = 0
                for chunk in r.iter_content(chunk_size=65536):
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_size:
                        raise RuntimeError("fichier trop volumineux")
                content = b"".join(chunks)
            finally:
                r.close()
            final = r.url or url
            if on_login and LOGIN_PATH_RE.search(urllib.parse.urlsplit(final).path):
                on_login()
            return FetchResult(content, r.headers, final)
        except (DeadLinkError, ProtectedError):
            raise
        except RateLimitedError as e:
            last = e
            if attempt < MAX_RETRIES:
                time.sleep(_retry_after_delay(r, attempt))
        except CloudflareChallengeError as e:
            last = e
            if attempt < MAX_RETRIES:
                time.sleep(_retry_after_delay(r, attempt))
        except Exception as e:
            last = e
            if attempt < MAX_RETRIES:
                time.sleep(1.0 * attempt)
    if isinstance(last, (RateLimitedError, ProtectedError,
                         CloudflareChallengeError)):
        raise last
    raise RuntimeError(f"{last}")
