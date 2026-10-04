# -*- coding: utf-8 -*-
"""Lot 2 — "Gestion des absences et des retardataires" (cahier des
charges du 2026-09-28), couverture App._on_abs_des_clicked/_reinstate_
selected/_withdraw_selected au niveau UNITAIRE, sur une doublure App
minimale (jamais de vrai Tk) — même principe que tests/test_players_
right_click_eliminate.py: EliminateSelectedShortcutTest, dont ce fichier
reprend directement le patron de doublure (_FakeAppForEliminateSelected).

Couvre :
- La machine à états de "Abs/Dés" (signaler / désactiver selon l'état de
  chaque joueur ciblé et le délai des retardataires) — cahier des
  charges, section 2 et 4.
- La désactivation EXIGE toujours une confirmation explicite (précaution
  du 2026-09-28), y compris depuis ce nouveau chemin.
- "Réinscrire" distingue un joueur actif signalé absent (cancel_absence)
  d'un joueur réellement désactivé (reinstate_player, INCHANGÉE)."""
import os
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402


def _new_db(tmp_dir, name="A"):
    """« Gestion de l'accueil » activée par défaut pour ces fixtures
    (modification du 2026-09-29) : ce module teste précisément la
    machine à états "Abs/Dés", réglage propre au tournoi Open qui
    l'active — voir test_accueil_management_toggle.py pour la
    couverture du réglage désactivé (comportement par défaut : bouton
    "Désactiver (forfait)" classique)."""
    db = database.Database(os.path.join(tmp_dir, f"{name}.tournoi"))
    db.set_settings({"accueil_management_enabled": 1})
    return db


def _start_tournament(db, minutes_ago=5, duration_minutes=60):
    import time
    start_epoch = int(time.time()) - minutes_ago * 60
    db.set_settings({
        "clock_started": 1,
        "tournament_start_epoch": start_epoch,
        "absence_duration_minutes": duration_minutes,
    })


class _FakeAppForAbsDes:
    """Doublure minimale de App — self.db est une VRAIE Database ; le
    reste (sélection, rafraîchissement) sont des doublures/espions,
    jamais la vraie logique UI (voir la docstring du module)."""

    def __init__(self, db, ids):
        self.db = db
        self._ids = list(ids)
        self.clear_checked_calls = 0
        self.refresh_all_calls = 0
        self.refresh_remote_players_calls = 0
        self.refresh_remote_checkin_calls = 0
        # _on_abs_des_clicked RÉUTILISE _withdraw_selected (jamais
        # dupliquée, voir sa docstring dans main.py) : liée ici pour de
        # vrai plutôt que doublée, exactement comme _eliminate_selected
        # est liée dans _FakeAppForEliminateSelected (tests/test_players_
        # right_click_eliminate.py).
        self._withdraw_selected = types.MethodType(main.App._withdraw_selected, self)
        # Correctif du 2026-10-03 ("Inscrire ignore le joueur sélectionné
        # quand un autre est déjà accueilli") : _reinstate_selected lit
        # désormais players_tree.selection() directement quand l'accueil
        # pilote la case — exposé ici (mêmes _ids que _checked_or_
        # selected_ids, ce conflit n'étant pas l'objet de ces tests, voir
        # tests/test_inscrire_ignore_deja_accueillis.py) avec la méthode
        # réelle greffée pour refléter le vrai réglage accueil/démarrage.
        self.players_tree = types.SimpleNamespace(
            selection=lambda: [str(pid) for pid in self._ids]
        )

    def _players_checkbox_drives_accueil(self):
        return main.App._players_checkbox_drives_accueil(self)

    def _checked_or_selected_ids(self):
        return list(self._ids)

    def _clear_checked(self):
        self.clear_checked_calls += 1

    def _refresh_all(self):
        self.refresh_all_calls += 1

    def _refresh_remote_players_cache(self):
        self.refresh_remote_players_calls += 1

    def _refresh_remote_checkin_cache(self):
        self.refresh_remote_checkin_calls += 1


