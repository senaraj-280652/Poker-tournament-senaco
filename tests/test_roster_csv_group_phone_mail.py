# -*- coding: utf-8 -*-
"""Import/export CSV du Répertoire — 5 champs (demande du 2026-09-20,
complément à la Phase 1 du chantier "Sécurisation du Contrôle à
distance") : NOM;CLUB;GROUPE;TELEPHONE;MAIL. Même harnais que tests/
test_import_csv_encoding.py (_FakeDialog minimal, roster._roster_path
redirigé vers un fichier temporaire) — ce fichier-ci se concentre sur
les 3 nouveaux champs et la non-perte d'information, jamais sur
l'encodage (déjà couvert par l'autre fichier, non dupliqué ici).

Règles vérifiées, explicitement demandées :
- un ancien CSV NOM;CLUB (2 colonnes) reste importable tel quel, Groupe/
  Téléphone/Mail restent vides pour un nouveau membre ;
- un export puis réimport des 5 champs ne perd rien (aller-retour) ;
- Groupe reste strictement ADMIN, DIRTO ou vide (une valeur invalide
  dans le CSV est ignorée, jamais normalisée en "" à la place d'un
  groupe déjà connu) ;
- un téléphone à zéro initial n'est jamais reconverti en nombre ;
- un import (ancien OU nouveau format) ne doit jamais détruire des
  informations déjà connues d'une personne déjà présente dans le
  répertoire, quand la cellule correspondante est absente ou vide."""
import csv
import io
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import roster  # noqa: E402


def _write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


class _FakeDialog:
    """Même doublure minimale que tests/test_import_csv_encoding.py :
    seul self._refresh est réellement utilisé par _import_csv."""

    def __init__(self):
        self.refreshed = 0

    def _refresh(self):
        self.refreshed += 1


class RosterCsvTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_csv_group_phone_mail_test_")
        self.addCleanup(self._tmp.cleanup)
        self.roster_path = os.path.join(self._tmp.name, "roster.json")
        patcher = patch.object(roster, "_roster_path", return_value=self.roster_path)
        self.addCleanup(patcher.stop)
        patcher.start()
        self.dlg = _FakeDialog()
        self.csv_path = os.path.join(self._tmp.name, "joueurs.csv")

    def _run_import(self, mock_askstring_return=None):
        with patch.object(main.filedialog, "askopenfilename", return_value=self.csv_path), \
             patch.object(main.simpledialog, "askstring", return_value=mock_askstring_return), \
             patch.object(main.messagebox, "showerror") as mock_error, \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.RosterManagerDialog._import_csv(self.dlg)
        return mock_error, mock_info

    def _run_export(self, save_path):
        with patch.object(main.filedialog, "asksaveasfilename", return_value=save_path), \
             patch.object(main.messagebox, "showerror") as mock_error, \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.RosterManagerDialog._export_csv(self.dlg)
        return mock_error, mock_info


class ExportFormatTest(RosterCsvTestCase):
    def test_entete_5_colonnes(self):
        roster.add_to_roster("Alice", club="CPC")
        export_path = os.path.join(self._tmp.name, "out.csv")
        self._run_export(export_path)
        with open(export_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f, delimiter=";"))
        self.assertEqual(rows[0], ["NOM", "CLUB", "GROUPE", "TELEPHONE", "MAIL"])

    def test_ligne_exportee_contient_les_5_valeurs(self):
        roster.add_to_roster("Alice", club="CPC")
        roster.set_group("Alice", "ADMIN")
        roster.set_phone("Alice", "0102030405")
        roster.set_mail("Alice", "alice@cpc.fr")
        export_path = os.path.join(self._tmp.name, "out.csv")
        self._run_export(export_path)
        with open(export_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f, delimiter=";"))
        self.assertIn(["Alice", "CPC", "ADMIN", "0102030405", "alice@cpc.fr"], rows)

    def test_personne_non_classee_exporte_des_cellules_vides(self):
        roster.add_to_roster("Bob")
        export_path = os.path.join(self._tmp.name, "out.csv")
        self._run_export(export_path)
        with open(export_path, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f, delimiter=";"))
        self.assertIn(["Bob", "", "", "", ""], rows)

    def test_telephone_a_zero_initial_ecrit_tel_quel_dans_le_fichier(self):
        """Vérifie le contenu BRUT du fichier (pas juste relu par le
        csv.reader — le point sensible est que rien ne le convertisse
        en nombre à AUCUNE étape, écriture comprise)."""
        roster.add_to_roster("Alice")
        roster.set_phone("Alice", "0102030405")
        export_path = os.path.join(self._tmp.name, "out.csv")
        self._run_export(export_path)
        with open(export_path, "rb") as f:
            raw = f.read()
        self.assertIn(b"0102030405", raw)
        self.assertNotIn(b"102030405", raw.replace(b"0102030405", b""))  # pas de version tronquee ailleurs


