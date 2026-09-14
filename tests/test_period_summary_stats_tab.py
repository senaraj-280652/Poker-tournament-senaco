# -*- coding: utf-8 -*-
"""Campagne de vérification de l'onglet "Statistiques" (demande du
2026-09-14) — PeriodSummaryDialog (main.py) et sa source de données,
Database.build_period_summary (database.py).

RECENSEMENT (voir aussi le rapport livré séparément) :

- Source unique de toutes les statistiques affichées : build_period_
  summary(folder, date_from, date_to, recursive), qui reparcourt TOUS
  les fichiers .tournoi du dossier choisi à chaque clic sur "Générer la
  synthèse" (_generate) — jamais recalculé automatiquement autrement :
  ni au tick périodique de l'appli, ni à l'ouverture de l'onglet, ni
  quand un autre tournoi change d'état. Le SEUL autre déclencheur de
  rafraîchissement est le filtre "Club" (_refresh_display), qui ne
  refait PAS l'analyse des fichiers : il ne fait que re-filtrer
  `self.summary` déjà en mémoire.
- Chaque "tournoi" de la synthèse vient de get_tournament_date, get_
  setting("tournament_name"), list_players, get_primes_summary (donc
  get_presence_bonuses/get_assiduity_bonuses/get_ranking_bonuses/get_
  bounty_bonuses), get_stats (prize_pool) et get_payouts_amounts —
  exactement les mêmes fonctions que les onglets Primes/Classement/
  Joueurs de CE tournoi, jamais un calcul dupliqué indépendant.
- Chaque "joueur" agrégé sur la période : tournaments_played (compte
  chaque fichier où le joueur est resté "active" ou a été réellement
  "eliminated" — PAS un forfait "withdrawn", règle métier validée le
  2026-09-15, voir ForfaitNeCompteJamaisCommeTournoiJoueTest : un
  forfait reste néanmoins agrégé dans "players" comme une participation
  administrative, total_cost/total_bounty_won/total_points compris),
  wins (place == 1), best_place (minimum des places connues), total_cost/
  total_gain (€, jamais affichés dans le tableau à l'écran — seulement
  disponibles à l'export), total_bounty_won et total_points (somme du
  "total" de get_primes_summary pour ce joueur, tournoi par tournoi).
- Filtres/sélections : dossier + "Inclure les sous-dossiers" + période
  (date_from/date_to, bornes INCLUSES) => reparcourent les fichiers ;
  filtre "Club" => ne touche que l'AFFICHAGE (voir _refresh_display) ET,
  depuis le correctif du 2026-09-14 (priorité 2), l'export lancé depuis
  "Exporter..." dans la foulée (voir _club_filtered_players, réutilisée
  par _open_export_dialog — voir ClubFilterExportConsistencyTest).
  La LISTE des clubs proposés dans ce filtre reste, elle, figée une
  seule fois à la construction de l'onglet — VOLONTAIREMENT non touché
  par cette demande (voir
  test_liste_des_clubs_du_filtre_jamais_rafraichie_apres_coup).
- Un fichier .tournoi illisible/corrompu dans le dossier choisi est,
  depuis le correctif du 2026-09-14 (priorité 1), ignoré proprement
  (connexion refermée, fichier identifié dans ~/.poker_tournament/
  crash.log via _log_skipped_tournament_file, aucune popup) : les
  autres tournois valides du même dossier continuent d'être chargés
  normalement (voir FichierIllisibleIgnoreProprementTest).
- "Terminé" (priorité 3) : réutilise désormais EXACTEMENT la règle du
  Lobby (Database.get_live_status()["finished"] — <= 1 actif restant ET
  plus d'un joueur au total), au lieu d'une définition indépendante
  ("== 1" actif) qui divergeait dès que TOUS les joueurs avaient
  finalement quitté un tournoi (voir StatutTermineAligneAvecLobbyTest).
  "Vainqueur" reste distinct : "-" dès que ce n'est pas EXACTEMENT 1
  joueur actif restant (0 comme plusieurs), jamais une IndexError.

Conventions reprises du reste de la suite : fichiers .tournoi
synthétiques en dossier temporaire (jamais ~/.poker_tournament ni un
vrai fichier), roster.py/export_prefs.py toujours patchés vers un
fichier temporaire, `types.MethodType` pour lier de vraies méthodes de
PeriodSummaryDialog à une instance réelle construite sur un tk.Tk()
caché."""
import math
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import tkinter as tk  # noqa: E402

import database  # noqa: E402
import export_prefs  # noqa: E402
import main  # noqa: E402
import roster  # noqa: E402

try:
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except tk.TclError:
    _TK_AVAILABLE = False


def _new_db(tmp_dir, filename, **settings):
    """Fichier .tournoi synthétique, réglages par défaut désactivant les
    axes non testés (assiduité 0 = désactivée) sauf ceux passés en
    kwargs. `tournament_date` DOIT être fourni par l'appelant pour tout
    test sensible aux dates (sinon repli sur la date de création du
    fichier, non déterministe d'un run à l'autre)."""
    path = os.path.join(tmp_dir, filename)
    db = database.Database(path)
    if settings:
        db.set_settings({k: str(v) for k, v in settings.items()})
    return db


# =====================================================================
# 1. build_period_summary — scénarios de base (un seul tournoi).
# =====================================================================
class TournoiNeufAucuneDonneeTest(unittest.TestCase):
    """Scénario "tournoi neuf / aucune donnée" : un .tournoi tout juste
    créé, aucun joueur. La synthèse doit lister ce tournoi (entries=0,
    "En cours", vainqueur "-") sans qu'aucun joueur n'apparaisse."""

    def test_tournoi_vide(self):
        with tempfile.TemporaryDirectory(prefix="stats_empty_") as tmp:
            db = _new_db(tmp, "vide.tournoi", tournament_date="2026-01-05",
                         tournament_name="Vide")
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            self.assertEqual(len(summary["tournaments"]), 1)
            t = summary["tournaments"][0]
            self.assertEqual(t["name"], "Vide")
            self.assertEqual(t["entries"], 0)
            self.assertEqual(t["status"], "En cours")
            self.assertEqual(t["winner"], "-")
            self.assertEqual(t["bounty_distributed"], 0)
            self.assertEqual(summary["players"], [])

    def test_dossier_totalement_vide(self):
        """Aucun fichier .tournoi du tout dans le dossier : synthèse
        vide des deux côtés, jamais une exception."""
        with tempfile.TemporaryDirectory(prefix="stats_no_files_") as tmp:
            summary = database.build_period_summary(tmp, recursive=False)
            self.assertEqual(summary, {"tournaments": [], "players": []})


