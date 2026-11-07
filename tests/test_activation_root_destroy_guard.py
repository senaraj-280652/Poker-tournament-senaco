# -*- coding: utf-8 -*-
"""Correctif du 2026-11-06 — crash Windows "TclError: application has been
destroyed" découvert en testant TEST-25 (protocole V3) sur un HP Windows 10 :
un abandon de l'activation (V2 ActivationDialog ou V3 ActivationEnLigneDialog)
pendant App.__init__, AVANT self.deiconify(), appelait self.destroy() sur la
racine déjà prématurément détruite — self.transient(master) sur un master
encore self.withdraw() en est le déclencheur le plus probable (déjà documenté
comme rendant une fenêtre invisible sous macOS pour _choose_tournament_file/
"Bienvenue", voir son commentaire ; jamais vu sous Windows avant ce test car
aucun test n'exerçait ce chemin de démarrage sur un profil réellement vierge,
voir test_license_v3_integration_main.py).

Trois volets couverts ici, jamais via un vrai App() (trop lourd — voir le
docstring de test_license_v3_integration_main.py) :
- App._detruire_racine_en_securite (méthode non liée, doublure légère — même
  principe que tests/test_lobby_double_open_guard.py) : absorbe un destroy()
  déjà en échec (TclError), jamais un autre type d'exception.
- ActivationDialog (V2) / ActivationEnLigneDialog (V3) sur une VRAIE racine Tk
  (même principe que tests/test_licence_window.py) : transient() n'est plus
  appelé, grab_set() l'est toujours (modalité préservée), l'annulation rend
  bien la main avec activated=False et détruit le dialogue (jamais la racine).
- Sur une vraie racine Tk, confirmation que Tcl lève bien TclError au second
  destroy() (comportement réel, pas supposé), et que notre garde l'absorbe.

JAMAIS update()/update_idletasks() ici (voir l'avertissement dans
test_licence_window.py : SIGSEGV reproductible à 100% sur ce harnais
Python 3.14/Tcl-Tk/macOS pour un Toplevel qui ne pend pas du premier tk.Tk()
du processus)."""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402

try:
    import tkinter as tk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

from _tk_cleanup import cleanup_tk  # noqa: E402


class _FauxDestroyReussi:
    def __init__(self):
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1


class _FauxDestroyEchoueTcl:
    def __init__(self):
        self.destroy_calls = 0

    def destroy(self):
        self.destroy_calls += 1
        raise tk.TclError('can\'t invoke "destroy" command: application has been destroyed')


class _FauxDestroyEchoueAutre:
    def destroy(self):
        raise RuntimeError("panne totalement différente, ne doit jamais être avalée")


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible sur ce poste")
class DetruireRacineEnSecuriteTest(unittest.TestCase):
    """Méthode non liée appelée directement avec une doublure comme `self`
    (même principe que tests/test_lobby_double_open_guard.py) — pas besoin
    d'une vraie racine Tk pour ces 4 tests, seul le comportement générique de
    la méthode est en jeu. @unittest.skipUnless ici uniquement pour
    tk.TclError (classe d'exception), jamais un vrai widget."""

    def test_destroy_reussi_positionne_le_drapeau_et_detruit_une_fois(self):
        faux = _FauxDestroyReussi()
        main.App._detruire_racine_en_securite(faux)
        self.assertTrue(faux._fermee_pendant_init)
        self.assertEqual(faux.destroy_calls, 1)

    def test_destroy_deja_detruit_absorbe_tclerror_sans_lever(self):
        faux = _FauxDestroyEchoueTcl()
        try:
            main.App._detruire_racine_en_securite(faux)
        except tk.TclError:
            self.fail("TclError aurait dû être absorbée, pas propagée.")
        self.assertTrue(faux._fermee_pendant_init)
        self.assertEqual(faux.destroy_calls, 1)

    def test_drapeau_pose_meme_quand_destroy_echoue(self):
        """Le drapeau doit être positionné quoi qu'il arrive — c'est lui,
        pas le succès de destroy(), que __main__/les relances vérifient
        avant mainloop()."""
        faux = _FauxDestroyEchoueTcl()
        main.App._detruire_racine_en_securite(faux)
        self.assertTrue(faux._fermee_pendant_init)

    def test_une_autre_exception_que_tclerror_nest_jamais_avalee(self):
        """Seul TclError (déjà détruit) est un cas attendu/inoffensif — toute
        autre panne de destroy() doit continuer à remonter normalement."""
        faux = _FauxDestroyEchoueAutre()
        with self.assertRaises(RuntimeError):
            main.App._detruire_racine_en_securite(faux)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible sur ce poste")
