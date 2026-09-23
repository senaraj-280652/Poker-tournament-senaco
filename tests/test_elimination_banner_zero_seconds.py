"""Test ciblé de la règle "Durée du bandeau d'élimination" = 0 (demande
du 2026-09-07) :

- durée > 0 s : le bandeau fonctionne normalement (comportement déjà
  existant, non modifié — juste revérifié ici en non-régression) ;
- durée = 0 s : AUCUN bandeau ne doit apparaître (ni onglet Chronomètre
  ni écran projecteur — les deux sont entièrement gouvernés par le même
  état, self._elimination_banner_current, voir _refresh_clock_tab et
  ClockWindow.refresh) ;
- le son (_play_elimination_sound, réglage totalement indépendant —
  voir sa docstring) continue de jouer normalement dans les deux cas :
  volontairement PAS désactivé par 0 s (comportement AVANT correctif,
  jamais changé).

AVANT ce correctif : trois clamps distincts forçaient silencieusement 0
à 1 (widget Spinbox from_=1, lecture avec max(1, ...), écriture avec
max(1, ...)) — 0 s ne pouvait donc jamais être réellement enregistré ni
appliqué ; le bandeau s'affichait quand même, juste 1 seconde. Corrigé
en changeant les trois planchers de 1 à 0."""
import os
import sys
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class _FakeDb:
    def __init__(self, elimination_banner_seconds=5):
        self._elimination_banner_seconds = elimination_banner_seconds
        self.set_settings_calls = []

    def get_setting_int(self, key, default=0):
        if key == "elimination_banner_seconds":
            return self._elimination_banner_seconds
        return default

    def set_settings(self, values):
        self.set_settings_calls.append(values)


class _FakeVar:
    def __init__(self, value):
        self._value = value

    def get(self):
        return self._value


class _FakeApp:
    """Doublure de App : ne reprend que ce que _advance_elimination_
    banner et _save_elimination_banner_seconds lisent ou appellent."""

    def __init__(self, elimination_banner_seconds=5, queue=None):
        import collections

        self.db = _FakeDb(elimination_banner_seconds)
        self._elimination_banner_queue = collections.deque(queue or [])
        self._elimination_banner_current = None
        self.sound_calls = 0

    def _play_elimination_sound(self):
        self.sound_calls += 1


def _job(name="Alice", eliminator="Bob"):
    return {"eliminated_name": name, "eliminator_name": eliminator}


class BannerDisabledAtZeroSecondsTest(unittest.TestCase):
    def test_duree_zero_aucun_bandeau_ne_devient_courant(self):
        fake = _FakeApp(elimination_banner_seconds=0, queue=[_job()])
        main.App._advance_elimination_banner(fake)
        self.assertIsNone(fake._elimination_banner_current)

    def test_duree_zero_le_son_joue_quand_meme(self):
        """Le coeur de la demande : 0 s désactive l'AFFICHAGE, jamais le
        son — réglage indépendant, comportement à préserver."""
        fake = _FakeApp(elimination_banner_seconds=0, queue=[_job()])
        main.App._advance_elimination_banner(fake)
        self.assertEqual(fake.sound_calls, 1)

    def test_duree_zero_avec_plusieurs_eliminations_en_file(self):
        """Un seul élément dépilé par appel (comportement existant,
        inchangé) : le bandeau reste None, le son ne joue qu'une fois
        pour CET appel, les autres restent en file pour le prochain
        passage (voir _tick, qui rappellera cette fonction)."""
        fake = _FakeApp(elimination_banner_seconds=0, queue=[_job("A"), _job("B")])
        main.App._advance_elimination_banner(fake)
        self.assertIsNone(fake._elimination_banner_current)
        self.assertEqual(fake.sound_calls, 1)
        self.assertEqual(len(fake._elimination_banner_queue), 1)


class BannerNormalAboveZeroSecondsTest(unittest.TestCase):
    def test_duree_1s_bandeau_devient_courant_avec_echeance(self):
        fake = _FakeApp(elimination_banner_seconds=1, queue=[_job()])
        before = time.time()
        main.App._advance_elimination_banner(fake)
        after = time.time()
        self.assertIsNotNone(fake._elimination_banner_current)
        self.assertEqual(fake._elimination_banner_current["eliminated_name"], "Alice")
        self.assertTrue(before + 1 <= fake._elimination_banner_current["until"] <= after + 1)

    def test_duree_5s_par_defaut_fonctionne_normalement(self):
        fake = _FakeApp(elimination_banner_seconds=5, queue=[_job()])
        main.App._advance_elimination_banner(fake)
        self.assertIsNotNone(fake._elimination_banner_current)

    def test_duree_superieure_a_zero_le_son_joue_aussi(self):
        """Non-régression : le son jouait déjà dans ce cas avant le
        correctif, continue de jouer."""
        fake = _FakeApp(elimination_banner_seconds=5, queue=[_job()])
        main.App._advance_elimination_banner(fake)
        self.assertEqual(fake.sound_calls, 1)

    def test_file_vide_aucun_effet_aucun_son(self):
        fake = _FakeApp(elimination_banner_seconds=5, queue=[])
        main.App._advance_elimination_banner(fake)
        self.assertIsNone(fake._elimination_banner_current)
        self.assertEqual(fake.sound_calls, 0)