class AbsDesDeclareTest(unittest.TestCase):
    """Joueur ACTIF, pas encore signalé absent -> signale son absence,
    AUCUNE confirmation."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="abs_des_declare_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db)

    def test_signale_l_absence_sans_confirmation(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno") as mock_confirm, \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._on_abs_des_clicked(app)

        mock_confirm.assert_not_called()
        mock_info.assert_not_called()
        self.assertIsNotNone(self.db.get_player(self.pid)["absent_declared_at"])
        self.assertEqual(app.refresh_all_calls, 1)
        self.assertEqual(app.refresh_remote_players_calls, 1)

    def test_ne_touche_ni_jetons_ni_table_ni_siege(self):
        before = dict(self.db.get_player(self.pid))
        app = _FakeAppForAbsDes(self.db, [self.pid])

        main.App._on_abs_des_clicked(app)

        after = dict(self.db.get_player(self.pid))
        for key in ("table_id", "seat", "chips", "status"):
            self.assertEqual(after[key], before[key], key)

    def test_selection_vide_ne_fait_rien(self):
        app = _FakeAppForAbsDes(self.db, [])

        main.App._on_abs_des_clicked(app)

        self.assertEqual(app.refresh_all_calls, 0)

    def test_reclic_sur_un_joueur_deja_signale_ne_re_signale_pas_silencieusement(self):
        """Une fois signalé, un second clic sur CE MÊME joueur (délai pas
        encore dépassé) tombe dans la branche "pas encore éligible" —
        JAMAIS un second appel silencieux à declare_absent (l'idempotence
        de Database.declare_absent lui-même, pour des déclarations
        RÉELLEMENT répétées depuis plusieurs appareils, est déjà couverte
        séparément — voir tests/test_absence_management.py)."""
        app = _FakeAppForAbsDes(self.db, [self.pid])
        main.App._on_abs_des_clicked(app)
        first = self.db.get_player(self.pid)["absent_declared_at"]

        with patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._on_abs_des_clicked(app)

        mock_info.assert_called_once()
        second = self.db.get_player(self.pid)["absent_declared_at"]
        self.assertEqual(second, first)  # inchangé, jamais rafraîchi par ce second clic

    def test_ignore_un_joueur_deja_eliminee(self):
        self.db.eliminate_player(self.pid)
        app = _FakeAppForAbsDes(self.db, [self.pid])

        main.App._on_abs_des_clicked(app)

        self.assertIsNone(self.db.get_player(self.pid)["absent_declared_at"])
        self.assertEqual(app.refresh_all_calls, 0)


class AbsDesWithdrawTest(unittest.TestCase):
    """Joueur ACTIF, DÉJÀ signalé absent, délai dépassé -> propose la
    désactivation définitive — EXIGE une confirmation explicite (précaution
    du 2026-09-28), réutilise le mécanisme historique de _withdraw_selected."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="abs_des_withdraw_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        self.db.add_player("Bob")
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)
        self.db.declare_absent(self.pid)

    def test_demande_confirmation_avant_de_desactiver(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=True) as mock_confirm:
            main.App._on_abs_des_clicked(app)

        mock_confirm.assert_called_once()
        self.assertEqual(self.db.get_player(self.pid)["status"], "withdrawn")

    def test_refus_de_la_confirmation_ne_desactive_pas(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=False):
            main.App._on_abs_des_clicked(app)

        self.assertEqual(self.db.get_player(self.pid)["status"], "active")

    def test_desactivation_applique_le_reequilibrage_historique(self):
        """Ne modifie pas le mécanisme de rééquilibrage — vérifié en
        constatant que withdraw_player (RÉUTILISÉE, jamais dupliquée) a
        bien libéré table/siège, exactement comme avant ce chantier."""
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._on_abs_des_clicked(app)

        after = self.db.get_player(self.pid)
        self.assertIsNone(after["table_id"])
        self.assertIsNone(after["seat"])