class ImportAncienFormatTest(RosterCsvTestCase):
    """Ancien CSV NOM;CLUB (2 colonnes) : doit rester importable tel
    quel, Groupe/Téléphone/Mail restent vides pour un NOUVEAU membre."""

    def test_ancien_csv_2_colonnes_toujours_importable(self):
        _write_bytes(self.csv_path, "NOM;CLUB\r\nAlice;CPC\r\n".encode("utf-8"))
        mock_error, mock_info = self._run_import()
        mock_error.assert_not_called()
        mock_info.assert_called_once()
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["club"], "CPC")

    def test_nouveau_membre_via_ancien_csv_groupe_telephone_mail_vides(self):
        _write_bytes(self.csv_path, "NOM;CLUB\r\nAlice;CPC\r\n".encode("utf-8"))
        self._run_import()
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["group"], "")
        self.assertEqual(entries["Alice"]["phone"], "")
        self.assertEqual(entries["Alice"]["mail"], "")

    def test_csv_1_colonne_noms_seuls_toujours_importable(self):
        _write_bytes(self.csv_path, "Joueur\r\nAlice\r\nBob\r\n".encode("utf-8"))
        mock_error, mock_info = self._run_import(mock_askstring_return="CPC")
        mock_error.assert_not_called()
        names = sorted(roster.load_roster())
        self.assertEqual(names, ["Alice", "Bob"])


class ImportNePerdJamaisInfosExistantesTest(RosterCsvTestCase):
    """LE point de sécurité explicitement demandé : un import (ancien OU
    nouveau format) ne doit jamais écraser des informations déjà
    connues d'une personne déjà présente, quand la cellule correspondante
    est absente ou vide."""

    def test_ancien_csv_2_colonnes_ne_detruit_pas_groupe_deja_connu(self):
        roster.add_to_roster("Alice", club="Ancien Club")
        roster.set_group("Alice", "ADMIN")
        roster.set_phone("Alice", "0102030405")
        roster.set_mail("Alice", "alice@cpc.fr")

        _write_bytes(self.csv_path, "NOM;CLUB\r\nAlice;Nouveau Club\r\n".encode("utf-8"))
        self._run_import()

        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["club"], "Nouveau Club")  # mis à jour (comportement déjà existant)
        self.assertEqual(entries["Alice"]["group"], "ADMIN")  # préservé
        self.assertEqual(entries["Alice"]["phone"], "0102030405")  # préservé
        self.assertEqual(entries["Alice"]["mail"], "alice@cpc.fr")  # préservé

    def test_nouveau_csv_5_colonnes_cellule_vide_ne_detruit_pas_lexistant(self):
        roster.set_group("Alice", "ADMIN")
        roster.set_phone("Alice", "0102030405")
        roster.set_mail("Alice", "alice@cpc.fr")

        # Ligne avec Groupe/Téléphone/Mail explicitement VIDES (mais les
        # 5 colonnes sont bien présentes, contrairement à l'ancien
        # format) : ne doit PAS effacer ce qui est déjà connu.
        _write_bytes(self.csv_path, "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nAlice;CPC;;;\r\n".encode("utf-8"))
        self._run_import()

        self.assertEqual(roster.get_group("Alice"), "ADMIN")
        self.assertEqual(roster.get_phone("Alice"), "0102030405")
        self.assertEqual(roster.get_mail("Alice"), "alice@cpc.fr")

    def test_nouveau_csv_avec_valeurs_les_met_a_jour(self):
        roster.set_group("Alice", "ADMIN")
        _write_bytes(
            self.csv_path,
            "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nAlice;CPC;DIRTO;0601020304;alice2@cpc.fr\r\n".encode("utf-8"),
        )
        self._run_import()
        self.assertEqual(roster.get_group("Alice"), "DIRTO")  # remplacé, pas accumulé
        self.assertEqual(roster.get_phone("Alice"), "0601020304")
        self.assertEqual(roster.get_mail("Alice"), "alice2@cpc.fr")


