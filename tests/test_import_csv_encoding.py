# -*- coding: utf-8 -*-
"""Tests du correctif d'encodage de RosterManagerDialog._import_csv
(Répertoire > Importer CSV, main.py) — demande du 2026-09-12.

Bug d'origine : un CSV encodé en Windows-1252/ANSI (typiquement un export
Excel sous Windows, ex. "Jérome M" avec un é encodé en 0xE9) faisait
échouer silencieusement l'import. L'ouverture forçait "utf-8-sig" sans
repli ; UnicodeDecodeError (sous-classe de ValueError, PAS de OSError)
n'était donc pas rattrapée par le `except OSError` de _import_csv, et
remontait jusqu'à Tkinter, qui l'avale sans aucun message (voir
App.report_callback_exception) — le bouton semblait "ne rien faire".

Correctif : le fichier est désormais lu en bytes, puis décodé via
_decode_csv_bytes (module-level, IMPORT_CSV_ENCODINGS = ("utf-8-sig",
"cp1252")) — UTF-8 (avec ou sans BOM) essayé en premier, repli sur
Windows-1252/ANSI sinon. Un échec de décodage résiduel (encodage non
couvert par ces deux essais) affiche désormais une boîte de dialogue
claire au lieu d'un échec silencieux.

Ce fichier vérifie :
1. CSV UTF-8 sans accent (non-régression) ;
2. CSV UTF-8 AVEC BOM (non-régression, déjà géré par "utf-8-sig") ;
3. CSV Windows-1252/ANSI avec accents ("Jérome M", "François D") — cas du
   bug, désormais importé correctement ;
4. import dans un répertoire de joueurs VIDE (aucun conflit possible) ;
5. les noms importés conservent EXACTEMENT leurs accents (comparaison de
   chaîne stricte sur le contenu réellement enregistré, pas seulement "un
   import a eu lieu") ;
6. un fichier dans un encodage non couvert par IMPORT_CSV_ENCODINGS (testé
   en réduisant temporairement cette liste à ("utf-8-sig",) — impossible à
   obtenir avec de vrais octets cp1252, cet encodage acceptant SUPER
   n'importe quelle suite d'octets) déclenche une messagebox.showerror
   claire, jamais un échec silencieux ;
7. un fichier réellement illisible (permission refusée) déclenche aussi
   une messagebox.showerror claire (chemin OSError, déjà géré avant ce
   correctif, non-régression).

N'utilise PAS le vrai ~/.poker_tournament/roster.json : roster._roster_path
est redirigé vers un fichier temporaire pour toute la durée de chaque
test (voir setUp), donc aucune interférence entre tests ni avec les
données réelles de l'utilisateur."""
import csv
import io
import os
import stat
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import main  # noqa: E402
import roster  # noqa: E402


def _write_bytes(path, data):
    with open(path, "wb") as f:
        f.write(data)


class _FakeDialog:
    """Bouchon minimal pour RosterManagerDialog : ne fournit à
    App._import_csv que ce dont elle a réellement besoin (self._refresh),
    sans construire la moindre fenêtre Tk — même principe que les
    doublures `types.SimpleNamespace`/`types.MethodType` déjà utilisées
    ailleurs dans la suite de tests (voir tests/test_primes_enabled_
    toggle.py) pour tester une méthode de main.py isolément."""

    def __init__(self):
        self.refreshed = 0

    def _refresh(self):
        self.refreshed += 1


class ImportCsvEncodingTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="import_csv_encoding_test_")
        self.addCleanup(self._tmp.cleanup)
        self.roster_path = os.path.join(self._tmp.name, "roster.json")

        patcher = patch.object(roster, "_roster_path", return_value=self.roster_path)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Vérifie que le répertoire de joueurs est bien vide au départ de
        # chaque test (voir point 4 de la docstring de ce fichier) : le
        # fichier n'existe même pas encore tant qu'aucune écriture n'a eu
        # lieu (roster.save_roster_entries), exactement comme un tout
        # nouveau poste de travail.
        self.assertFalse(os.path.exists(self.roster_path))
        self.assertEqual(roster.load_roster_entries(), [])

        self.dlg = _FakeDialog()
        self.csv_path = os.path.join(self._tmp.name, "joueurs.csv")

    def _run_import(self, mock_askstring_return=None):
        """Exécute RosterManagerDialog._import_csv sur self.csv_path, en
        bouchonnant la boîte de sélection de fichier (renvoie directement
        self.csv_path) et la boîte de dialogue Club le cas échéant."""
        with patch.object(main.filedialog, "askopenfilename", return_value=self.csv_path), \
             patch.object(main.simpledialog, "askstring", return_value=mock_askstring_return), \
             patch.object(main.messagebox, "showerror") as mock_error, \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.RosterManagerDialog._import_csv(self.dlg)
        return mock_error, mock_info

    # -- 1. UTF-8 sans accent (non-régression) -----------------------------

    def test_utf8_plain_no_accent(self):
        _write_bytes(
            self.csv_path,
            "Joueur\r\nAlice Martin\r\nBob Dupont\r\n".encode("utf-8"),
        )
        mock_error, mock_info = self._run_import(mock_askstring_return="CPC")
        mock_error.assert_not_called()
        mock_info.assert_called_once()
        names = sorted(e["name"] for e in roster.load_roster_entries())
        self.assertEqual(names, ["Alice Martin", "Bob Dupont"])

    # -- 2. UTF-8 avec BOM (non-régression) --------------------------------

    def test_utf8_with_bom(self):
        _write_bytes(
            self.csv_path,
            "Joueur\r\nAlice Martin\r\nBob Dupont\r\n".encode("utf-8-sig"),
        )
        mock_error, mock_info = self._run_import(mock_askstring_return="CPC")
        mock_error.assert_not_called()
        mock_info.assert_called_once()
        names = sorted(e["name"] for e in roster.load_roster_entries())
        self.assertEqual(names, ["Alice Martin", "Bob Dupont"])
        # Le BOM ne doit laisser AUCUNE trace ("﻿") dans le nom importé.
        self.assertFalse(any(n.startswith("﻿") for n in names))

    # -- 3. Windows-1252/ANSI avec accents (cas du bug) --------------------

    def test_cp1252_with_accents(self):
        names_in = ["Jérome M", "François D", "Bénédicte L", "Andrée K"]
        text = "Joueur\r\n" + "\r\n".join(names_in) + "\r\n"
        _write_bytes(self.csv_path, text.encode("cp1252"))

        # Vérifie que le fichier de test reproduit bien la situation décrite
        # (0xE9 pour le é de "Jérome") avant de vérifier le comportement.
        with open(self.csv_path, "rb") as f:
            raw = f.read()
        self.assertIn(b"J\xe9rome M", raw)
        with self.assertRaises(UnicodeDecodeError):
            raw.decode("utf-8")

        mock_error, mock_info = self._run_import(mock_askstring_return="CPC")
        mock_error.assert_not_called()
        mock_info.assert_called_once()

        # -- 5. Les accents sont conservés EXACTEMENT (comparaison stricte).
        names_out = sorted(e["name"] for e in roster.load_roster_entries())
        self.assertEqual(names_out, sorted(names_in))
        self.assertIn("Jérome M", names_out)
        self.assertIn("François D", names_out)

    # -- 4. Import dans un répertoire de joueurs VIDE ----------------------

    def test_import_into_empty_roster(self):
        self.assertEqual(roster.load_roster_entries(), [])  # déjà vérifié en setUp, explicite ici
        _write_bytes(self.csv_path, "Joueur\r\nSeul Joueur\r\n".encode("utf-8"))
        mock_error, mock_info = self._run_import(mock_askstring_return="CPC")
        mock_error.assert_not_called()
        entries = roster.load_roster_entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "Seul Joueur")

    # -- 6. Encodage réellement non supporté : message clair, pas de crash --

    def test_unsupported_encoding_shows_error_dialog(self):
        # cp1252 accepte n'importe quelle suite d'octets : pour exercer le
        # message "encodage non reconnu", on réduit temporairement la liste
        # des encodages essayés à ("utf-8-sig",) seul, puis on fournit un
        # fichier cp1252 avec un accent (qui échoue alors bien en UTF-8).
        _write_bytes(self.csv_path, "Joueur\r\nJérome M\r\n".encode("cp1252"))
        with patch.object(main, "IMPORT_CSV_ENCODINGS", ("utf-8-sig",)):
            mock_error, mock_info = self._run_import(mock_askstring_return="CPC")
        mock_error.assert_called_once()
        args, kwargs = mock_error.call_args
        self.assertIn("Encodage", args[1] if len(args) > 1 else kwargs.get("message", ""))
        mock_info.assert_not_called()
        # Rien n'a été importé : import interrompu proprement, pas de
        # données partielles/corrompues dans le répertoire.
        self.assertEqual(roster.load_roster_entries(), [])

    # -- 7. Fichier illisible (permission refusée) : message clair ---------

    @unittest.skipIf(os.name == "nt" or os.geteuid() == 0,
                      "chmod 000 sans effet sous Windows ou en root")
    def test_unreadable_file_shows_error_dialog(self):
        _write_bytes(self.csv_path, "Joueur\r\nAlice\r\n".encode("utf-8"))
        os.chmod(self.csv_path, 0)
        self.addCleanup(os.chmod, self.csv_path, stat.S_IRUSR | stat.S_IWUSR)
        mock_error, mock_info = self._run_import(mock_askstring_return="CPC")
        mock_error.assert_called_once()
        mock_info.assert_not_called()
        self.assertEqual(roster.load_roster_entries(), [])

    # -- Non-régression : délimiteur ";" à deux colonnes (Nom;Club) --------

    def test_two_columns_semicolon_delimiter_unaffected(self):
        text = "Nom;Club\r\nJérome M;CPC\r\nAlice;Autre Club\r\n"
        _write_bytes(self.csv_path, text.encode("cp1252"))
        mock_error, mock_info = self._run_import()
        mock_error.assert_not_called()
        entries = {e["name"]: e["club"] for e in roster.load_roster_entries()}
        self.assertEqual(entries.get("Jérome M"), "CPC")
        self.assertEqual(entries.get("Alice"), "Autre Club")


if __name__ == "__main__":
    unittest.main()