class DoubleDestroyRacineReelleTest(unittest.TestCase):
    """Confirme, sur une VRAIE racine Tk (pas une supposition), que Tcl lève
    effectivement TclError au second destroy() — le scénario exact du crash
    Windows observé — et que notre garde l'absorbe dans ce cas réel."""

    def test_deuxieme_destroy_sur_une_vraie_racine_leve_tclerror(self):
        root = tk.Tk()
        root.withdraw()
        root.destroy()
        with self.assertRaises(tk.TclError):
            root.destroy()

    def test_detruire_racine_en_securite_absorbe_ce_cas_reel(self):
        root = tk.Tk()
        root.withdraw()
        root.destroy()  # racine déjà détruite, comme après l'incident transient()
        try:
            main.App._detruire_racine_en_securite(root)
        except tk.TclError:
            self.fail("TclError réelle d'un second destroy() aurait dû être absorbée.")
        self.assertTrue(root._fermee_pendant_init)


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible sur ce poste")
class DialoguesActivationTransientRetireTest(unittest.TestCase):
    """ActivationDialog (V2) et ActivationEnLigneDialog (V3), sur une VRAIE
    racine Tk encore withdraw() (même état qu'au moment où App.__init__ les
    construit) — même principe que FenetreLicenceReelleTest dans
    test_licence_window.py. wait_window patché en no-op pour ne jamais
    bloquer le test dans la boucle d'événements imbriquée (aucun utilisateur
    réel pour cliquer "Quitter")."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def test_activationdialog_v2_najamais_transient_mais_garde_grab_set(self):
        with patch.object(tk.Toplevel, "transient") as mock_transient, \
                patch.object(tk.Toplevel, "grab_set") as mock_grab_set, \
                patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            dialogue = main.ActivationDialog(self.root)
        mock_transient.assert_not_called()
        mock_grab_set.assert_called_once()
        self.assertFalse(dialogue.activated)
        dialogue.destroy()

    def test_activationenlignedialog_v3_najamais_transient_mais_garde_grab_set(self):
        with patch.object(tk.Toplevel, "transient") as mock_transient, \
                patch.object(tk.Toplevel, "grab_set") as mock_grab_set, \
                patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            dialogue = main.ActivationEnLigneDialog(self.root)
        mock_transient.assert_not_called()
        mock_grab_set.assert_called_once()
        self.assertFalse(dialogue.activated)
        dialogue.destroy()

    def test_annulation_v2_quitter_rend_la_main_avec_activated_false_et_detruit(self):
        """Simule un clic "Quitter" (_quit) une fois le dialogue construit :
        il doit se fermer lui-même (self.destroy() dans _quit, sur LE
        DIALOGUE, jamais la racine) et rendre la main normalement."""
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            dialogue = main.ActivationDialog(self.root)
        self.assertFalse(dialogue.activated)
        dialogue._quit()
        self.assertFalse(dialogue.activated)
        self.assertFalse(bool(dialogue.winfo_exists()), "Le dialogue doit avoir été détruit par _quit().")
        # La racine reste parfaitement intacte ici — c'est App.__init__ qui
        # décide ensuite, via _detruire_racine_en_securite, de la détruire.
        self.assertTrue(bool(self.root.winfo_exists()))

    def test_annulation_v3_quitter_rend_la_main_avec_activated_false_et_detruit(self):
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            dialogue = main.ActivationEnLigneDialog(self.root)
        self.assertFalse(dialogue.activated)
        dialogue._quit()
        self.assertFalse(dialogue.activated)
        self.assertFalse(bool(dialogue.winfo_exists()), "Le dialogue doit avoir été détruit par _quit().")
        self.assertTrue(bool(self.root.winfo_exists()))

    def test_sequence_complete_abandon_v3_puis_destruction_securisee_de_la_racine(self):
        """Reproduit la séquence EXACTE de App.__init__ (abandon V3) sans
        construire le vrai App() (trop lourd, voir test_license_v3_
        integration_main.py) : dialogue annulé -> racine détruite en
        sécurité -> drapeau posé, jamais d'exception, jamais de fenêtre
        orpheline."""
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            dialogue_activation = main.ActivationEnLigneDialog(self.root)
            if not dialogue_activation.activated:
                main.App._detruire_racine_en_securite(self.root)
        self.assertTrue(self.root._fermee_pendant_init)
        # Une fois réellement détruite, l'interpréteur Tcl lui-même est
        # parti : même winfo_exists() lève TclError ("cannot invoke winfo
        # command: application has been destroyed"), jamais un simple False
        # — comportement confirmé ici, voir aussi _tk_cleanup._still_exists,
        # qui absorbe déjà ce cas précis pour cette raison.
        with self.assertRaises(tk.TclError):
            self.root.winfo_exists()


if __name__ == "__main__":
    unittest.main()
