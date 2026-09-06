"""Test ciblé du correctif "Menu principal" reproductible après avoir
fermé des tournois depuis le téléphone (voir open_windows.py :
bring_pid_to_front) : sans le `try` ajouté autour de CHAQUE comparaison
`unix id of p` dans la boucle `repeat`, un unique process "fantôme"
(laissé un court instant par System Events juste après la fermeture
d'un tournoi) faisait échouer TOUT le `repeat` avec une erreur
AppleScript non rattrapée — avant même d'avoir atteint le vrai process
cible plus loin dans la liste.

Ne peut pas être vérifié en conditions réelles ici (accès Accessibilité
macOS non disponible dans cet environnement de test, et un vrai process
"fantôme" ne se reproduit pas à la demande) : ce test vérifie donc (1)
la STRUCTURE du script AppleScript généré — le `try` protège bien
chaque comparaison, pas seulement le bloc AXRaise déjà protégé avant ce
correctif — et (2), sur macOS, que ce script reste syntaxiquement
valide pour le vrai `osascript` (une erreur d'AUTORISATION est
acceptée, un environnement de test n'ayant pas forcément la permission
Accessibilité ; seule une erreur de SYNTAXE fait échouer ce test)."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import open_windows  # noqa: E402


def _generated_script(pid=999999):
    """Capture les arguments passés à subprocess.Popen par
    bring_pid_to_front, sans jamais vraiment lancer osascript."""
    captured = {}

    def fake_popen(args, **kwargs):
        captured["args"] = args

        class _FakeProc:
            pass

        return _FakeProc()

    with patch.object(open_windows.subprocess, "Popen", side_effect=fake_popen), \
         patch.object(open_windows.sys, "platform", "darwin"):
        open_windows.bring_pid_to_front(pid)
    return captured["args"]


class BringPidToFrontScriptStructureTest(unittest.TestCase):
    def test_chaque_comparaison_unix_id_est_protegee_par_son_propre_try(self):
        args = _generated_script()
        self.assertEqual(args[0], "osascript")
        script = args[2]

        idx_repeat = script.index("repeat with p in")
        idx_if_unix = script.index("if unix id of p is")
        idx_end_repeat = script.index("end repeat")
        self.assertLess(idx_repeat, idx_if_unix)
        self.assertLess(idx_if_unix, idx_end_repeat)

        # Un "try" doit encadrer précisément CE "if" (entre repeat et lui),
        # pas seulement exister ailleurs dans le script (le bloc AXRaise
        # plus bas était déjà protégé avant ce correctif — on vérifie ici
        # spécifiquement la protection de la comparaison unix id).
        idx_try_before = script.rindex("try", idx_repeat, idx_if_unix)
        self.assertGreater(idx_try_before, idx_repeat)
        idx_end_try_after = script.index("end try", idx_if_unix)
        self.assertLess(idx_end_try_after, idx_end_repeat)

    @unittest.skipUnless(sys.platform == "darwin", "AppleScript : macOS uniquement")
    def test_le_script_reste_syntaxiquement_valide_pour_le_vrai_osascript(self):
        args = _generated_script()
        result = subprocess.run(args, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return
        stderr = (result.stderr or "").lower()
        # Une erreur d'AUTORISATION (permission Accessibilité non accordée
        # à cet environnement de test) est acceptable ici — seule une
        # erreur de SYNTAXE indiquerait que ce correctif a cassé le script.
        acceptable = any(
            marker in stderr
            for marker in ("authoriz", "accès d'aide", "-1743", "-25211")
        )
        self.assertTrue(
            acceptable,
            f"Le script semble syntaxiquement invalide (erreur inattendue) : {result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