class SaveAndReloadSettingTest(unittest.TestCase):
    def test_enregistre_bien_0_sans_le_remonter_a_1(self):
        fake = _FakeApp()
        main.App._save_elimination_banner_seconds(fake, _FakeVar(0))
        self.assertEqual(fake.db.set_settings_calls, [{"elimination_banner_seconds": 0}])

    def test_enregistre_une_valeur_normale_inchangee(self):
        fake = _FakeApp()
        main.App._save_elimination_banner_seconds(fake, _FakeVar(10))
        self.assertEqual(fake.db.set_settings_calls, [{"elimination_banner_seconds": 10}])

    def test_valeur_negative_plafonnee_a_0_pas_a_1(self):
        fake = _FakeApp()
        main.App._save_elimination_banner_seconds(fake, _FakeVar(-3))
        self.assertEqual(fake.db.set_settings_calls, [{"elimination_banner_seconds": 0}])

    def test_valeur_trop_grande_plafonnee_a_30(self):
        fake = _FakeApp()
        main.App._save_elimination_banner_seconds(fake, _FakeVar(999))
        self.assertEqual(fake.db.set_settings_calls, [{"elimination_banner_seconds": 30}])

    def test_lecture_0_enregistre_est_bien_relue_a_0(self):
        """Persistance : une fois 0 enregistré (voir get_setting_int
        mocké pour renvoyer exactement ce qui a été "sauvegardé"),
        _advance_elimination_banner doit continuer à désactiver
        l'affichage — pas de remontée à 1 nulle part à la relecture."""
        fake = _FakeApp(elimination_banner_seconds=0, queue=[_job()])
        main.App._advance_elimination_banner(fake)
        self.assertIsNone(fake._elimination_banner_current)


class TooltipTest(unittest.TestCase):
    def test_tooltip_mentionne_la_desactivation_a_0(self):
        """Vérifie le texte source directement (pas de fenêtre Tk
        nécessaire) : la phrase demandée est bien présente, ajoutée au
        tooltip existant plutôt que le remplacer."""
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        start = source.index('elim_lbl = ttk.Label(right, text="Durée du bandeau')
        end = source.index("elim_seconds_var = tk.IntVar", start)
        tooltip_block = source[start:end]
        self.assertIn("Mettre 0 seconde pour désactiver l'affichage du bandeau", tooltip_block)
        # Le reste du tooltip (indépendance du son, etc.) doit rester présent.
        self.assertIn("Indépendante", tooltip_block)
        self.assertIn("de 0 à 30 secondes", tooltip_block)

    def test_widget_accepte_bien_0_desormais(self):
        source_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
        with open(source_path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn('right, from_=0, to=30, width=5, textvariable=elim_seconds_var', source)


class SpinboxTooltipTest(unittest.TestCase):
    """Demande du 2026-09-08 : la Spinbox elle-même (elim_spin), pas
    seulement son libellé (elim_lbl, déjà couvert par TooltipTest
    ci-dessus), doit avoir son propre tooltip — jusqu'ici un survol
    direct de la Spinbox n'affichait rien."""

    def _spinbox_block(self):
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
                   encoding="utf-8") as f:
            source = f.read()
        start = source.index("elim_spin = ttk.Spinbox(")
        # Marqueur de fin (demande du 2026-09-22, "Durée du bandeau
        # d'élimination"/"Timeout pour Annuler Eliminer" déplacées sous
        # "Un seul tournoi à la fois", colonne droite de Paramètres) :
        # ce bloc elim_spin est désormais suivi directement du bloc
        # "Timeout pour Annule Eliminer", plus de "# -- Colonne droite"
        # juste après (ce marqueur précède maintenant tout le bloc, pas
        # seulement elim_spin).
        end = source.index('# -- Timeout pour "Annule Eliminer"', start)
        return source[start:end]

    def test_tooltip_ajoute_sur_la_spinbox_avec_le_texte_demande(self):
        """Texte exact demandé le 2026-09-08 (révisé le même jour pour
        mentionner explicitement le cas 0 seconde) — les deux littéraux
        adjacents du code source sont vérifiés séparément, Python les
        concatène sans rien ajouter entre eux (pas de séparateur de
        ligne dans la chaîne finale)."""
        block = self._spinbox_block()
        idx = block.index("Tooltip(\n            elim_spin,")
        call_block = block[idx:block.index(")", idx) + 1]
        self.assertIn('"Durée d\'affichage du bandeau d\'élimination, en secondes. "', call_block)
        self.assertIn('"Si 0, le bandeau n\'est pas affiché.",', call_block)

    def test_valeur_et_limites_de_la_spinbox_inchangees(self):
        """Non-régression explicite : l'ajout du tooltip ne doit pas
        toucher from_/to/width/textvariable/command ni les bindings
        Return/FocusOut déjà existants."""
        block = self._spinbox_block()
        self.assertIn(
            "elim_spin = ttk.Spinbox(\n"
            "            right, from_=0, to=30, width=5, textvariable=elim_seconds_var,\n"
            "            command=lambda: self._save_elimination_banner_seconds(elim_seconds_var),\n"
            "        )",
            block,
        )
        self.assertIn('elim_spin.bind(\n            "<Return>"', block)
        self.assertIn('elim_spin.bind(\n            "<FocusOut>"', block)

    def test_tooltip_de_la_spinbox_distinct_de_celui_du_libelle(self):
        """Le tooltip du libellé (détaillé, voir TooltipTest) reste
        inchangé et distinct de celui — volontairement plus court — de
        la Spinbox elle-même."""
        block = self._spinbox_block()
        self.assertNotIn("Indépendante", block)
        self.assertNotIn("Mettre 0 seconde pour désactiver", block)


if __name__ == "__main__":
    unittest.main()
