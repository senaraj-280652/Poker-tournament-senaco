# -*- coding: utf-8 -*-
"""Hauteur de la fenêtre "Bienvenue" (Menu principal) — correctif du
2026-10-12 signalé après essai manuel : l'ajout du bouton "🗑️ Purger les
données" faisait dépasser "ℹ️ À propos" hors de la fenêtre (toujours non
redimensionnable, voir App._choose_tournament_file), le bas du bouton
étant coupé.

ATTENTION (voir tests/test_primes_settings_inheritance.py, docstring du
module, et la convention déjà établie dans cette suite) : piloter
RÉELLEMENT _choose_tournament_file() de bout en bout (son wait_window()
final) a déjà provoqué un SIGSEGV Tcl/Tk reproductible dans ce harnais
de tests, lors d'une tentative antérieure (wait_window imbriqué depuis
un callback after() programmé) — JAMAIS à reproduire. Ce fichier ne
pilote donc JAMAIS cette méthode ni son wait_window : il reconstruit à
l'identique, sur un tk.Toplevel ordinaire (sans wait_window ni
grab_set), les SEULS widgets qui déterminent la hauteur requise — les 3
libellés d'en-tête, puis TOUS les boutons de btn_frame, dont la liste
EXACTE (texte, largeur, espacement) est extraite directement du source
de main.py (jamais recopiée à la main, pour qu'un futur bouton ajouté
ou retiré fasse automatiquement réagir ce test, sans qu'il faille penser
à le mettre à jour à la main).

Couvre :
1. La hauteur CONFIGURÉE (win.geometry) est suffisante pour contenir
   réellement tous les boutons actuels, marge raisonnable comprise —
   pas seulement "ne plante pas", une vraie mesure Tk (winfo_reqheight).
2. La largeur reste 480 (inchangée, demande explicite).
3. Tous les boutons gardent exactement la même largeur (width=28) et le
   même espacement (pady=6) qu'avant ce correctif — seule la hauteur de
   fenêtre change, rien d'autre.
4. Le bouton "À propos" (dernier de la liste, celui qui était coupé)
   tient entièrement dans la hauteur configurée."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402
from tkinter import ttk  # noqa: E402

from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _reqheight_with_pady(widget, pady):
    """Hauteur requise par CE widget seul (winfo_reqheight(), disponible
    immédiatement après sa création — jamais besoin d'idle-task pour la
    taille naturelle PROPRE d'un widget, contrairement à la hauteur
    AGRÉGÉE de son parent, voir plus bas) + son espacement pack(pady=...).

    AVERTISSEMENT IMPORTANT (diagnostic du 2026-10-12, isolé par
    bissection jusqu'à une reproduction minimale de 6 lignes) : appeler
    Toplevel.update_idletasks()/update() dans CE harnais de tests
    (Python 3.14/Tcl-Tk/macOS) provoque un SIGSEGV reproductible à 100%
    dès lors que ce Toplevel pend d'un tk.Tk() qui n'est pas le tout
    premier du processus — systématiquement vrai ici, puisque des
    dizaines d'autres fichiers de cette suite créent/détruisent déjà
    leur propre racine AVANT que celui-ci ne s'exécute lors d'un
    `discover`. Jamais reproduit avec wait_window()/grab_set() (déjà
    proscrits par ailleurs, voir tests/test_primes_settings_inheritance.
    py) : un déclencheur DIFFÉRENT, documenté ici précisément pour qu'il
    ne soit pas réintroduit par erreur. D'où ce calcul ARITHMÉTIQUE
    (somme des winfo_reqheight() individuels + pady connus), qui
    reproduit EXACTEMENT (vérifié : 521px dans les deux cas) la valeur
    qu'update_idletasks() aurait donnée, sans jamais l'appeler."""
    if isinstance(pady, tuple):
        top, bottom = pady
    else:
        top = bottom = pady
    return widget.winfo_reqheight() + top + bottom


def _main_source():
    with open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
        encoding="utf-8",
    ) as f:
        return f.read()


def _choose_tournament_file_body(source):
    start = source.index("def _choose_tournament_file(self):")
    # Prochaine méthode d'App au même niveau d'indentation (4 espaces,
    # "    def ") après celle-ci — borne de fin fiable, même principe
    # que les autres tests structurels de cette suite.
    end = source.index("\n    def ", start + 1)
    return source[start:end]


def _configured_geometry():
    """("480x567" par ex.) -> (480, 567) — lue directement dans le
    source, jamais recopiée en dur ici (sinon ce test continuerait de
    passer même si quelqu'un oubliait de l'ajuster après un nouveau
    bouton)."""
    body = _choose_tournament_file_body(_main_source())
    match = re.search(r'win\.geometry\("(\d+)x(\d+)"\)', body)
    assert match, "win.geometry(\"WxH\") introuvable dans _choose_tournament_file"
    return int(match.group(1)), int(match.group(2))


def _button_specs():
    """[("🆕  Nouveau tournoi", 28), ...] — texte et largeur de CHAQUE
    bouton de btn_frame, dans l'ordre réel du source. Chaque définition
    est bornée par le ".pack(pady=" qui la termine (jamais par le
    prochain ")" tout court : plusieurs boutons ont un command=lambda
    contenant lui-même des parenthèses, ex. LobbyDialog(win) ou
    self._show_about(win))."""
    body = _choose_tournament_file_body(_main_source())
    specs = []
    for m in re.finditer(r'btn_frame, text="([^"]*)"', body):
        pack_match = re.search(r"\.pack\(pady=", body[m.end():])
        assert pack_match, f"Pas de .pack(pady=...) trouvé après le bouton {m.group(1)!r}"
        segment = body[m.end():m.end() + pack_match.start()]
        width_match = re.search(r"width=(\d+)", segment)
        assert width_match, f"Pas de width=N trouvé pour le bouton {m.group(1)!r}"
        specs.append((m.group(1), int(width_match.group(1))))
    return specs


class ConfiguredGeometryTest(unittest.TestCase):
    def test_largeur_inchangee_480(self):
        width, _height = _configured_geometry()
        self.assertEqual(width, 480)

    def test_hauteur_augmentee_depuis_527(self):
        """Verrouille le FAIT qu'elle a bien changé (pas la valeur
        précise, mesurée séparément ci-dessous de façon réaliste) —
        527 était la hauteur juste avant l'ajout du bouton Purger."""
        _width, height = _configured_geometry()
        self.assertGreater(height, 527)