class TournoiQuelquesJoueursEnCoursTest(unittest.TestCase):
    """Scénario "tournoi avec quelques joueurs", encore en cours (aucune
    élimination) : statut "En cours", vainqueur "-", primes de présence
    seules (aucun classement/bounty possible sans rang connu)."""

    def test_quatre_joueurs_aucune_elimination(self):
        with tempfile.TemporaryDirectory(prefix="stats_inprogress_") as tmp:
            db = _new_db(
                tmp, "encours.tournoi", tournament_date="2026-01-06",
                tournament_name="En cours", attendance_bonus_points=5,
            )
            for name in ("Alice", "Bob", "Chris", "Dana"):
                db.add_player(name)
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            t = summary["tournaments"][0]
            self.assertEqual(t["entries"], 4)
            self.assertEqual(t["status"], "En cours")
            self.assertEqual(t["winner"], "-")
            self.assertEqual(t["bounty_distributed"], 0)  # aucun kill possible

            self.assertEqual(len(summary["players"]), 4)
            for a in summary["players"]:
                # Indépendamment recalculé : seule la prime de présence
                # (5 pts) peut s'appliquer, personne n'a de rang connu.
                self.assertEqual(a["total_points"], 5)
                self.assertIsNone(a["best_place"])
                self.assertEqual(a["wins"], 0)
                self.assertEqual(a["tournaments_played"], 1)


class TournoiCompletEliminationOrdreControleTest(unittest.TestCase):
    """Scénario "tournoi terminé avec classement complet" + "joueurs
    éliminés dans différents ordres" : 4 joueurs, éliminations dans un
    ordre précis, chaque valeur de la synthèse recalculée à la main
    (formules documentées dans database.py : ranking_points "Classique"
    = 100×√N/place arrondi ; bounty classique = kills × round(10×√N)
    quand bounty_amount=0) et comparée au résultat de Senaco — jamais
    seulement "pas d'exception"."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_full_")
        self.addCleanup(self._tmp.cleanup)
        self.db = _new_db(
            self._tmp.name, "complet.tournoi",
            tournament_date="2026-01-07", tournament_name="Complet",
            attendance_bonus_points=5, ranking_formula="current",
            bounty_amount=0,  # -> formule 10x√N (voir bounty_unit_value)
        )
        self.addCleanup(self.db.conn.close)
        self.alice = self.db.add_player("Alice")
        self.bob = self.db.add_player("Bob")
        self.chris = self.db.add_player("Chris")
        self.dana = self.db.add_player("Dana")

        # Ordre d'élimination volontairement différent de l'ordre
        # d'inscription : Dana (4e) puis Chris (3e) puis Bob (2e),
        # Alice survit seule (1re).
        self.db.eliminate_player(self.dana, eliminated_by_id=self.alice)
        self.db.eliminate_player(self.chris, eliminated_by_id=self.bob)
        self.db.eliminate_player(self.bob, eliminated_by_id=self.alice)

    def _expected_ranking(self, place, n_players=4):
        return round(100 * math.sqrt(n_players) / place)

    def _expected_bounty_value(self, n_players=4):
        return round(10 * math.sqrt(n_players))

    def test_synthese_correspond_au_calcul_independant(self):
        summary = database.build_period_summary(self._tmp.name, recursive=False)
        t = summary["tournaments"][0]
        self.assertEqual(t["status"], "Terminé")
        self.assertEqual(t["winner"], "Alice")
        self.assertEqual(t["entries"], 4)

        bv = self._expected_bounty_value()  # round(10*sqrt(4)) = 20
        self.assertEqual(bv, 20)
        expected_bounty_distributed = 2 * bv + 1 * bv  # Alice 2 kills, Bob 1 kill
        self.assertEqual(t["bounty_distributed"], expected_bounty_distributed)
        self.assertEqual(t["bounty_distributed"], 60)

        by_name = {a["name"]: a for a in summary["players"]}
        expected = {
            "Alice": {
                "place": 1, "kills": 2,
                "points": 5 + self._expected_ranking(1) + 2 * bv,
            },
            "Bob": {
                "place": 2, "kills": 1,
                "points": 5 + self._expected_ranking(2) + 1 * bv,
            },
            "Chris": {
                "place": 3, "kills": 0,
                "points": 5 + self._expected_ranking(3) + 0,
            },
            "Dana": {
                "place": 4, "kills": 0,
                "points": 5 + self._expected_ranking(4) + 0,
            },
        }
        # Valeurs numériques attendues, écrites en clair pour ne dépendre
        # d'aucune fonction de production dans le calcul "indépendant" :
        # 100*sqrt(4)=200 -> 200/1=200, 200/2=100, 200/3=66.67->67, 200/4=50.
        self.assertEqual(expected["Alice"]["points"], 5 + 200 + 40)   # 245
        self.assertEqual(expected["Bob"]["points"], 5 + 100 + 20)     # 125
        self.assertEqual(expected["Chris"]["points"], 5 + 67 + 0)     # 72
        self.assertEqual(expected["Dana"]["points"], 5 + 50 + 0)      # 55

        for name, exp in expected.items():
            a = by_name[name]
            self.assertEqual(a["best_place"], exp["place"], name)
            self.assertEqual(a["wins"], 1 if exp["place"] == 1 else 0, name)
            self.assertEqual(a["total_points"], exp["points"], name)
            self.assertEqual(a["tournaments_played"], 1, name)

        self.assertEqual(by_name["Alice"]["total_bounty_won"], 2 * bv)
        self.assertEqual(by_name["Bob"]["total_bounty_won"], 1 * bv)
        self.assertEqual(by_name["Chris"]["total_bounty_won"], 0)

    def test_total_cost_et_gain_recalcules_independamment(self):
        """total_cost/total_gain (€) : présents dans build_period_summary
        et exportables (PERIOD_PLAYER_COLUMNS) mais ABSENTS du tableau
        affiché à l'écran (voir cols_p dans PeriodSummaryDialog.__init__,
        recensement du rapport) — vérifiés ici indépendamment tout de
        même puisqu'ils font partie du contrat de build_period_summary."""
        summary = database.build_period_summary(self._tmp.name, recursive=False)
        by_name = {a["name"]: a for a in summary["players"]}
        # buyin_amount par défaut (DEFAULT_SETTINGS) = 50 ; 1 achat chacun.
        for name in ("Alice", "Bob", "Chris", "Dana"):
            self.assertEqual(by_name[name]["total_cost"], 50.0, name)
        # payout_structure par défaut = {1: 100%} ; pool = 4*50*(1-0%) = 200 ;
        # seule la 1re place (Alice) touche un gain.
        self.assertEqual(by_name["Alice"]["total_gain"], 200.0)
        for name in ("Bob", "Chris", "Dana"):
            self.assertEqual(by_name[name]["total_gain"], 0.0, name)


