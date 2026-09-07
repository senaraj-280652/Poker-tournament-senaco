"""Test ciblé du correctif de robustesse #2 (audit du 2026-09-07) :
open_windows._save (open_windows.json) et set_phone_selected_pid
(phone_selected_pid.json) écrivaient directement sur le fichier final
(open(path, "w") + json.dump) — un crash/coupure PENDANT cette écriture
pouvait laisser un JSON tronqué/corrompu, que _load()/get_phone_
selected_pid() ne peuvent alors que traiter comme "vide", perdant du
même coup tout le registre partagé (y compris les entrées d'AUTRES
tournois déjà ouverts, qui ne se ré-enregistrent pas spontanément).

open_windows._atomic_write_json corrige ça : écrit dans un fichier
temporaire distinct (même dossier, pour que le remplacement reste sur
le même système de fichiers), le referme explicitement, PUIS
os.replace() — remplacement atomique aussi bien sous macOS/Linux que
Windows. Tous les tests ci-dessous travaillent dans un répertoire
temporaire dédié (jamais ~/.poker_tournament, le vrai registre de
l'utilisateur)."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


class AtomicWriteJsonTest(unittest.TestCase):
    def setUp(self):
        self._tmpdir_ctx = tempfile.TemporaryDirectory(prefix="poker_atomic_write_test_")
        self.tmpdir = self._tmpdir_ctx.name
        self.addCleanup(self._tmpdir_ctx.cleanup)
        self.target = os.path.join(self.tmpdir, "registry.json")

    def _tmp_leftovers(self):
        return [n for n in os.listdir(self.tmpdir) if n.startswith(".tmp-")]

    def test_ecriture_normale_produit_le_bon_contenu(self):
        open_windows._atomic_write_json(self.target, {"a": 1})
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 1})

    def test_aucun_fichier_temporaire_ne_traine_apres_une_ecriture_reussie(self):
        open_windows._atomic_write_json(self.target, {"a": 1})
        self.assertEqual(self._tmp_leftovers(), [])

    def test_remplace_correctement_un_fichier_deja_existant(self):
        open_windows._atomic_write_json(self.target, {"a": 1})
        open_windows._atomic_write_json(self.target, {"a": 2, "b": 3})
        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"a": 2, "b": 3})
        self.assertEqual(self._tmp_leftovers(), [])

    def test_fichier_temporaire_cree_dans_le_meme_dossier_que_la_cible(self):
        """Nécessaire pour que os.replace() reste sur le même système de
        fichiers (un rename entre deux volumes différents échouerait)."""
        captured = {}
        real_mkstemp = tempfile.mkstemp

        def spy_mkstemp(*args, **kwargs):
            captured["dir"] = kwargs.get("dir")
            return real_mkstemp(*args, **kwargs)

        with patch.object(open_windows.tempfile, "mkstemp", side_effect=spy_mkstemp):
            open_windows._atomic_write_json(self.target, {"a": 1})
        self.assertEqual(captured["dir"], self.tmpdir)

    def test_ecriture_interrompue_ne_detruit_pas_le_dernier_fichier_valide(self):
        """Le scénario central demandé : une écriture qui échoue APRÈS
        avoir créé le fichier temporaire (ex : coupure/erreur disque
        juste avant le remplacement) ne doit JAMAIS toucher au fichier
        cible déjà en place — le dernier JSON valide doit rester lisible
        tel quel."""
        open_windows._atomic_write_json(self.target, {"valide": "avant"})

        with patch.object(open_windows.os, "replace", side_effect=OSError("disque plein simulé")):
            with self.assertRaises(OSError):
                open_windows._atomic_write_json(self.target, {"jamais": "ecrit"})

        with open(self.target, "r", encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"valide": "avant"})

    def test_ecriture_interrompue_ne_laisse_pas_de_fichier_temporaire(self):
        with patch.object(open_windows.os, "replace", side_effect=OSError("simulé")):
            with self.assertRaises(OSError):
                open_windows._atomic_write_json(self.target, {"x": 1})
        self.assertEqual(self._tmp_leftovers(), [])

    def test_echec_pendant_json_dump_ne_laisse_pas_de_fichier_temporaire(self):
        """Cas où l'échec survient encore plus tôt (pendant l'écriture
        elle-même, avant même d'atteindre os.replace())."""

        class _Unserializable:
            pass

        with self.assertRaises(TypeError):
            open_windows._atomic_write_json(self.target, {"x": _Unserializable()})
        self.assertEqual(self._tmp_leftovers(), [])
        self.assertFalse(os.path.exists(self.target))


class SaveAndSetPhoneSelectedPidStillSwallowOSErrorTest(unittest.TestCase):
    """_save et set_phone_selected_pid avalaient déjà OSError avant ce
    correctif (comportement visible à préserver à l'identique) : vérifie
    qu'ils continuent de le faire, maintenant que l'écriture elle-même
    passe par _atomic_write_json."""

    def test_save_n_expose_pas_une_erreur_ecriture(self):
        with patch.object(open_windows, "_atomic_write_json", side_effect=OSError("échec simulé")):
            try:
                open_windows._save({"a": 1})
            except OSError:
                self.fail("_save() n'aurait pas dû laisser remonter l'OSError")

    def test_set_phone_selected_pid_n_expose_pas_une_erreur_ecriture(self):
        with patch.object(open_windows, "_atomic_write_json", side_effect=OSError("échec simulé")):
            try:
                open_windows.set_phone_selected_pid(42)
            except OSError:
                self.fail("set_phone_selected_pid() n'aurait pas dû laisser remonter l'OSError")


if __name__ == "__main__":
    unittest.main()
