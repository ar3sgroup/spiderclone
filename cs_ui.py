# -*- coding: utf-8 -*-
"""
by xyrek from ar3s — 2026-09-22

Interface terminal thematisee : banniere ASCII, marqueurs colories [+] [*] [!], invite interactive, controle des couleurs.
"""

import sys

RED = "\x1b[1;31m"
GREEN = "\x1b[1;32m"
BLUE = "\x1b[1;34m"
RESET = "\x1b[0m"

_use_color = None

try:
    import colorama

    colorama.just_fix_windows_console()
except Exception:
    pass


def set_color(enabled):
    """Force ou relâche le mode couleur.

    enabled=True  -> couleurs toujours actives
    enabled=False -> couleurs désactivées
    enabled=None  -> retour au mode automatique (isatty)
    """
    global _use_color
    _use_color = enabled


def _color_enabled():
    if _use_color is None:
        try:
            return bool(sys.stdout.isatty())
        except Exception:
            return False
    return bool(_use_color)


def _paint_spans(text, spans):
    """Encadre les tranches [start, end) de *text* avec RED/RESET.

    Les bornes sont clampées à la longueur réelle de la ligne ; les
    tranches vides ou hors bornes sont ignorées. Sans couleur, renvoie
    le texte tel quel.
    """
    if not _color_enabled() or not spans:
        return text
    out = []
    pos = 0
    for a, b in spans:
        a = max(0, min(a, len(text)))
        b = max(a, min(b, len(text)))
        if a >= b:
            continue
        out.append(text[pos:a])
        out.append(RED + text[a:b] + RESET)
        pos = b
    out.append(text[pos:])
    return "".join(out)


def _marker(ch, msg):
    """Construit « [ch] msg » avec les crochets en rouge (si couleur)."""
    if _color_enabled():
        return RED + "[" + ch + "]" + RESET + " " + msg
    return "[" + ch + "] " + msg


def step(msg):
    """Marqueur d'étape  [+] msg  (démarrage, clonage)."""
    return _marker("+", msg)


def info(msg):
    """Marqueur d'information  [*] msg  (sitemap, reprise, archive...)."""
    return _marker("*", msg)


def warn(msg):
    """Marqueur d'avertissement  [!] msg  (page de connexion, config...)."""
    return _marker("!", msg)


SPIDER = (
    "               :   ...       .",
    "             .:.  o.          .:  ..",
    "             :.  .:            :   ::",
    "      :      o.  o     :  .    .:  ::     .",
    "     o.      o   o.   .#..o    .:  ::      :",
    "     .o      .o  .:.  oo..#   .#   :.      #",
    "      .:      ::.  .:o#####o::.  .o.      o:",
    " .:    :....   . ...o#######o....:.      o.",
    ".:         .:::::...:##oo###:..:.:...::..:    :",
    ":.             ...:##: ::..##o    .:.          o",
    " o         .:::..:### .##: o##o:::.            o",
    " :o.:::::oo:.  .:###o.... .####.  .:o.        o",
    "           .:::::###  #### .###o::  .:o:::.:.o.",
    "         .o:    :o##  ###o .##o  .:::.",
    "       .o:   .::. .#o.. ..:#o..o:   .o.",
    "    .:::     #:     .o###o:.    :#.   o:",
    "    .o       ::                 :o     .:o",
    "     o       #.                 ::       o.",
    "     .:     .#                  ::      o.",
    "     .:     o:                  .#.    .:",
    "      o.     ::                .::     ::",
    "       .:.    .:              .o.    .::",
    "               o.            .o     .",
    "               .::          ::.",
    "                  ..     ...",
)

_RED_SPANS = {
    8: [(23, 25)],
}

BANNER_WIDTH = 58

_FONT = {
    "c": (" ###", "#   ", "#   ", "#   ", " ###"),
    "d": ("### ", "#  #", "#  #", "#  #", "### "),
    "l": ("#   ", "#   ", "#   ", "#   ", "####"),
    "n": ("#  #", "## #", "# ##", "#  #", "#  #"),
    "o": (" ## ", "#  #", "#  #", "#  #", " ## "),
    "p": ("####", "#  #", "####", "#   ", "#   "),
    "i": ("  # ", "    ", "  # ", "  # ", "  # "),
    "e": (" ## ", "#  #", "####", "#   ", " ## "),
    "u": ("#  #", "#  #", "#  #", "#  #", " ## "),
    "r": ("####", "#  #", "#   ", "#   ", "#   "),
    "s": (" ###", "#   ", " ## ", "   #", "### "),
    "t": (" ###", "  # ", "  # ", "  # ", "  ##"),
}

CLONE_START = 5 * len("spider")


def _render(word):
    """Rend un mot en blocs 5x4 (une chaîne par ligne, sans séparateur final)."""
    lines = [""] * 5
    for k, ch in enumerate(word):
        sep = " " if k else ""
        glyph = _FONT[ch]
        for i in range(5):
            lines[i] += sep + glyph[i]
    return lines


def _wordmark():
    """Lettrage « spider » (blanc) + « clone » (rouge), largeur 54."""
    lines = _render("spider" + "clone")
    spans = [(CLONE_START, len(lines[0]))]
    return [_paint_spans(line, spans) for line in lines]


TAGLINE = "> Clone · Mirror · Preserve <"
_TAGLINE_SPANS = [(0, 1), (28, 29)]

BOOT_LINES = (
    "Initialisation du moteur de clonage...",
    "Chargement des modules...",
    "Bonne exploration !",
)

PROMPT = "user@spiderclone:~$"


def prompt() -> str:
    """Invite interactive « user@spiderclone:~$ » (colorée si activé)."""
    if _color_enabled():
        return (GREEN + "user@spiderclone" + RESET
                + BLUE + ":~$" + RESET + " ")
    return PROMPT + " "


def banner_lines():
    """Renvoie la liste des lignes de la bannière (couleurs selon réglage)."""
    lines = [r.rstrip() for r in SPIDER]
    lines.append("")
    lines.extend("  " + line for line in _wordmark())
    lines.append("")
    lines.append(" " * 14 + _paint_spans(TAGLINE, _TAGLINE_SPANS))
    lines.append("")
    lines.append("")
    lines.extend("  " + line for line in BOOT_LINES)
    lines.append("")
    lines.append("  " + _paint_spans(PROMPT, [(0, len(PROMPT))]))
    return lines


def print_banner():
    """Affiche la bannière sur stdout."""
    for line in banner_lines():
        print(line)