class PrimesDesactiveesTest(unittest.TestCase):
    """Cas limite : primes_enabled=0 pour ce tournoi -> aucune prime nulle
    part (get_primes_summary renvoie déjà []), mais le joueur compte
    quand même comme ayant "joué" ce tournoi (tournaments_played)."""

    def test_bounty_distribue_nul_et_points_nuls_mais_tournoi_compte(self):
        with tempfile.TemporaryDirectory(prefix="stats_noprimes_") as tmp:
            db = _new_db(
                tmp, "sansprimes.tournoi", tournament_date="2026-01-08",
                primes_enabled=0, attendance_bonus_points=5, bounty_amount=10,
            )
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            db.eliminate_player(b, eliminated_by_id=a)
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            t = summary["tournaments"][0]
            self.assertEqual(t["bounty_distributed"], 0)
            by_name = {a["name"]: a for a in summary["players"]}
            self.assertEqual(by_name["Alice"]["total_points"], 0)
            self.assertEqual(by_name["Alice"]["total_bounty_won"], 0)
            # Alice a malgré tout bien "joué" ce tournoi.
            self.assertEqual(by_name["Alice"]["tournaments_played"], 1)


class SitAndGoTraiteCommeUnTournoiNormalTest(unittest.TestCase):
    """Scénario "Sit & Go" : build_period_summary ne consulte jamais le
    réglage `is_sng` — un Sit & Go doit apparaître exactement comme un
    tournoi normal dans la synthèse (aucune exclusion, aucun traitement
    spécial)."""

    def test_sit_and_go_inclus_normalement(self):
        with tempfile.TemporaryDirectory(prefix="stats_sng_") as tmp:
            db = _new_db(
                tmp, "sng.tournoi", tournament_date="2026-01-09",
                tournament_name="SnG rapide", is_sng=1,
            )
            db.add_player("Alice")
            db.add_player("Bob")
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            self.assertEqual(len(summary["tournaments"]), 1)
            self.assertEqual(summary["tournaments"][0]["entries"], 2)
            self.assertEqual(len(summary["players"]), 2)


