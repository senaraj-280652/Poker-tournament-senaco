# -*- coding: utf-8 -*-
"""Non-régression du correctif du 2026-10-26 : sur une toute première
installation (aucun ~/.poker_tournament existant), "Nouveau tournoi" et
"Sit & Go rapide" créaient un tournoi sans jamais exiger de club ni de
"Dossier par défaut" valide — le fichier atterrissait silencieusement
dans le dossier personnel (HOME), proposé par défaut mais jamais imposé.

Ce fichier teste :
1. diagnostiquer_prerequis_nouveau_tournoi() — logique pure (aucun
   Tkinter, aucun export_prefs/tournament_prefs réel), les 6 cas
   demandés : réglages vides, club composé d'espaces, dossier
   inexistant, chemin désignant un fichier, dossier non inscriptible,
   cas valide.
2. App._verifier_prerequis_nouveau_tournoi — la méthode d'interface qui
   relie cette logique au message explicatif et à l'ouverture des
   Paramètres de base, via le même procédé de greffe de méthode
   (types.MethodType) qu'utilisent déjà tests/test_table_integrity_
   warning.py et consorts, pour éviter d'ouvrir une vraie fenêtre Tk.
3. Une garde structurelle confirmant que new_tournament()/new_sng()
   appellent bien ce contrôle AVANT tout filedialog.asksaveasfilename()
   — vérifié sur le code source lui-même (inspect.getsource), puisque
   ces deux fonctions sont des fermetures imbriquées non testables
   isolément sans ouvrir la fenêtre Bienvenue complète."""
import inspect
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402


