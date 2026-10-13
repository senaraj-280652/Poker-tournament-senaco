# -*- coding: utf-8 -*-
"""Tests du bouton "🗑️ Purger les données" du Menu principal (demande du
2026-10-12) — vérification STRUCTURELLE du code source, même convention
déjà établie par tests/test_basic_settings_dialog.py pour "⚙️ Paramètres
de base" : purge_now() est une closure imbriquée dans _choose_tournament_
file, construite au fil d'un wait_window() réel sur la fenêtre
"Bienvenue" ; l'isoler pour un test fonctionnel direct nécessiterait de
dupliquer toute cette fenêtre.

Ce que ce fichier vérifie EXPLICITEMENT, conformément à la demande :
- Le bouton est placé EXACTEMENT entre "♻️ Restaurer..." et "⚙️
  Paramètres de base" (immédiatement sous Restaurer, avant Paramètres
  de base).
- Il ouvre DIRECTEMENT DataPurgeDialog — jamais un second circuit de
  purge, jamais un passage obligé par un tournoi ouvert.
- Il refuse (même garde-fou cross-process que restore_now : open_
  windows.list_open_paths(), "ici ou dans une autre fenêtre") tant qu'un
  tournoi est ouvert, N'IMPORTE LEQUEL, où que ce soit.
- DataPurgeDialog est bien construite avec parent_window=win (jamais
  `self`, l'App racine encore withdraw() à ce stade), pour qu'elle
  s'affiche réellement — voir tests/test_data_purge_dialog.py::
  DataPurgeDialogParentWindowTest pour la couverture comportementale de
  ce mécanisme lui-même, non répétée ici.

Toutes les sécurités de DataPurgeDialog elle-même (sélection des
périodes/types/dossiers, vérification de sauvegarde, confirmations
avant suppression définitive) sont déjà couvertes par tests/test_data_
purge_dialog.py et tests/test_data_purge.py — totalement INCHANGÉES par
ce nouveau point d'entrée, non répétées ici."""
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


def _purge_now_body(source):
    """Depuis le 2026-10-13, la logique vit dans App._open_data_purge_
    dialog (extraite pour être réutilisable depuis le menu Réglages de
    "🖥️ Ouvrir Senaco" sans tournoi) — purge_now() elle-même n'est plus
    qu'une délégation d'une ligne, voir tests/test_ouvrir_senaco_sans_
    tournoi.py::ReuseNoDuplicationStructuralTest. `parent` y remplace
    `win` (même paramètre, win est simplement ce qui lui est passé
    depuis purge_now())."""
    start = source.index("def _open_data_purge_dialog(self, parent):")
    end = source.index("def _open_basic_settings_dialog(self, parent):")
    return source[start:end]


class PurgeButtonPlacementTest(unittest.TestCase):
    def test_bouton_place_entre_restaurer_et_parametres_de_base(self):
        source = _main_source()
        idx_restore = source.index('text="♻️  Restaurer depuis une clé USB"')
        idx_purge_btn = source.index('text="🗑️  Purger les données"')
        idx_settings = source.index('text="⚙️  Paramètres de base"')
        self.assertLess(idx_restore, idx_purge_btn)
        self.assertLess(idx_purge_btn, idx_settings)

    def test_bouton_immediatement_sous_restaurer_aucun_autre_bouton_entre_deux(self):
        """Demande explicite : "immédiatement sous « Restaurer depuis une
        clé USB »" — aucun AUTRE bouton (ttk.Button(...text=...)) ne doit
        apparaître entre restore_btn.pack(...) et la création du bouton
        Purger lui-même."""
        source = _main_source()
        idx_restore_pack = source.index("restore_btn.pack(pady=6)")
        idx_purge_btn = source.index("purge_btn = ttk.Button(")
        between = source[idx_restore_pack:idx_purge_btn]
        self.assertNotIn("ttk.Button(", between)

    def test_bouton_appelle_purge_now(self):
        source = _main_source()
        idx_purge_btn = source.index('text="🗑️  Purger les données"')
        idx_settings = source.index('text="⚙️  Paramètres de base"')
        between = source[idx_purge_btn:idx_settings]
        self.assertIn("command=purge_now", between)


class PurgeNowBehaviorTest(unittest.TestCase):
    def test_refuse_si_un_tournoi_est_ouvert(self):
        body = _purge_now_body(_main_source())
        self.assertIn("open_windows.list_open_paths()", body)
        # Le refus doit survenir AVANT toute construction de DataPurgeDialog.
        idx_guard = body.index("open_windows.list_open_paths()")
        idx_dialog = body.index("DataPurgeDialog(")
        self.assertLess(idx_guard, idx_dialog)
        guard_block = body[idx_guard:idx_dialog]
        self.assertIn("showerror", guard_block)
        self.assertIn("return", guard_block)

    def test_ouvre_directement_datapurgedialog_avec_parent_window_win(self):
        """parent_window=parent (PAS self) : voir la docstring de
        App._open_data_purge_dialog et DataPurgeDialog.__init__ — sans
        quoi la fenêtre resterait invisible (App racine encore self.
        withdraw() à ce stade, quand appelée depuis "Bienvenue")."""
        body = _purge_now_body(_main_source())
        self.assertIn("DataPurgeDialog(self, parent_window=parent)", body)

    def test_naccede_jamais_a_self_point_db(self):
        """Doit fonctionner SANS ouvrir ni créer aucun tournoi."""
        body = _purge_now_body(_main_source())
        self.assertNotIn("self.db.", body)

    def test_ne_touche_jamais_a_la_fenetre_bienvenue(self):
        """"Après fermeture de la fenêtre de purge, revenir au Menu
        principal" — purge_now() ne doit JAMAIS fermer/masquer "win"
        (l'écran "Bienvenue") lui-même : le retour au menu est ENTIÈREMENT
        géré par le grab()/transient() de DataPurgeDialog (voir sa
        docstring), rien à faire explicitement ici — vérifié en
        s'assurant qu'aucun appel à win.destroy()/win.withdraw() n'existe
        dans purge_now()."""
        body = _purge_now_body(_main_source())
        self.assertNotIn("win.destroy()", body)
        self.assertNotIn("win.withdraw()", body)

    def test_ne_duplique_aucune_logique_de_purge(self):
        """Jamais de second circuit parallèle : purge_now() ne fait que
        vérifier l'absence de tournoi ouvert puis déléguer entièrement à
        DataPurgeDialog — aucune manipulation de fichier directe, aucun
        appel à data_purge.* ici."""
        body = _purge_now_body(_main_source())
        self.assertNotIn("data_purge.", body)
        self.assertNotIn("os.remove", body)


if __name__ == "__main__":
    unittest.main()