class ImportGroupeValideStrictementTest(RosterCsvTestCase):
    """"Groupe doit rester strictement ADMIN, DIRTO ou vide" — vérifié à
    l'import : une valeur invalide est ignorée, jamais convertie en
    chaîne vide à la place d'un groupe déjà connu."""

    def test_groupe_admin_importe(self):
        _write_bytes(
            self.csv_path,
            "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nAlice;CPC;ADMIN;;\r\n".encode("utf-8"),
        )
        self._run_import()
        self.assertEqual(roster.get_group("Alice"), "ADMIN")

    def test_groupe_dirto_importe(self):
        _write_bytes(
            self.csv_path,
            "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nBob;CPC;DIRTO;;\r\n".encode("utf-8"),
        )
        self._run_import()
        self.assertEqual(roster.get_group("Bob"), "DIRTO")

    def test_groupe_insensible_a_la_casse(self):
        _write_bytes(
            self.csv_path,
            "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nAlice;CPC;admin;;\r\n".encode("utf-8"),
        )
        self._run_import()
        self.assertEqual(roster.get_group("Alice"), "ADMIN")

    def test_groupe_invalide_ignore_pour_nouveau_membre(self):
        _write_bytes(
            self.csv_path,
            "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nAlice;CPC;SUPERADMIN;;\r\n".encode("utf-8"),
        )
        mock_error, mock_info = self._run_import()
        mock_error.assert_not_called()  # jamais une erreur bloquante, juste ignoré
        self.assertEqual(roster.get_group("Alice"), "")

    def test_groupe_invalide_ne_detruit_pas_groupe_existant(self):
        """LE cas le plus important : une valeur invalide dans la
        colonne GROUPE (faute de frappe, colonne décalée) ne doit
        JAMAIS effacer un ADMIN/DIRTO déjà connu."""
        roster.set_group("Alice", "ADMIN")
        _write_bytes(
            self.csv_path,
            "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nAlice;CPC;PasUnGroupe;;\r\n".encode("utf-8"),
        )
        self._run_import()
        self.assertEqual(roster.get_group("Alice"), "ADMIN")  # toujours ADMIN, jamais ""


class ImportTelephoneJamaisConvertiTest(RosterCsvTestCase):
    def test_telephone_avec_zero_initial_preserve_a_limport(self):
        _write_bytes(
            self.csv_path,
            "NOM;CLUB;GROUPE;TELEPHONE;MAIL\r\nAlice;CPC;;0601020304;\r\n".encode("utf-8"),
        )
        self._run_import()
        self.assertEqual(roster.get_phone("Alice"), "0601020304")
        self.assertNotEqual(roster.get_phone("Alice"), "601020304")


class RoundTripExportPuisReimportTest(RosterCsvTestCase):
    """"Les nouveaux CSV doivent permettre d'exporter puis réimporter les
    5 champs sans perte" — vérifié sur un répertoire VIDÉ entre l'export
    et le réimport (simule un nouveau poste, cas d'usage réel d'un
    export/import)."""

    def test_aller_retour_sans_perte_pour_plusieurs_personnes(self):
        roster.set_group("Alice", "ADMIN")
        roster.set_phone("Alice", "0102030405")
        roster.set_mail("Alice", "alice@cpc.fr")
        roster.add_to_roster("Alice", club="CPC")
        roster.set_group("Bob", "DIRTO")
        roster.set_phone("Bob", "0607080910")
        roster.add_to_roster("Bob", club="Autre Club")
        roster.add_to_roster("Chris")  # non classé, aucun contact

        export_path = os.path.join(self._tmp.name, "out.csv")
        self._run_export(export_path)

        # Vide complètement le répertoire (simule un autre poste/une
        # réinstallation) avant de réimporter, pour un aller-retour
        # sans contamination par l'état déjà en mémoire.
        roster.save_roster_entries([])
        self.assertEqual(roster.load_roster_entries(), [])

        with patch.object(main.filedialog, "askopenfilename", return_value=export_path), \
             patch.object(main.messagebox, "showerror") as mock_error, \
             patch.object(main.messagebox, "showinfo"):
            main.RosterManagerDialog._import_csv(self.dlg)
        mock_error.assert_not_called()

        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"], {
            "name": "Alice", "club": "CPC", "group": "ADMIN",
            "phone": "0102030405", "mail": "alice@cpc.fr",
        })
        self.assertEqual(entries["Bob"], {
            "name": "Bob", "club": "Autre Club", "group": "DIRTO",
            "phone": "0607080910", "mail": "",
        })
        self.assertEqual(entries["Chris"], {
            "name": "Chris", "club": "", "group": "", "phone": "", "mail": "",
        })


if __name__ == "__main__":
    unittest.main()