# =====================================================================
# 2. build_period_summary — historique multi-tournois + filtre de dates.
# =====================================================================
class HistoriqueMultiTournoisTest(unittest.TestCase):
    """Scénario "plusieurs tournois dans l'historique" : deux fichiers,
    un joueur commun (Alice, les deux) et deux joueurs occasionnels (Bob
    dans T1 seulement, Chris dans T2 seulement) — vérifie l'agrégation
    ET le filtre de dates (bornes incluses), recalculés indépendamment."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_history_")
        self.addCleanup(self._tmp.cleanup)
        # Noms de fichiers triés alphabétiquement dans l'ordre chronologique
        # voulu (find_tournament_files trie par chemin, pas par date).
        db1 = _new_db(
            self._tmp.name, "T1.tournoi", tournament_date="2026-01-10",
            tournament_name="Soirée 1", bounty_amount=10, ranking_formula="none",
        )
        a1 = db1.add_player("Alice")
        b1 = db1.add_player("Bob")
        db1.eliminate_player(b1, eliminated_by_id=a1)
        db1.conn.close()

        db2 = _new_db(
            self._tmp.name, "T2.tournoi", tournament_date="2026-01-15",
            tournament_name="Soirée 2", bounty_amount=10, ranking_formula="none",
        )
        a2 = db2.add_player("Alice")
        c2 = db2.add_player("Chris")
        db2.eliminate_player(c2, eliminated_by_id=a2)
        db2.conn.close()

    def test_agregation_sans_filtre_de_dates(self):
        summary = database.build_period_summary(self._tmp.name, recursive=False)
        self.assertEqual([t["name"] for t in summary["tournaments"]], ["Soirée 1", "Soirée 2"])

        by_name = {a["name"]: a for a in summary["players"]}
        self.assertEqual(set(by_name), {"Alice", "Bob", "Chris"})

        alice = by_name["Alice"]
        self.assertEqual(alice["tournaments_played"], 2)
        self.assertEqual(alice["wins"], 2)
        self.assertEqual(alice["best_place"], 1)
        self.assertEqual(alice["total_bounty_won"], 20)  # 10 (T1) + 10 (T2)
        self.assertEqual(alice["total_points"], 20)      # ranking_formula "none"

        for name in ("Bob", "Chris"):
            p = by_name[name]
            self.assertEqual(p["tournaments_played"], 1, name)
            self.assertEqual(p["wins"], 0, name)
            self.assertEqual(p["best_place"], 2, name)
            self.assertEqual(p["total_points"], 0, name)

        # Tri par total_points décroissant ; à égalité (Bob/Chris à 0),
        # ordre stable = ordre de première apparition dans les fichiers
        # (T1 avant T2) : Bob avant Chris.
        self.assertEqual([a["name"] for a in summary["players"]], ["Alice", "Bob", "Chris"])

    def test_filtre_de_dates_exclut_aussi_les_stats_du_joueur_commun(self):
        """Le filtrage par date ne doit pas seulement retirer T1 de la
        liste des tournois : les points qu'Alice y a gagnés doivent
        aussi disparaître de son agrégat (pas seulement du compte de
        tournois joués), et Bob (jamais présent dans un tournoi INCLUS)
        ne doit plus apparaître du tout."""
        summary = database.build_period_summary(
            self._tmp.name, date_from="2026-01-12", recursive=False,
        )
        self.assertEqual([t["name"] for t in summary["tournaments"]], ["Soirée 2"])
        by_name = {a["name"]: a for a in summary["players"]}
        self.assertNotIn("Bob", by_name)
        self.assertEqual(by_name["Alice"]["tournaments_played"], 1)
        self.assertEqual(by_name["Alice"]["total_points"], 10)  # T2 seulement
        self.assertEqual(by_name["Chris"]["tournaments_played"], 1)

    def test_bornes_de_periode_incluses(self):
        """"bornes incluses" (docstring de build_period_summary) : une
        date_from/date_to EXACTEMENT égale à la date d'un tournoi doit
        l'inclure, pas l'exclure."""
        only_t1 = database.build_period_summary(
            self._tmp.name, date_from="2026-01-10", date_to="2026-01-10", recursive=False,
        )
        self.assertEqual([t["name"] for t in only_t1["tournaments"]], ["Soirée 1"])

        only_t2 = database.build_period_summary(
            self._tmp.name, date_from="2026-01-15", date_to="2026-01-15", recursive=False,
        )
        self.assertEqual([t["name"] for t in only_t2["tournaments"]], ["Soirée 2"])

        none_included = database.build_period_summary(
            self._tmp.name, date_from="2026-01-11", date_to="2026-01-14", recursive=False,
        )
        self.assertEqual(none_included["tournaments"], [])
        self.assertEqual(none_included["players"], [])


class ForfaitNeCompteJamaisCommeTournoiJoueTest(unittest.TestCase):
    """Règle métier validée le 2026-09-15 (remplace l'ancien comportement
    documenté dans ce test, voir git history) : un joueur inscrit puis
    déclaré forfait (Database.withdraw_player — inscrit, blindes
    éventuellement prélevées pendant son absence, déclaré forfait au
    bout d'~1h) a bien participé ADMINISTRATIVEMENT à ce tournoi (il
    reste dans "players", continue de peser sur total_cost/total_
    bounty_won/total_points exactement comme avant), mais ce tournoi ne
    doit PLUS compter dans son nombre de tournois JOUÉS
    (tournaments_played)."""

    def test_joueur_normal_present_incremente_tournaments_played(self):
        """Joueur encore "active" en fin de tournoi (le vainqueur, ou
        n'importe quel joueur actif d'un tournoi pas encore terminé) :
        compte comme un tournoi joué."""
        with tempfile.TemporaryDirectory(prefix="stats_normal_") as tmp:
            db = _new_db(tmp, "normal.tournoi", tournament_date="2026-01-21")
            db.add_player("Alice")
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            alice = next(a for a in summary["players"] if a["name"] == "Alice")
            self.assertEqual(alice["tournaments_played"], 1)

    def test_joueur_elimine_normalement_incremente_tournaments_played(self):
        """Joueur réellement éliminé en cours de partie (status=
        'eliminated', une vraie place lui a été attribuée) : compte
        comme un tournoi joué, quel que soit son rang final."""
        with tempfile.TemporaryDirectory(prefix="stats_eliminated_") as tmp:
            db = _new_db(tmp, "elimine.tournoi", tournament_date="2026-01-22")
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            db.eliminate_player(b, eliminated_by_id=a)
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            by_name = {p["name"]: p for p in summary["players"]}
            self.assertEqual(by_name["Bob"]["tournaments_played"], 1)
            self.assertEqual(by_name["Alice"]["tournaments_played"], 1)

    def test_joueur_forfait_ne_compte_pas_comme_tournoi_joue(self):
        """Cœur de la règle : un forfait (status='withdrawn') laisse
        tournaments_played à 0 pour CE tournoi, même si son buy-in a
        bien été encaissé (compté dans "entries" au niveau du tournoi,
        comportement de "entries" inchangé par cette règle — voir
        _new_db/add_player, qui enregistre le buy-in dès l'inscription)."""
        with tempfile.TemporaryDirectory(prefix="stats_withdrawn_") as tmp:
            db = _new_db(tmp, "forfait.tournoi", tournament_date="2026-01-11")
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            db.withdraw_player(b)  # forfait avant toute élimination réelle
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            by_name = {p["name"]: p for p in summary["players"]}
            # Bob reste bien présent dans les statistiques (participation
            # administrative), mais SANS que ce tournoi ne compte comme joué.
            self.assertIn("Bob", by_name)
            self.assertEqual(by_name["Bob"]["tournaments_played"], 0)
            # Le buy-in de Bob reste compté au niveau du tournoi (entries) :
            # règle inchangée, sans rapport avec tournaments_played.
            self.assertEqual(summary["tournaments"][0]["entries"], 2)
            # Alice, elle, a normalement joué (encore active en fin de
            # tournoi non terminé) : inchangée par cette règle.
            self.assertEqual(by_name["Alice"]["tournaments_played"], 1)

    def test_forfait_ne_cree_ni_victoire_ni_classement(self):
        """"wins"/"best_place" restent, comme avant cette règle,
        totalement inatteignables pour un forfait : aucune "place" ne
        lui est jamais attribuée (ni "eliminated" ni "active"+terminé) —
        vérifié explicitement pour ne pas dépendre d'une coïncidence."""
        with tempfile.TemporaryDirectory(prefix="stats_withdrawn_no_win_") as tmp:
            db = _new_db(tmp, "forfait2.tournoi", tournament_date="2026-01-23")
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            db.eliminate_player(b, eliminated_by_id=a)  # Alice seule active
            db.withdraw_player(a)  # ... puis Alice forfait aussi (0 actif)
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            alice = next(p for p in summary["players"] if p["name"] == "Alice")
            self.assertEqual(alice["tournaments_played"], 0)  # forfait, jamais joué
            self.assertEqual(alice["wins"], 0)
            self.assertIsNone(alice["best_place"])
            # Le tournoi est bien "Terminé" (voir StatutTermineAligneAvecLobbyTest,
            # 0 actif restant) mais SANS vainqueur ni classement pour Alice.
            self.assertEqual(summary["tournaments"][0]["status"], "Terminé")
            self.assertEqual(summary["tournaments"][0]["winner"], "-")

    def test_forfait_ne_change_ni_cout_ni_primes_deja_dues(self):
        """Non-régression explicite (demande du 2026-09-15, "ne change pas
        d'autres règles métier") : total_cost/total_bounty_won/
        total_points d'un forfait restent calculés exactement comme
        avant cette règle — seul tournaments_played change."""
        with tempfile.TemporaryDirectory(prefix="stats_withdrawn_cost_") as tmp:
            db = _new_db(
                tmp, "forfait3.tournoi", tournament_date="2026-01-24",
                attendance_bonus_points=5,
            )
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            db.withdraw_player(b)
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            bob = next(p for p in summary["players"] if p["name"] == "Bob")
            self.assertEqual(bob["tournaments_played"], 0)
            # Coût du buy-in déjà encaissé avant le forfait : toujours
            # compté (buyin_amount par défaut = 50, voir DEFAULT_SETTINGS).
            self.assertEqual(bob["total_cost"], 50.0)
            # Prime de présence (5 pts) : get_presence_bonuses boucle sur
            # TOUS les joueurs (voir sa docstring), forfait compris —
            # inchangé par cette règle, qui ne touche QUE tournaments_played.
            self.assertEqual(bob["total_points"], 5)
            self.assertEqual(bob["total_bounty_won"], 0)  # aucun kill

    def test_melange_multi_tournois_avec_certains_forfaits(self):
        """Historique de 3 tournois pour un même joueur : joué, forfait,
        joué à nouveau -> tournaments_played doit valoir 2 (pas 3), le
        forfait au milieu n'ayant jamais dû être compté."""
        with tempfile.TemporaryDirectory(prefix="stats_mixed_forfeits_") as tmp:
            db1 = _new_db(tmp, "M1.tournoi", tournament_date="2026-02-01")
            db1.add_player("Alice")
            db1.conn.close()

            db2 = _new_db(tmp, "M2.tournoi", tournament_date="2026-02-02")
            a2 = db2.add_player("Alice")
            db2.withdraw_player(a2)  # forfait ce soir-là
            db2.conn.close()

            db3 = _new_db(tmp, "M3.tournoi", tournament_date="2026-02-03")
            db3.add_player("Alice")
            db3.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            alice = next(p for p in summary["players"] if p["name"] == "Alice")
            self.assertEqual(alice["tournaments_played"], 2)  # M1 et M3 seulement

    def test_forfait_seul_dans_tout_l_historique_reste_a_zero(self):
        """Un joueur qui n'a JAMAIS réellement joué (forfait à chacune de
        ses inscriptions) apparaît quand même dans "players" (participation
        administrative) mais avec tournaments_played = 0 sur toute la
        période — jamais une exclusion complète de la liste."""
        with tempfile.TemporaryDirectory(prefix="stats_always_withdrawn_") as tmp:
            db1 = _new_db(tmp, "N1.tournoi", tournament_date="2026-02-04")
            b1 = db1.add_player("Bob")
            db1.withdraw_player(b1)
            db1.conn.close()

            db2 = _new_db(tmp, "N2.tournoi", tournament_date="2026-02-05")
            b2 = db2.add_player("Bob")
            db2.withdraw_player(b2)
            db2.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            bob = next(p for p in summary["players"] if p["name"] == "Bob")
            self.assertEqual(bob["tournaments_played"], 0)
            self.assertEqual(bob["wins"], 0)
            self.assertIsNone(bob["best_place"])


# =====================================================================
# 3. Cas limites : fichiers illisibles, tri, récursivité.
# =====================================================================
class FichierIllisibleIgnoreProprementTest(unittest.TestCase):
    """CORRECTIF du 2026-09-14 (priorité 1) — l'ancienne version de ce
    test démontrait l'anomalie inverse (voir git history) : une seule
    exception sqlite3, survenant à la PREMIÈRE vraie requête sur un
    fichier corrompu (jamais à l'ouverture de la connexion elle-même,
    que sqlite3 ne valide qu'à la lecture), sortait de build_period_
    summary et faisait échouer la synthèse ENTIÈRE. Désormais, TOUT le
    traitement d'un fichier (ouverture ET première requête, y compris
    au milieu de la boucle sur ses joueurs) est protégé, et un échec sur
    un fichier n'empêche jamais les autres d'être chargés."""

    def test_fichier_corrompu_ignore_les_autres_tournois_charges(self):
        with tempfile.TemporaryDirectory(prefix="stats_corrupt_") as tmp:
            db = _new_db(
                tmp, "valide.tournoi", tournament_date="2026-01-12",
                tournament_name="Valide",
            )
            db.add_player("Alice")
            db.conn.close()

            with open(os.path.join(tmp, "corrompu.tournoi"), "w", encoding="utf-8") as f:
                f.write("ceci n'est pas une base sqlite valide")

            # Ne doit JAMAIS lever : le fichier corrompu est ignoré, le
            # tournoi valide continue d'être chargé normalement.
            summary = database.build_period_summary(tmp, recursive=False)
            self.assertEqual(len(summary["tournaments"]), 1)
            self.assertEqual(summary["tournaments"][0]["name"], "Valide")
            self.assertEqual(summary["tournaments"][0]["entries"], 1)
            self.assertEqual(len(summary["players"]), 1)
            self.assertEqual(summary["players"][0]["name"], "Alice")

    def test_fichier_corrompu_journalise_dans_crash_log_sans_popup(self):
        """"journaliser/identifier le fichier ignoré sans popup bloquante"
        (demande explicite) : build_period_summary n'affiche jamais rien
        à l'écran (fonction pure, aucune dépendance à tkinter) — on
        vérifie ici que le fichier en cause est bien identifié via
        _log_skipped_tournament_file, sans avoir besoin du vrai
        ~/.poker_tournament/crash.log (patché vers un fichier temporaire,
        même précaution que pour roster.py/export_prefs.py ailleurs)."""
        with tempfile.TemporaryDirectory(prefix="stats_corrupt_log_") as tmp:
            corrompu_path = os.path.join(tmp, "corrompu.tournoi")
            with open(corrompu_path, "w", encoding="utf-8") as f:
                f.write("ceci n'est pas une base sqlite valide")

            with patch.object(database, "_log_skipped_tournament_file") as mock_log:
                database.build_period_summary(tmp, recursive=False)
            mock_log.assert_called_once()
            logged_path, logged_exc = mock_log.call_args[0]
            self.assertEqual(logged_path, corrompu_path)
            self.assertIsInstance(logged_exc, Exception)

    def test_fichier_corrompu_reellement_journalise_sur_disque(self):
        """Non-mocké cette fois : vérifie que le message écrit dans le
        journal (crash.log, patché vers un fichier temporaire) identifie
        bien le CHEMIN du fichier fautif — utile pour un TD qui verrait
        un tournoi manquer à l'appel sans autre explication à l'écran."""
        with tempfile.TemporaryDirectory(prefix="stats_corrupt_log2_") as tmp:
            corrompu_path = os.path.join(tmp, "corrompu.tournoi")
            with open(corrompu_path, "w", encoding="utf-8") as f:
                f.write("ceci n'est pas une base sqlite valide")

            log_dir = tempfile.mkdtemp(prefix="stats_corrupt_crashlog_")
            log_path = os.path.join(log_dir, "crash.log")
            with patch.object(database, "_defensive_integrity_log_path", return_value=log_path):
                database.build_period_summary(tmp, recursive=False)

            with open(log_path, encoding="utf-8") as f:
                content = f.read()
            self.assertIn("build_period_summary", content)
            self.assertIn(corrompu_path, content)

    def test_db_fermee_meme_en_cas_d_erreur(self):
        """"fermeture propre de la DB" (demande explicite) : Database.
        close() doit être appelée même quand la première requête sur le
        fichier corrompu échoue — espionne le VRAI Database.close (wraps=,
        donc le comportement réel est conservé) plutôt que de le
        remplacer, pour vérifier l'appel sans changer l'issue du test."""
        with tempfile.TemporaryDirectory(prefix="stats_corrupt_reopen_") as tmp:
            valide_db = _new_db(tmp, "valide.tournoi", tournament_date="2026-01-13")
            valide_db.conn.close()
            corrompu_path = os.path.join(tmp, "corrompu.tournoi")
            with open(corrompu_path, "w", encoding="utf-8") as f:
                f.write("ceci n'est pas une base sqlite valide")

            with patch.object(
                database.Database, "close", autospec=True, side_effect=database.Database.close,
            ) as mock_close:
                summary = database.build_period_summary(tmp, recursive=False)
            # Une fermeture par fichier réellement OUVERT (le corrompu ET
            # le valide) — jamais une connexion oubliée ouverte.
            self.assertEqual(mock_close.call_count, 2)
            self.assertEqual(len(summary["tournaments"]), 1)


class RecursiviteDesSousDossiersTest(unittest.TestCase):
    """"Inclure les sous-dossiers" (recursive_var) : recursive=False ne
    doit voir que le dossier choisi ; recursive=True doit aussi trouver
    un .tournoi rangé dans un sous-dossier."""

    def test_recursive_false_ignore_les_sous_dossiers(self):
        with tempfile.TemporaryDirectory(prefix="stats_recursive_") as tmp:
            top = _new_db(tmp, "racine.tournoi", tournament_date="2026-01-13")
            top.conn.close()
            sub = os.path.join(tmp, "archive")
            os.makedirs(sub)
            nested = _new_db(sub, "nested.tournoi", tournament_date="2026-01-14")
            nested.conn.close()

            summary_flat = database.build_period_summary(tmp, recursive=False)
            self.assertEqual(len(summary_flat["tournaments"]), 1)

            summary_recursive = database.build_period_summary(tmp, recursive=True)
            self.assertEqual(len(summary_recursive["tournaments"]), 2)


class EgaliteDeDatesTriStableTest(unittest.TestCase):
    """Cas limite "égalités" : deux tournois à la MÊME date -> le tri
    (stable, par date uniquement) doit conserver un ordre déterministe
    (celui du tri des chemins de fichiers, sans lever d'exception ni
    mélanger les deux)."""

    def test_meme_date_ordre_stable_par_nom_de_fichier(self):
        with tempfile.TemporaryDirectory(prefix="stats_tie_") as tmp:
            dbA = _new_db(tmp, "A_soir.tournoi", tournament_date="2026-01-20", tournament_name="A")
            dbA.conn.close()
            dbB = _new_db(tmp, "B_soir.tournoi", tournament_date="2026-01-20", tournament_name="B")
            dbB.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            self.assertEqual([t["name"] for t in summary["tournaments"]], ["A", "B"])


# =====================================================================
# 4. CORRECTIF (priorité 3) — "Terminé" aligné sur la règle du Lobby.
# =====================================================================
class StatutTermineAligneAvecLobbyTest(unittest.TestCase):
    """Avant ce correctif, Database.build_period_summary exigeait
    EXACTEMENT 1 joueur actif restant pour dire "Terminé", alors que
    Database.get_live_status() (utilisé par le Lobby) le dit dès qu'il
    en reste <= 1 (0 ou 1) ET qu'il y en a eu plus d'un au total. Un
    tournoi où le dernier joueur restant forfaitait (0 actif) restait
    ainsi "En cours" indéfiniment côté Statistiques alors que le Lobby
    le disait déjà terminé. build_period_summary appelle désormais
    directement get_live_status()["finished"] (voir database.py) —
    aucune définition indépendante recréée.

    Les 3 scénarios de régression explicitement demandés (0, 1, 2
    actifs) sont couverts un par un ci-dessous."""

    def test_zero_actif_restant_est_termine(self):
        """0 actif (tous forfait après une élimination) : "Terminé",
        vainqueur "-" (aucun survivant à désigner, jamais d'IndexError)."""
        with tempfile.TemporaryDirectory(prefix="stats_zero_active_") as tmp:
            db = _new_db(tmp, "zero_actif.tournoi", tournament_date="2026-01-16")
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            db.eliminate_player(b, eliminated_by_id=a)  # Alice seule active
            db.withdraw_player(a)  # ... puis elle forfait aussi -> 0 actif

            self.assertTrue(db.get_live_status()["finished"])  # règle du Lobby
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            t = summary["tournaments"][0]
            self.assertEqual(t["status"], "Terminé")
            self.assertEqual(t["winner"], "-")

    def test_un_actif_restant_est_termine(self):
        """1 actif (cas normal, vainqueur désigné) : "Terminé", vainqueur
        nommé — comportement inchangé par rapport à avant ce correctif."""
        with tempfile.TemporaryDirectory(prefix="stats_one_active_") as tmp:
            db = _new_db(tmp, "un_actif.tournoi", tournament_date="2026-01-17")
            a = db.add_player("Alice")
            b = db.add_player("Bob")
            db.eliminate_player(b, eliminated_by_id=a)  # Alice seule active

            self.assertTrue(db.get_live_status()["finished"])
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            t = summary["tournaments"][0]
            self.assertEqual(t["status"], "Terminé")
            self.assertEqual(t["winner"], "Alice")

    def test_deux_actifs_restants_est_en_cours(self):
        """2 actifs (ou plus) : "En cours", vainqueur "-" — non-régression
        du cas normal le plus courant."""
        with tempfile.TemporaryDirectory(prefix="stats_two_active_") as tmp:
            db = _new_db(tmp, "deux_actifs.tournoi", tournament_date="2026-01-18")
            db.add_player("Alice")
            db.add_player("Bob")
            db.add_player("Chris")

            self.assertFalse(db.get_live_status()["finished"])
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            t = summary["tournaments"][0]
            self.assertEqual(t["status"], "En cours")
            self.assertEqual(t["winner"], "-")

    def test_tournoi_neuf_zero_joueur_reste_en_cours(self):
        """Non-régression explicite : un tournoi sans AUCUN joueur (0 au
        total, pas seulement 0 actif) ne doit jamais devenir "Terminé"
        par accident — get_live_status()["finished"] exige déjà
        total_players_ever > 1, tout comme cette nouvelle implémentation."""
        with tempfile.TemporaryDirectory(prefix="stats_new_tournament_") as tmp:
            db = _new_db(tmp, "vide.tournoi", tournament_date="2026-01-19")
            self.assertFalse(db.get_live_status()["finished"])
            db.conn.close()

            summary = database.build_period_summary(tmp, recursive=False)
            t = summary["tournaments"][0]
            self.assertEqual(t["status"], "En cours")
            self.assertEqual(t["winner"], "-")


# =====================================================================
# 5. Interface : PeriodSummaryDialog (onglet réel, widgets Tk réels).
# =====================================================================
class _StubApp:
    """Reproduit uniquement ce que PeriodSummaryDialog.__init__ lit sur
    `app` (voir son code : getattr(app, "db", None))."""
    db = None


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class PeriodSummaryDialogUiTestCase(unittest.TestCase):
    """Un seul tk.Tk() par CLASSE (voir tests/test_rebalance_phase2_max_
    seats_change.py pour la même précaution) : construit le VRAI
    PeriodSummaryDialog (pas une doublure), sur un tk.Tk() caché comme
    parent (un ttk.Frame n'exige pas un Notebook précis comme master)."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_ui_")
        self.addCleanup(self._tmp.cleanup)

        roster_path = os.path.join(self._tmp.name, "roster.json")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(roster, "_roster_path", return_value=roster_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
        ):
            self.addCleanup(target.stop)
            target.start()

        self.dialog = main.PeriodSummaryDialog(self.root, _StubApp())
        self.addCleanup(self.dialog.destroy)

    def _rows(self, tree, skip_total=True):
        children = list(tree.get_children())
        if skip_total:
            children = children[1:]
        return [tree.item(iid, "values") for iid in children]

    def test_ouverture_onglet_aucune_synthese_generee(self):
        """Ouverture initiale (self.summary is None) : les deux tableaux
        restent vides, aucune exception."""
        self.dialog._refresh_display()
        self.assertEqual(list(self.dialog.tournaments_tree.get_children()), [])
        self.assertEqual(list(self.dialog.players_tree.get_children()), [])

    def test_dossier_invalide_affiche_une_erreur_sans_planter(self):
        self.dialog.folder_var.set(os.path.join(self._tmp.name, "n_existe_pas"))
        with patch.object(main, "messagebox") as mock_mb:
            self.dialog._generate()
            mock_mb.showerror.assert_called_once()
        self.assertIsNone(self.dialog.summary)

    def test_dates_invalides_affiche_une_erreur_sans_planter(self):
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog.date_from_var.set("pas-une-date")
        with patch.object(main, "messagebox") as mock_mb:
            self.dialog._generate()
            mock_mb.showerror.assert_called_once()

    def test_date_debut_apres_date_fin_refusee(self):
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog.date_from_var.set("2026-02-01")
        self.dialog.date_to_var.set("2026-01-01")
        with patch.object(main, "messagebox") as mock_mb:
            self.dialog._generate()
            mock_mb.showerror.assert_called_once()

    def test_generer_sur_dossier_vide_affiche_seulement_le_total_zero(self):
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()
        # Une seule ligne dans chaque tableau : la ligne TOTAL (voir
        # _refresh_display, insérée inconditionnellement) — comportement
        # actuel à vérifier manuellement sur le HP (cf. rapport) : un
        # "TOTAL : 0" affiché même sans la moindre donnée peut prêter à
        # confusion pour l'utilisateur.
        self.assertEqual(len(self.dialog.tournaments_tree.get_children()), 1)
        self.assertEqual(len(self.dialog.players_tree.get_children()), 1)
        self.assertIn("0 tournoi(s)", self.dialog.info_lbl.cget("text"))
        self.assertIn("0 joueur(s)", self.dialog.info_lbl.cget("text"))

    def test_generer_avec_beaucoup_de_tournois_et_joueurs(self):
        """Volume : 15 tournois x 8 joueurs, aucune exception, tous les
        tournois et le bon nombre de joueurs distincts apparaissent."""
        self.dialog.folder_var.set(self._tmp.name)
        for i in range(15):
            db = _new_db(
                self._tmp.name, f"T{i:02d}.tournoi",
                tournament_date=f"2026-03-{i + 1:02d}", tournament_name=f"Soir {i}",
            )
            for j in range(8):
                db.add_player(f"Joueur{j}")
            db.conn.close()
        self.dialog._generate()
        self.assertEqual(len(self._rows(self.dialog.tournaments_tree)), 15)
        self.assertEqual(len(self._rows(self.dialog.players_tree)), 8)
        self.assertIn("15 tournoi(s)", self.dialog.info_lbl.cget("text"))
        self.assertIn("8 joueur(s)", self.dialog.info_lbl.cget("text"))

    def test_libelles_et_valeurs_affiches_coherents(self):
        """Cohérence des libellés/valeurs affichés (demande explicite du
        rapport) : compare chaque cellule affichée à la valeur attendue,
        calculée indépendamment — pas seulement "une ligne existe"."""
        db = _new_db(
            self._tmp.name, "coherence.tournoi", tournament_date="2026-04-01",
            tournament_name="Cohérence", bounty_amount=10, ranking_formula="none",
        )
        a = db.add_player("Alice")
        b = db.add_player("Bob")
        db.eliminate_player(b, eliminated_by_id=a)
        db.conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()

        # Un ttk.Treeview restitue toujours ses "values" sous forme de
        # chaînes (round-trip Tcl/Tk), même pour des colonnes numériques
        # insérées avec de vrais int (voir _refresh_display) : comparées
        # ici à des chaînes, pas à des int.
        t_rows = self._rows(self.dialog.tournaments_tree)
        self.assertEqual(len(t_rows), 1)
        date_disp, name_disp, status_disp, entries_disp, winner_disp, bounty_disp = t_rows[0]
        self.assertEqual(date_disp, "01/04/2026")  # format_date_fr
        self.assertEqual(name_disp, "Cohérence")
        self.assertEqual(status_disp, "Terminé")
        self.assertEqual(entries_disp, "2")
        self.assertEqual(winner_disp, "Alice")
        self.assertEqual(bounty_disp, "10")  # 1 kill x 10 (valeur fixe)

        p_rows = self._rows(self.dialog.players_tree)
        alice_row = next(r for r in p_rows if r[1] == "Alice")
        _club, _name, played, wins, best, bounty_won, total_pts = alice_row
        self.assertEqual(played, "1")
        self.assertEqual(wins, "1")
        self.assertEqual(best, "1")
        self.assertEqual(bounty_won, "10")
        self.assertEqual(total_pts, "10")  # ranking_formula "none" -> seul le bounty compte

    def test_filtre_club_change_affichage_sans_replanter_les_fichiers(self):
        """Changer le filtre Club ne doit que re-filtrer l'affichage
        (jamais rouvrir les fichiers .tournoi) : on le vérifie en
        patchant build_period_summary pour qu'il lève si jamais il était
        appelé une seconde fois après le filtrage.

        Important (recensement) : le filtre "Club" lit le club ACTUEL du
        joueur dans le répertoire GLOBAL (roster.get_club, ~/.poker_
        tournament/roster.json) — jamais le club éventuellement enregistré
        DANS le fichier .tournoi (players.club, réglé par add_player) au
        moment de l'inscription. D'où roster.set_club ici plutôt que
        add_player(..., club=...), qui n'a aucune influence sur ce filtre."""
        # roster.set_club AVANT la construction du dialogue : la liste des
        # clubs proposés dans le filtre est figée une seule fois, à la
        # construction du widget (_populate_club_filter_listbox, appelée
        # depuis build_club_filter_widget) — voir aussi le test dédié
        # ClubFilterListboxJamaisRafraichieTest plus bas, qui démontre que
        # l'ordre inverse (répertoire modifié APRÈS coup) ne fonctionne pas.
        roster.set_club("Alice", "Chemillé")
        roster.set_club("Bob", "Angers")
        self.dialog.destroy()
        self.dialog = main.PeriodSummaryDialog(self.root, _StubApp())

        db = _new_db(self._tmp.name, "clubs.tournoi", tournament_date="2026-04-02")
        db.add_player("Alice")
        db.add_player("Bob")
        db.conn.close()

        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()
        self.assertEqual(len(self._rows(self.dialog.players_tree)), 2)

        listbox = self.dialog.stats_club_listbox
        items = list(listbox.get(0, "end"))
        listbox.selection_clear(0, "end")
        listbox.selection_set(items.index("Chemillé"))

        with patch.object(main, "build_period_summary") as mock_build:
            self.dialog._refresh_display()
            mock_build.assert_not_called()

        filtered_rows = self._rows(self.dialog.players_tree)
        self.assertEqual([r[1] for r in filtered_rows], ["Alice"])

    def test_liste_des_clubs_du_filtre_jamais_rafraichie_apres_coup(self):
        """Recensement (comportement actuel, pas un correctif) : la liste
        déroulante du filtre "Club" est peuplée UNE SEULE FOIS, à la
        construction de l'onglet (_populate_club_filter_listbox, appelée
        depuis build_club_filter_widget) — jamais republiée ensuite.
        Un club ajouté au répertoire APRÈS l'ouverture de l'onglet
        (ex : depuis "Répertoire" pendant que Statistiques est déjà
        ouvert) n'apparaît donc PAS dans le filtre tant que l'application
        n'a pas été relancée, même après avoir cliqué "Générer la
        synthèse" à nouveau."""
        # self.dialog (construit dans setUp, AVANT ce club) sert
        # volontairement de base ici : aucune reconstruction.
        self.dialog.folder_var.set(self._tmp.name)  # dossier valide (vide), sinon showerror bloquant
        roster.set_club("Alice", "ToutNouveauClub")
        self.dialog._generate()  # même un nouveau "Générer" ne republie pas la liste
        items = list(self.dialog.stats_club_listbox.get(0, "end"))
        self.assertNotIn("ToutNouveauClub", items)

    def test_export_sans_synthese_generee_message_info(self):
        with patch.object(main, "messagebox") as mock_mb:
            self.dialog._open_export_dialog()
            mock_mb.showinfo.assert_called_once()

    def test_export_synthese_vide_message_info(self):
        self.dialog.folder_var.set(self._tmp.name)  # dossier vide
        self.dialog._generate()
        with patch.object(main, "messagebox") as mock_mb:
            self.dialog._open_export_dialog()
            mock_mb.showinfo.assert_called_once()


# =====================================================================
# 6. CORRECTIF (priorité 2) — l'export reflète désormais le filtre Club
#    affiché à l'écran, sans changer le fonctionnement du filtre lui-même.
# =====================================================================
@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible dans cet environnement")
class ClubFilterExportConsistencyTest(unittest.TestCase):
    """Avant ce correctif, PeriodSummaryDialog._open_export_dialog()
    transmettait `self.summary` intact (jamais filtré) à PeriodExport
    Dialog, quel que soit le filtre "Club" affiché à l'écran par
    _refresh_display(). _open_export_dialog utilise désormais
    _club_filtered_players (la MÊME méthode que _refresh_display,
    jamais une deuxième logique de filtre) pour ne transmettre à
    l'export que les joueurs actuellement affichés. `self.summary`
    lui-même (la donnée BRUTE, non filtrée) n'est jamais modifié : seul
    ce qui est transmis à PeriodExportDialog change. Le filtre à
    l'écran (_refresh_display, build_club_filter_widget, get_selected_
    clubs...) est intégralement inchangé."""

    @classmethod
    def setUpClass(cls):
        cls.root = tk.Tk()
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="stats_export_fix_")
        self.addCleanup(self._tmp.cleanup)
        roster_path = os.path.join(self._tmp.name, "roster.json")
        prefs_path = os.path.join(self._tmp.name, "export_prefs.json")
        for target in (
            patch.object(roster, "_roster_path", return_value=roster_path),
            patch.object(export_prefs, "_prefs_path", return_value=prefs_path),
        ):
            self.addCleanup(target.stop)
            target.start()

        roster.set_club("Alice", "Chemillé")
        roster.set_club("Bob", "Angers")
        db = _new_db(self._tmp.name, "clubs.tournoi", tournament_date="2026-04-03")
        db.add_player("Alice")
        db.add_player("Bob")
        db.conn.close()

        self.dialog = main.PeriodSummaryDialog(self.root, _StubApp())
        self.addCleanup(self.dialog.destroy)
        self.dialog.folder_var.set(self._tmp.name)
        self.dialog._generate()

    def _select_club(self, club_name):
        listbox = self.dialog.stats_club_listbox
        items = list(listbox.get(0, "end"))
        listbox.selection_clear(0, "end")
        listbox.selection_set(items.index(club_name))
        self.dialog._refresh_display()

    def _displayed_player_names(self):
        return {
            self.dialog.players_tree.item(iid, "values")[1]
            for iid in list(self.dialog.players_tree.get_children())[1:]  # saute TOTAL
        }

    def _exported_player_names(self):
        """Capture ce que _open_export_dialog transmet RÉELLEMENT à
        PeriodExportDialog (donc, en aval, à export_period_summary_csv|
        xlsx|pdf) — jamais dialog.summary directement, qui reste
        volontairement non filtré (voir la docstring de la classe)."""
        with patch.object(main, "PeriodExportDialog") as mock_dialog_cls:
            self.dialog._open_export_dialog()
        mock_dialog_cls.assert_called_once()
        export_summary = mock_dialog_cls.call_args.args[1]
        return {a["name"] for a in export_summary["players"]}, export_summary

    def test_avec_filtre_club_export_contient_les_memes_joueurs_que_l_ecran(self):
        self._select_club("Chemillé")
        displayed_names = self._displayed_player_names()
        self.assertEqual(displayed_names, {"Alice"})

        exported_names, _ = self._exported_player_names()
        self.assertEqual(exported_names, displayed_names)
        self.assertEqual(exported_names, {"Alice"})

    def test_sans_filtre_club_export_complet_inchange(self):
        # "Tous" (index 0) reste sélectionné par défaut après _generate
        # (voir _populate_club_filter_listbox) : aucun filtre actif.
        displayed_names = self._displayed_player_names()
        self.assertEqual(displayed_names, {"Alice", "Bob"})

        exported_names, _ = self._exported_player_names()
        self.assertEqual(exported_names, {"Alice", "Bob"})
        self.assertEqual(exported_names, displayed_names)

    def test_tournois_jamais_filtres_par_club_ni_a_l_ecran_ni_a_l_export(self):
        """Le filtre "Club" ne porte QUE sur le classement des joueurs
        (voir _refresh_display) — la liste des tournois de la période
        reste, elle, entièrement inchangée par le filtre, à l'écran
        comme à l'export : non-régression explicite de ce correctif."""
        self._select_club("Chemillé")
        _, export_summary = self._exported_player_names()
        self.assertEqual(len(export_summary["tournaments"]), 1)
        self.assertIs(export_summary["tournaments"], self.dialog.summary["tournaments"])

    def test_summary_brut_jamais_modifie_par_l_export(self):
        """dialog.summary (la donnée source, non filtrée) ne doit jamais
        être altéré par un appel à _open_export_dialog — un "Générer" ou
        un changement de filtre ultérieur ne doit jamais hériter d'un
        état laissé par un export précédent."""
        self._select_club("Chemillé")
        self._exported_player_names()
        self.assertEqual(
            {a["name"] for a in self.dialog.summary["players"]}, {"Alice", "Bob"}
        )


if __name__ == "__main__":
    unittest.main()