class ButtonDimensionsUnchangedTest(unittest.TestCase):
    """"Conserver... les dimensions des boutons et leur espacement"."""

    def test_tous_les_boutons_gardent_width_28(self):
        specs = _button_specs()
        self.assertGreaterEqual(len(specs), 9)  # au moins les 9 boutons connus
        for text, width in specs:
            self.assertEqual(width, 28, f"Bouton {text!r} : largeur inattendue {width}")

    def test_tous_les_boutons_gardent_pady_6(self):
        body = _choose_tournament_file_body(_main_source())
        # Chaque bouton de btn_frame est suivi de ".pack(pady=6)" (sur sa
        # propre ligne ou sur celle du Tooltip voisin) — compte le nombre
        # d'occurrences de "pady=6" directement associées à un .pack()
        # de bouton, doit égaler le nombre de boutons.
        pack_pady_6 = re.findall(r"\.pack\(pady=6\)", body)
        specs = _button_specs()
        self.assertEqual(len(pack_pady_6), len(specs))

    def test_purger_present_entre_restaurer_et_parametres(self):
        """Non-régression du placement validé précédemment."""
        specs = [text for text, _w in _button_specs()]
        self.assertIn("♻️  Restaurer depuis une clé USB", specs)
        self.assertIn("🗑️  Purger les données", specs)
        self.assertIn("⚙️  Paramètres de base", specs)
        idx_restore = specs.index("♻️  Restaurer depuis une clé USB")
        idx_purge = specs.index("🗑️  Purger les données")
        idx_settings = specs.index("⚙️  Paramètres de base")
        self.assertEqual(idx_purge, idx_restore + 1)
        self.assertEqual(idx_settings, idx_purge + 1)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class RealRenderedHeightFitsTest(unittest.TestCase):
    """Mesure RÉELLE (Tk) de la hauteur requise par le contenu actuel —
    reconstruction fidèle, JAMAIS App._choose_tournament_file() elle-
    même (voir l'avertissement en tête de fichier). Les libellés d'en-
    tête sont recopiés tels quels (3 lignes fixes, stables depuis
    longtemps, hors du champ de cette demande) ; la liste des boutons,
    elle, est entièrement dérivée du source (_button_specs) pour rester
    automatiquement à jour.

    Racine Tk et nettoyage STANDARD de cette suite (setUpClass/
    tearDownClass + cleanup_tk, même convention que des dizaines
    d'autres fichiers) — rendu possible ici car la hauteur totale est
    calculée ARITHMÉTIQUEMENT (_reqheight_with_pady), SANS jamais
    appeler update_idletasks()/update() : voir sa docstring pour le
    diagnostic précis du SIGSEGV que cela évite."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self.win = None
        self.addCleanup(lambda: cleanup_tk(self, "win"))

    def _build_replica_and_measure(self):
        """Construit la même arborescence que _choose_tournament_file
        (3 libellés d'en-tête + btn_frame avec tous les boutons réels) et
        renvoie (hauteur_totale_requise, dernier_bouton, y_du_dernier_
        bouton) — jamais via update_idletasks (voir _reqheight_with_pady).
        y_du_dernier_bouton est calculé en cumulant les hauteurs
        précédentes, PAS via winfo_y() (qui exigerait lui aussi un idle-
        task réel pour être fiable après pack())."""
        FELT_DARK = "#0b241a"
        GOLD = "#d4af37"
        CREAM = "#f5f0e1"
        MUTED = "#9fb8a8"
        self.win = tk.Toplevel(self.root)
        self.win.configure(bg=FELT_DARK)

        total = 0
        l1 = tk.Label(
            self.win, text="♠ ♥ ♦ ♣", bg=FELT_DARK, fg=GOLD, font=("Helvetica", 22, "bold"),
        )
        l1.pack(pady=(28, 4))
        total += _reqheight_with_pady(l1, (28, 4))
        l2 = tk.Label(
            self.win, text="Senaco", bg=FELT_DARK, fg=CREAM, font=("Helvetica", 16, "bold"),
        )
        l2.pack(pady=(0, 6))
        total += _reqheight_with_pady(l2, (0, 6))
        l3 = tk.Label(
            self.win, text="Créez un nouveau tournoi ou reprenez-en un en cours",
            bg=FELT_DARK, fg=MUTED, font=("Helvetica", 10),
        )
        l3.pack(pady=(0, 24))
        total += _reqheight_with_pady(l3, (0, 24))

        btn_frame = tk.Frame(self.win, bg=FELT_DARK)
        btn_frame.pack(pady=4)
        total += 4 + 4  # pady=4 (haut+bas) de btn_frame lui-même

        last_btn = None
        y_of_last_btn = None
        for text, width in _button_specs():
            last_btn = ttk.Button(btn_frame, text=text, width=width)
            last_btn.pack(pady=6)
            total += 6  # moitié haute du pady de CE bouton, avant de l'ajouter
            y_of_last_btn = total
            total += last_btn.winfo_reqheight() + 6  # son contenu + moitié basse

        return total, last_btn, y_of_last_btn

    def test_hauteur_configuree_contient_tout_le_contenu_reel(self):
        total_height, _last_btn, _y = self._build_replica_and_measure()
        _configured_width, configured_height = _configured_geometry()
        self.assertLessEqual(
            total_height, configured_height,
            "Le contenu réel dépasse la hauteur configurée : au moins un "
            "bouton serait coupé, exactement le bug signalé.",
        )

    def test_marge_raisonnable_en_bas(self):
        """Pas seulement "ça tient" — une vraie marge visible sous le
        dernier bouton, comme avant ce correctif (46px, voir le
        commentaire de win.geometry dans main.py) : au moins 20px."""
        total_height, _last_btn, _y = self._build_replica_and_measure()
        _configured_width, configured_height = _configured_geometry()
        margin = configured_height - total_height
        self.assertGreaterEqual(margin, 20)

    def test_bouton_a_propos_entierement_visible(self):
        """LE bouton signalé coupé — vérifié directement via sa position
        cumulée + sa hauteur réelle, dans la hauteur configurée."""
        total_height, last_btn, y_of_last_btn = self._build_replica_and_measure()
        _configured_width, configured_height = _configured_geometry()
        bottom_of_last_button = y_of_last_btn + last_btn.winfo_reqheight()
        self.assertLessEqual(bottom_of_last_button, total_height)
        self.assertLessEqual(total_height, configured_height)


if __name__ == "__main__":
    unittest.main()