class DiagnostiquerPrerequisTest(unittest.TestCase):
    """Logique pure — isdir_fn/access_fn factices, aucun vrai fichier."""

    def test_reglages_entierement_vides(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi("", "")
        self.assertIn("le nom du club", manques)
        self.assertIn("le « Dossier par défaut »", manques)
        self.assertEqual(len(manques), 2)

    def test_reglages_none(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi(None, None)
        self.assertEqual(len(manques), 2)

    def test_club_compose_uniquement_d_espaces_compte_comme_vide(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi(
            "   ", "/un/dossier", isdir_fn=lambda p: True, access_fn=lambda p, m: True
        )
        self.assertEqual(manques, ["le nom du club"])

    def test_dossier_compose_uniquement_d_espaces_compte_comme_vide(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi("Mon Club", "   ")
        self.assertEqual(manques, ["le « Dossier par défaut »"])

    def test_dossier_inexistant(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi(
            "Mon Club", "/chemin/inexistant", isdir_fn=lambda p: False
        )
        self.assertEqual(len(manques), 1)
        self.assertIn("n'existe pas", manques[0])

    def test_chemin_designant_un_fichier_pas_un_dossier(self):
        # os.path.isdir() renvoie False pour un fichier existant — même
        # traitement que "inexistant", intentionnellement (un fichier
        # n'est jamais un dossier valide).
        manques = main.diagnostiquer_prerequis_nouveau_tournoi(
            "Mon Club", "/chemin/vers/un_fichier.txt", isdir_fn=lambda p: False
        )
        self.assertEqual(len(manques), 1)
        self.assertIn("n'existe pas ou n'est pas un dossier", manques[0])

    def test_dossier_existant_mais_non_inscriptible(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi(
            "Mon Club", "/dossier/protege",
            isdir_fn=lambda p: True, access_fn=lambda p, mode: False,
        )
        self.assertEqual(len(manques), 1)
        self.assertIn("accessible en écriture", manques[0])

    def test_cas_valide_aucun_manque(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi(
            "Club Démo", "/dossier/valide",
            isdir_fn=lambda p: True, access_fn=lambda p, mode: True,
        )
        self.assertEqual(manques, [])

    def test_club_valide_mais_dossier_manquant(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi("Mon Club", "")
        self.assertEqual(manques, ["le « Dossier par défaut »"])

    def test_ordre_club_puis_dossier(self):
        manques = main.diagnostiquer_prerequis_nouveau_tournoi("", "")
        self.assertEqual(manques[0], "le nom du club")


class VerifierPrerequisNouveauTournoiTest(unittest.TestCase):
    """App._verifier_prerequis_nouveau_tournoi, via greffe de méthode —
    aucune vraie fenêtre Tk, comme tests/test_table_integrity_warning.py."""

    def setUp(self):
        self.win = types.SimpleNamespace()
        self.win._verifier_prerequis_nouveau_tournoi = types.MethodType(
            main.App._verifier_prerequis_nouveau_tournoi, self.win
        )
        self.win._open_basic_settings_dialog = MagicMock()

    def test_tout_configure_et_valide_retourne_true_sans_dialogue(self):
        # Vrai dossier temporaire (existant, inscriptible) plutôt qu'un
        # patch de os.path.isdir/os.access : diagnostiquer_prerequis_
        # nouveau_tournoi() reçoit ses vérificateurs par défaut
        # (isdir_fn=os.path.isdir), liés à la DÉFINITION de la fonction
        # — un patch de os.path.isdir après coup ne les atteindrait pas,
        # ceci teste donc le vrai chemin de code, sans fragilité.
        with tempfile.TemporaryDirectory() as dossier_reel:
            with patch.object(main.export_prefs, "load_value", return_value="Club Démo"), \
                 patch.object(main.tournament_prefs, "load_last_settings",
                               return_value={"tournament_day_folder": dossier_reel}), \
                 patch.object(main, "messagebox") as mock_messagebox:
                ok = self.win._verifier_prerequis_nouveau_tournoi(self.win)
        self.assertTrue(ok)
        mock_messagebox.askyesno.assert_not_called()
        self.win._open_basic_settings_dialog.assert_not_called()

    def test_rien_configure_retourne_false_et_affiche_le_message(self):
        with patch.object(main.export_prefs, "load_value", return_value=""), \
             patch.object(main.tournament_prefs, "load_last_settings", return_value={}), \
             patch.object(main, "messagebox") as mock_messagebox:
            mock_messagebox.askyesno.return_value = False
            ok = self.win._verifier_prerequis_nouveau_tournoi(self.win)
        self.assertFalse(ok)
        mock_messagebox.askyesno.assert_called_once()
        titre, message = mock_messagebox.askyesno.call_args[0]
        self.assertIn("club", message)
        self.assertIn("Dossier par défaut", message)
        # Refusé par l'utilisateur (askyesno -> False) : Paramètres de
        # base ne doit pas s'ouvrir tout seul.
        self.win._open_basic_settings_dialog.assert_not_called()

    def test_accepter_le_message_ouvre_les_parametres_de_base(self):
        with patch.object(main.export_prefs, "load_value", return_value=""), \
             patch.object(main.tournament_prefs, "load_last_settings", return_value={}), \
             patch.object(main, "messagebox") as mock_messagebox:
            mock_messagebox.askyesno.return_value = True
            ok = self.win._verifier_prerequis_nouveau_tournoi(self.win)
        self.assertFalse(ok)
        self.win._open_basic_settings_dialog.assert_called_once_with(self.win)

    def test_aucun_repli_automatique_sur_home_ou_bureau(self):
        # Même si HOME/Bureau existent réellement sur la machine qui
        # exécute ce test, la fonction ne doit JAMAIS s'y rabattre :
        # seule la valeur CONFIGURÉE (ici vide) compte.
        with patch.object(main.export_prefs, "load_value", return_value="Mon Club"), \
             patch.object(main.tournament_prefs, "load_last_settings", return_value={}), \
             patch.object(main, "messagebox") as mock_messagebox:
            mock_messagebox.askyesno.return_value = False
            ok = self.win._verifier_prerequis_nouveau_tournoi(self.win)
        self.assertFalse(ok)
        message = mock_messagebox.askyesno.call_args[0][1]
        self.assertNotIn(os.path.expanduser("~"), message)


class OrdreAppelDansNewTournamentTest(unittest.TestCase):
    """Garde structurelle : confirme que new_tournament()/new_sng()
    appellent _verifier_prerequis_nouveau_tournoi AVANT tout
    filedialog.asksaveasfilename() — ces fonctions sont des fermetures
    imbriquées dans _choose_tournament_file, non appelables isolément
    sans ouvrir la fenêtre Bienvenue complète ; on vérifie donc l'ORDRE
    directement dans le code source, pour attraper toute régression qui
    déplacerait ce contrôle après coup."""

    def test_new_tournament_verifie_avant_le_dialogue_d_enregistrement(self):
        source = inspect.getsource(main.App._choose_tournament_file)
        idx_new_tournament = source.index("def new_tournament():")
        idx_new_sng = source.index("def new_sng():")
        bloc_new_tournament = source[idx_new_tournament:idx_new_sng]

        idx_verif = bloc_new_tournament.index("_verifier_prerequis_nouveau_tournoi")
        idx_dialogue = bloc_new_tournament.index("asksaveasfilename")
        self.assertLess(
            idx_verif, idx_dialogue,
            "la vérification doit se trouver AVANT le dialogue d'enregistrement"
        )

    def test_new_sng_verifie_avant_le_dialogue_d_enregistrement(self):
        source = inspect.getsource(main.App._choose_tournament_file)
        idx_new_sng = source.index("def new_sng():")
        idx_open_tournament = source.index("def open_tournament():")
        bloc_new_sng = source[idx_new_sng:idx_open_tournament]

        idx_verif = bloc_new_sng.index("_verifier_prerequis_nouveau_tournoi")
        idx_dialogue = bloc_new_sng.index("asksaveasfilename")
        self.assertLess(idx_verif, idx_dialogue)


if __name__ == "__main__":
    unittest.main()
