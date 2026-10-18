# -*- coding: utf-8 -*-
"""Pré-remplissage du champ "Dossier des tournois" de l'onglet
Statistiques (demande du 2026-10-15) :

- Quand AUCUN tournoi n'est ouvert (écran "Menu principal", App._build_
  tabs avec self.db is None — voir main.py:10265) : le champ doit
  reprendre le dossier par défaut des PARAMÈTRES GÉNÉRAUX (tournament_
  prefs.load_last_settings()["tournament_day_folder"], mémorisé
  indépendamment de tout tournoi dans ~/.poker_tournament/last_settings.
  json), jamais rester vide si ce réglage existe.
- Quand un tournoi EST ouvert : comportement STRICTEMENT inchangé (son
  propre réglage, sinon le dossier contenant son fichier .tournoi) — le
  nouveau repli n'intervient QUE si tout le reste est encore vide.
- "Parcourir..." reste entièrement fonctionnel, sans rapport avec ce
  pré-remplissage initial (voir _browse_folder, non modifiée).
- Mécanisme déjà utilisé de façon identique ailleurs dans l'app sur Mac
  et Windows (simple lecture d'un fichier JSON du profil utilisateur,
  aucun code spécifique à une plateforme) — voir tournament_prefs.py."""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tkinter as tk  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import tournament_prefs  # noqa: E402
from _tk_cleanup import cleanup_tk  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


class _StubApp:
    db = None


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class StatsDefaultFolderStandaloneTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cleanup_tk(cls, "root")

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_default_folder_")
        self.addCleanup(self._tmp.cleanup)
        prefs_path = os.path.join(self._tmp.name, "last_settings.json")
        patcher = patch.object(tournament_prefs, "_prefs_path", return_value=prefs_path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_sans_tournoi_ouvert_reprend_le_dossier_general_par_defaut(self):
        tournament_prefs.save_last_settings({"tournament_day_folder": "/dossier/general"})
        dlg = main.PeriodSummaryDialog(self.root, _StubApp())
        self.addCleanup(lambda: cleanup_tk(self, "dlg"))
        self.assertEqual(dlg.folder_var.get(), "/dossier/general")

    def test_sans_tournoi_ouvert_et_sans_reglage_general_reste_vide(self):
        """Non-régression : aucun réglage général enregistré -> le champ
        reste vide (comportement d'avant ce correctif), jamais une
        valeur inventée."""
        dlg = main.PeriodSummaryDialog(self.root, _StubApp())
        self.addCleanup(lambda: cleanup_tk(self, "dlg"))
        self.assertEqual(dlg.folder_var.get(), "")

    def test_tournoi_ouvert_avec_son_propre_reglage_najamais_ecrase(self):
        """Non-régression centrale : un tournoi ouvert avec SON PROPRE
        dossier réglé garde cette valeur, même si le réglage général
        pointe ailleurs — le nouveau repli ne doit jamais prendre le
        pas sur un réglage déjà existant et fonctionnel."""
        tournament_prefs.save_last_settings({"tournament_day_folder": "/dossier/general"})
        db = database.Database(os.path.join(self._tmp.name, "T.tournoi"))
        self.addCleanup(db.close)
        db.set_settings({"tournament_day_folder": "/dossier/propre/au/tournoi"})
        app_stub = type("A", (), {"db": db})()
        dlg = main.PeriodSummaryDialog(self.root, app_stub)
        self.addCleanup(lambda: cleanup_tk(self, "dlg"))
        self.assertEqual(dlg.folder_var.get(), "/dossier/propre/au/tournoi")

    def test_tournoi_ouvert_sans_reglage_propre_retombe_sur_son_dossier(self):
        """Non-régression : un tournoi ouvert SANS réglage propre garde
        le repli existant (le dossier qui le contient), jamais le
        réglage général — même si celui-ci est renseigné par ailleurs."""
        tournament_prefs.save_last_settings({"tournament_day_folder": "/dossier/general"})
        sub = os.path.join(self._tmp.name, "sous_dossier")
        os.makedirs(sub, exist_ok=True)
        db = database.Database(os.path.join(sub, "T.tournoi"))
        self.addCleanup(db.close)
        app_stub = type("A", (), {"db": db})()
        dlg = main.PeriodSummaryDialog(self.root, app_stub)
        self.addCleanup(lambda: cleanup_tk(self, "dlg"))
        self.assertEqual(dlg.folder_var.get(), os.path.dirname(os.path.abspath(db.path)))

    def test_parcourir_reste_fonctionnel_independamment_du_prefremplissage(self):
        tournament_prefs.save_last_settings({"tournament_day_folder": "/dossier/general"})
        dlg = main.PeriodSummaryDialog(self.root, _StubApp())
        self.addCleanup(lambda: cleanup_tk(self, "dlg"))
        with patch.object(main.filedialog, "askdirectory", return_value="/choisi/manuellement"):
            dlg._browse_folder()
        self.assertEqual(dlg.folder_var.get(), "/choisi/manuellement")


if __name__ == "__main__":
    unittest.main()
