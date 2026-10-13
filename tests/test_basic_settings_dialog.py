# -*- coding: utf-8 -*-
"""Tests du bouton "⚙️ Paramètres de base" du Menu principal (demande
explicite du 2026-10-12, Étape 1) : vérification STRUCTURELLE du code
source, même convention déjà établie par NoOpOnCancellationTest/
RestoreMultiFolderTransparencyTest (tests/test_backup_restore.py) pour
backup_now()/restore_now() — open_basic_settings() est une closure
imbriquée dans _choose_tournament_file, construite au fil d'un
wait_window() réel sur la fenêtre "Bienvenue" ; l'isoler pour un test
fonctionnel direct nécessiterait de dupliquer toute cette fenêtre.

Ce que ce fichier vérifie EXPLICITEMENT, conformément à la demande :
- Le bouton est placé EXACTEMENT entre "♻️ Restaurer..." et "ℹ️ À
  propos".
- La fenêtre ouverte ne contient QUE "Nom du Club :" et "Dossier par
  défaut :" (+ le bouton "📂 Parcourir…") — aucun autre champ.
- Elle réutilise EXACTEMENT les mécanismes globaux déjà existants :
  export_prefs pour le nom du club, tournament_prefs.load_last_
  settings()/save_last_settings() pour le dossier par défaut — LE MÊME
  magasin global que celui déjà lu par backup_restore.restore_backup()
  pour sa propre validation (voir tests/test_backup_restore.py,
  TournamentFolderValidationTest).
- Elle n'accède JAMAIS à self.db (fonctionne sans ouvrir ni créer
  aucun tournoi)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _main_source():
    with open(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
        encoding="utf-8",
    ) as f:
        return f.read()


def _open_basic_settings_body(source):
    """Depuis le 2026-10-13, la logique vit dans App._open_basic_settings_
    dialog (extraite pour être réutilisable depuis le menu Réglages de
    "🖥️ Ouvrir Senaco" sans tournoi) — open_basic_settings() elle-même
    n'est plus qu'une délégation d'une ligne, voir
    tests/test_ouvrir_senaco_sans_tournoi.py::ReuseNoDuplicationStructuralTest."""
    start = source.index("def _open_basic_settings_dialog(self, parent):")
    end = source.index("def _choose_tournament_file(self):")
    return source[start:end]


class BasicSettingsButtonPlacementTest(unittest.TestCase):
    def test_bouton_place_entre_restaurer_et_a_propos(self):
        source = _main_source()
        idx_restore = source.index('text="♻️  Restaurer depuis une clé USB"')
        idx_settings_btn = source.index('text="⚙️  Paramètres de base"')
        idx_about = source.index('text="ℹ️  À propos"')
        self.assertLess(idx_restore, idx_settings_btn)
        self.assertLess(idx_settings_btn, idx_about)

    def test_bouton_appelle_open_basic_settings(self):
        source = _main_source()
        idx_settings_btn = source.index('text="⚙️  Paramètres de base"')
        idx_about = source.index('text="ℹ️  À propos"')
        between = source[idx_settings_btn:idx_about]
        self.assertIn("command=open_basic_settings", between)


class BasicSettingsDialogContentTest(unittest.TestCase):
    def test_contient_uniquement_nom_du_club_et_dossier_par_defaut(self):
        body = _open_basic_settings_body(_main_source())
        self.assertIn('"Nom du Club :"', body)
        self.assertIn('"Dossier par défaut :"', body)
        self.assertIn("📂 Parcourir…", body)
        # Aucun autre champ de réglage (ex. un réglage de tournoi) ne
        # doit apparaître dans cette fenêtre minimale.
        self.assertNotIn("buyin_amount", body)
        self.assertNotIn("starting_chips", body)

    def test_najamais_daccess_a_self_point_db(self):
        """Doit fonctionner SANS ouvrir ni créer aucun tournoi — aucun
        accès RÉEL (code, pas simplement mentionné dans la docstring) à
        self.db nulle part dans cette fenêtre."""
        body = _open_basic_settings_body(_main_source())
        self.assertNotIn("self.db.", body)

    def test_reutilise_export_prefs_pour_le_nom_du_club(self):
        body = _open_basic_settings_body(_main_source())
        self.assertIn('export_prefs.load_value("club_name"', body)
        self.assertIn('export_prefs.save_value("club_name"', body)

    def test_reutilise_tournament_prefs_pour_le_dossier_par_defaut(self):
        """LE MÊME magasin global que celui déjà lu par backup_restore.
        restore_backup() (tournament_prefs.load_last_settings()
        ["tournament_day_folder"]) — jamais un fichier de préférences
        séparé propre à cette fenêtre."""
        body = _open_basic_settings_body(_main_source())
        self.assertIn("tournament_prefs.load_last_settings()", body)
        self.assertIn('"tournament_day_folder"', body)
        self.assertIn("tournament_prefs.save_last_settings(", body)

    def test_parcourir_utilise_askdirectory(self):
        body = _open_basic_settings_body(_main_source())
        idx_browse = body.index("def browse_folder():")
        self.assertIn("filedialog.askdirectory(", body[idx_browse:idx_browse + 300])

    def test_bouton_annuler_present_et_ne_sauvegarde_rien(self):
        body = _open_basic_settings_body(_main_source())
        idx_annuler = body.index('text="Annuler"')
        line_end = body.index("\n", idx_annuler)
        self.assertIn("settings_win.destroy", body[idx_annuler:line_end + 1])


if __name__ == "__main__":
    unittest.main()