class AbsDesNotYetEligibleTest(unittest.TestCase):
    """Joueur ACTIF, DÉJÀ signalé absent, délai PAS dépassé -> ignoré,
    message informatif si c'est la seule sélection."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="abs_des_not_yet_eligible_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid = self.db.add_player("Alice")
        _start_tournament(self.db, minutes_ago=5, duration_minutes=60)
        self.db.declare_absent(self.pid)

    def test_message_informatif_seule_selection(self):
        app = _FakeAppForAbsDes(self.db, [self.pid])

        with patch.object(main.messagebox, "askyesno") as mock_confirm, \
             patch.object(main.messagebox, "showinfo") as mock_info:
            main.App._on_abs_des_clicked(app)

        mock_confirm.assert_not_called()
        mock_info.assert_called_once()
        self.assertEqual(self.db.get_player(self.pid)["status"], "active")
        self.assertEqual(app.refresh_all_calls, 0)


class AbsDesMixedSelectionTest(unittest.TestCase):
    """Une même sélection peut mélanger des joueurs à des stades
    différents — chacun est traité selon SON PROPRE état, en un seul
    clic (cahier des charges : rien n'empêche de cocher plusieurs
    joueurs à la fois pour cette commande, comme pour les autres
    commandes groupées de cet onglet)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="abs_des_mixed_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)
        self.pid_new = self.db.add_player("Alice")     # pas encore signalée
        self.pid_eligible = self.db.add_player("Bob")   # signalée, délai (déjà) dépassé
        # Délai des retardataires DÉJÀ dépassé pour tout le tournoi (délai
        # global, voir Database.absence_deadline_epoch) — Bob signalé
        # absent DANS ces conditions est donc immédiatement éligible à la
        # désactivation, tandis qu'Alice, jamais encore signalée, doit
        # simplement l'être par ce même clic groupé.
        _start_tournament(self.db, minutes_ago=61, duration_minutes=60)
        self.db.declare_absent(self.pid_eligible)

    def test_traite_chaque_joueur_selon_son_propre_etat(self):
        app = _FakeAppForAbsDes(self.db, [self.pid_new, self.pid_eligible])

        with patch.object(main.messagebox, "askyesno", return_value=True):
            main.App._on_abs_des_clicked(app)

        self.assertIsNotNone(self.db.get_player(self.pid_new)["absent_declared_at"])
        self.assertEqual(self.db.get_player(self.pid_eligible)["status"], "withdrawn")


class ReinstateDistinguishesActiveFromWithdrawnTest(unittest.TestCase):
    """"Réinscrire" DISTINGUE IMPÉRATIVEMENT (cahier des charges section 3)
    un joueur encore actif mais signalé absent d'un joueur réellement
    désactivé."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="reinstate_distinguish_test_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(self._tmp.name)
        self.addCleanup(self.db.conn.close)

    def test_joueur_actif_signale_absent_confirme_son_retour(self):
        """Correctif du 2026-09-29 ("retour d'un joueur absent") :
        "Réinscrire" ne se contente plus d'annuler le signalement — il
        confirme aussi l'arrivée (arrived_at), exactement le même
        résultat que le téléphone (voir tests/test_return_from_absence.py
        pour la couverture dédiée de ce correctif)."""
        pid = self.db.add_player("Alice")
        _start_tournament(self.db)
        self.db.declare_absent(pid)
        self.db.set_chips(pid, 33333)
        app = _FakeAppForAbsDes(self.db, [pid])

        main.App._reinstate_selected(app)

        after = self.db.get_player(pid)
        self.assertIsNone(after["absent_declared_at"])
        self.assertIsNotNone(after["arrived_at"])
        self.assertEqual(after["chips"], 33333)  # jamais réinitialisés
        self.assertEqual(after["status"], "active")

    def test_joueur_reellement_desactive_utilise_reinstate_player(self):
        pid = self.db.add_player("Alice")
        self.db.withdraw_player(pid)
        app = _FakeAppForAbsDes(self.db, [pid])

        main.App._reinstate_selected(app)

        after = self.db.get_player(pid)
        self.assertEqual(after["status"], "active")
        self.assertIsNotNone(after["table_id"])  # reinstate_player réattribue un siège

    def test_selection_mixte_traite_chacun_correctement(self):
        pid_absent = self.db.add_player("Alice")
        pid_withdrawn = self.db.add_player("Bob")
        _start_tournament(self.db)
        self.db.declare_absent(pid_absent)
        self.db.set_chips(pid_absent, 5000)
        self.db.withdraw_player(pid_withdrawn)
        app = _FakeAppForAbsDes(self.db, [pid_absent, pid_withdrawn])

        main.App._reinstate_selected(app)

        self.assertEqual(self.db.get_player(pid_absent)["chips"], 5000)
        self.assertIsNone(self.db.get_player(pid_absent)["absent_declared_at"])
        self.assertIsNotNone(self.db.get_player(pid_absent)["arrived_at"])
        self.assertEqual(self.db.get_player(pid_withdrawn)["status"], "active")


if __name__ == "__main__":
    unittest.main()
