# -*- coding: utf-8 -*-
"""Tests du système de points distribués (classement, demande du
2026-09-10, étendu le 2026-09-20 avec "Tournois CPC") — remplace l'ancien
champ "Montant de la prime de classement" (ranking_bonus_points) par une
liste déroulante à 5 choix : Aucun, Classique, Progressive, Tournois CPC,
Sit & Go CPC (réglage ranking_formula, propre à chaque tournoi).

"Tournois CPC" (règle définitive du club, formule et valeurs de contrôle
fournies le 2026-09-20) : P(r,N) = 50 + 950×N × [0,12×0,88^(r-1)] /
[1 − 0,88^N], appliquée SANS EXCEPTION quel que soit N (y compris hors du
barème documenté 15-45 joueurs). Arrondi par la méthode des PLUS GRANDS
RESTES (jamais un round() indépendant par place) : partie entière de
chaque valeur théorique, puis distribution des points manquants pour
atteindre EXACTEMENT N×1000 aux places ayant les plus grands restes
décimaux, départagées par la place r la plus petite en cas d'égalité
stricte — voir database.ranking_points_table pour l'implémentation
(fractions.Fraction, exactitude totale) et sa docstring pour la preuve
algébrique que la somme théorique vaut toujours exactement N×1000.

RÈGLE DE COMPATIBILITÉ (validée explicitement par l'utilisateur avant
codage — voir database.py: Database.resolve_ranking_formula) :
- ranking_formula présent -> utilisé tel quel (nouveau tournoi, stampé
  "none" à la création, ou déjà configuré) ;
- ranking_formula absent + ranking_bonus_points > 0 -> valeur fixe
  historique préservée EXACTEMENT, indéfiniment ;
- ranking_formula absent + ranking_bonus_points absent ou à 0 -> résolu
  à "current" (Classique), PAS "none" : c'était déjà, de tout temps, le
  comportement réel du code (0 est faux en Python, la formule
  s'appliquait déjà) — ne jamais changer rétroactivement les résultats
  déjà affichés/exportés d'un ancien tournoi.

Ne touche ni aux bounties, ni aux primes de présence/assiduité, ni au
système de tables/rééquilibrage/guidage BB (hors sujet, non modifiés).

Utilise une vraie Database SQLite en mémoire/fichier temporaire (pas de
doublure)."""
import os
import sys
import tempfile
import unittest
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


def _new_db(tmp_dir, settings=None):
    db = database.Database(os.path.join(tmp_dir, "A.tournoi"))
    if settings:
        db.set_settings(settings)
    return db


def _eliminate_all_and_get_ranking(db, names):
    """Inscrit `names` (dans l'ordre), les élimine tous SAUF le dernier
    dans cet ordre (donc le dernier reste actif -> vainqueur, place 1),
    pour obtenir un classement complet 1..N sans dépendre du guidage
    grosse blinde ni du système de tables (hors sujet ici)."""
    ids = [db.add_player(n) for n in names]
    # Élimine du premier au avant-dernier : place N pour le 1er éliminé,
    # ..., place 2 pour l'avant-dernier -- laisse le dernier actif
    # (place 1, vainqueur).
    for pid in ids[:-1]:
        db.eliminate_player(pid)
    return {r["name"]: r for r in db.get_ranking_bonuses()}


class RankingPointsFormulaFunctionTest(unittest.TestCase):
    """database.ranking_points() — fonction pure, sans DB."""

    def test_none_toujours_zero(self):
        for place in range(1, 10):
            self.assertEqual(database.ranking_points(place, 27, "none"), 0)

    def test_current_formule_classique(self):
        # 100*sqrt(27)/1 ~ 519.6 -> round 520 ; /3 ~ 173.2 -> round 173.
        self.assertEqual(database.ranking_points(1, 27, "current"), round(100 * (27 ** 0.5) / 1))
        self.assertEqual(database.ranking_points(3, 27, "current"), round(100 * (27 ** 0.5) / 3))

    def test_progressive_formule(self):
        self.assertEqual(
            database.ranking_points(4, 20, "progressive"),
            round(100 * (20 ** 0.5) / (4 ** 0.5)),
        )

    def test_rang_1_current_et_progressive_identiques(self):
        # P=1 : sqrt(1)=1 des deux côtés -> même valeur pour le vainqueur.
        for n in (9, 20, 36):
            self.assertEqual(
                database.ranking_points(1, n, "current"),
                database.ranking_points(1, n, "progressive"),
            )

    def test_progressive_ecart_plus_faible_que_classique(self):
        # L'écart entre le rang 1 et un rang moyen doit être plus faible
        # en Progressive qu'en Classique (demande explicite de la formule).
        n = 20
        ecart_classique = database.ranking_points(1, n, "current") - database.ranking_points(5, n, "current")
        ecart_progressif = database.ranking_points(1, n, "progressive") - database.ranking_points(5, n, "progressive")
        self.assertLess(ecart_progressif, ecart_classique)

    def test_sitngo_cpc_valeurs_exactes_n5_a_n9(self):
        expected = {
            5: [1400, 1200, 1000, 800, 600],
            6: [1500, 1300, 1100, 900, 700, 500],
            7: [1600, 1400, 1200, 1000, 800, 600, 400],
            8: [1700, 1500, 1300, 1100, 900, 700, 500, 300],
            9: [1800, 1600, 1400, 1200, 1000, 800, 600, 400, 200],
        }
        for n, values in expected.items():
            computed = [database.ranking_points(p, n, "sitngo_cpc") for p in range(1, n + 1)]
            self.assertEqual(computed, values, f"N={n}")

    def test_sitngo_cpc_somme_egale_n_fois_1000(self):
        for n in range(5, 10):
            total = sum(database.ranking_points(p, n, "sitngo_cpc") for p in range(1, n + 1))
            self.assertEqual(total, n * 1000, f"N={n}")

    def test_place_ou_n_invalides_renvoient_zero_quelle_que_soit_la_formule(self):
        for formula in ("none", "current", "progressive", "tournois_cpc", "sitngo_cpc"):
            self.assertEqual(database.ranking_points(0, 10, formula), 0)
            self.assertEqual(database.ranking_points(None, 10, formula), 0)
            self.assertEqual(database.ranking_points(1, 0, formula), 0)


class RankingPointsTournoisCpcTest(unittest.TestCase):
    """"Tournois CPC" — règle définitive du club (2026-09-20) : formule
    P(r,N) = 50 + 950×N × [0,12×0,88^(r-1)] / [1 − 0,88^N], arrondie par
    la méthode des plus grands restes. Tests obligatoires demandés
    explicitement : somme exacte, N<15, 15<=N<=45, N>45, départage
    déterministe, valeurs de contrôle du document, non-régression des
    formules existantes."""

    def test_valeurs_de_controle_du_document(self):
        # Document fourni par le club : vainqueur (place 1) pour
        # N=15/30/45 — vérifié par calcul indépendant (fractions.Fraction)
        # avant codage, voir l'échange du 2026-09-20.
        self.assertEqual(database.ranking_points(1, 15, "tournois_cpc"), 2055)
        self.assertEqual(database.ranking_points(1, 30, "tournois_cpc"), 3545)
        self.assertEqual(database.ranking_points(1, 45, "tournois_cpc"), 5196)

    def test_somme_exacte_egale_a_n_fois_1000(self):
        # Garantie obligatoire explicite du club : la somme distribuée
        # doit TOUJOURS valoir exactement N×1000, quel que soit N.
        for n in (1, 2, 3, 5, 10, 14, 15, 16, 30, 45, 46, 60, 100, 200):
            total = sum(database.ranking_points(p, n, "tournois_cpc") for p in range(1, n + 1))
            self.assertEqual(total, n * 1000, f"N={n}")

    def test_n_inferieur_a_15_meme_formule_sans_blocage(self):
        # Confirmation explicite du club : aucune limitation, aucun
        # blocage, aucun changement de formule hors de la plage 15-45.
        table_14 = database.ranking_points_table(14, "tournois_cpc")
        self.assertEqual(table_14[0], 1966)  # vainqueur, calculé indépendamment
        self.assertEqual(sum(table_14), 14000)
        self.assertEqual(len(table_14), 14)

    def test_n_entre_15_et_45(self):
        for n, winner in ((15, 2055), (30, 3545), (45, 5196)):
            table = database.ranking_points_table(n, "tournois_cpc")
            self.assertEqual(table[0], winner, f"N={n}")
            self.assertEqual(sum(table), n * 1000, f"N={n}")

    def test_n_superieur_a_45_meme_formule_sans_blocage(self):
        table_46 = database.ranking_points_table(46, "tournois_cpc")
        self.assertEqual(table_46[0], 5309)  # vainqueur, calculé indépendamment
        self.assertEqual(sum(table_46), 46000)
        self.assertEqual(len(table_46), 46)

    def test_suite_strictement_decroissante(self):
        # Une place moins bonne ne doit jamais rapporter plus de points
        # qu'une meilleure place, quel que soit N.
        for n in (1, 14, 15, 30, 45, 46, 100):
            table = database.ranking_points_table(n, "tournois_cpc")
            for i in range(len(table) - 1):
                self.assertGreaterEqual(table[i], table[i + 1], f"N={n}, r={i+1}")

    def test_aucune_egalite_reelle_de_reste_dans_la_formule(self):
        """Vérifie, par calcul exact (fractions.Fraction, aucune
        imprécision possible), qu'AUCUNE paire de places de 1 à 300
        joueurs n'a jamais un reste décimal rigoureusement identique
        dans cette formule géométrique — la règle de départage "r le
        plus petit gagne" (voir test_departage_deterministe_regle_du_
        club ci-dessous) est donc une garantie DÉFENSIVE qui ne se
        déclenche jamais en pratique avec CETTE formule précise, mais
        doit rester correcte si jamais un cas limite apparaissait
        (implémentation générique, pas spécifique à cette absence
        d'égalité constatée)."""
        q = Fraction(88, 100)
        for n in (14, 15, 30, 45, 46, 100, 200):
            one_minus_q = 1 - q
            denom = 1 - q ** n
            raw = [
                50 + Fraction(950 * n) * (one_minus_q * q ** (r - 1)) / denom
                for r in range(1, n + 1)
            ]
            remainders = [v - int(v) for v in raw]
            self.assertEqual(len(remainders), len(set(remainders)), f"N={n}")

    def test_departage_deterministe_regle_du_club(self):
        """Règle explicite du club : "en cas d'égalité exacte de reste,
        utiliser la meilleure place (r le plus petit) comme départage
        déterministe." La formule réelle ne produit jamais d'égalité
        exacte (voir test ci-dessus) — ce test vérifie donc directement
        l'ALGORITHME de départage utilisé par ranking_points_table
        (tri par reste décroissant, puis par index croissant :
        `sorted(range(n), key=lambda i: (-remainders[i], i))`, voir sa
        docstring/implémentation dans database.py), sur un jeu de
        restes SYNTHÉTIQUE construit avec une égalité délibérée entre
        les places 3 et 5 — reproduit exactement le même algorithme que
        la production, pas la formule elle-même."""
        remainders = [
            Fraction(9, 10),   # place 1 (r=1, index 0)
            Fraction(1, 10),   # place 2
            Fraction(5, 10),   # place 3 -- égalité exacte avec la place 5
            Fraction(2, 10),   # place 4
            Fraction(5, 10),   # place 5 -- même reste que la place 3
        ]
        # missing=2 : juste assez pour la place 1 (0.9, la meilleure) ET
        # UNE SEULE des deux places à égalité (0.5 chacune) -- c'est
        # précisément ce qui force le départage à jouer un rôle : avec
        # missing=3 les deux places à égalité passeraient toutes les
        # deux, sans jamais exercer la règle de départage elle-même.
        missing = 2
        order = sorted(range(len(remainders)), key=lambda i: (-remainders[i], i))
        bonus_places = sorted(i + 1 for i in order[:missing])  # 1-indexé, pour lisibilité
        # Plus grands restes : place 1 (0.9) d'abord, puis l'égalité
        # places 3/5 (0.5 chacune) départagée par r le plus petit ->
        # place 3 choisie, place 5 exclue (un seul emplacement restant).
        self.assertEqual(bonus_places, [1, 3])

    def test_reproductibilite_stricte(self):
        """Deux appels indépendants avec les mêmes arguments donnent
        TOUJOURS exactement le même résultat, place par place —
        condition nécessaire à un départage déterministe (un tri
        instable romprait une égalité de façon aléatoire d'un appel à
        l'autre)."""
        for n in (14, 15, 30, 45, 46):
            first = database.ranking_points_table(n, "tournois_cpc")
            second = database.ranking_points_table(n, "tournois_cpc")
            self.assertEqual(first, second, f"N={n}")

    def test_ranking_points_et_ranking_points_table_coherents(self):
        """Les deux chemins d'accès (ranking_points par place isolée, et
        ranking_points_table pour tout le barème) doivent TOUJOURS
        renvoyer exactement les mêmes valeurs — cohérence entre les deux
        chemins d'accès, demande explicite du club."""
        for n in (1, 14, 15, 30, 45, 46, 60):
            table = database.ranking_points_table(n, "tournois_cpc")
            for r in range(1, n + 1):
                self.assertEqual(
                    database.ranking_points(r, n, "tournois_cpc"), table[r - 1],
                    f"N={n}, r={r}",
                )

    def test_place_hors_bornes_renvoie_zero(self):
        self.assertEqual(database.ranking_points(16, 15, "tournois_cpc"), 0)

    def test_non_regression_formules_existantes_inchangees(self):
        """L'ajout de "tournois_cpc" ne doit rien changer aux 4 formules
        existantes — mêmes valeurs qu'avant ce chantier."""
        self.assertEqual(database.ranking_points(1, 27, "current"), round(100 * (27 ** 0.5) / 1))
        self.assertEqual(
            database.ranking_points(4, 20, "progressive"),
            round(100 * (20 ** 0.5) / (4 ** 0.5)),
        )
        self.assertEqual(database.ranking_points(1, 7, "sitngo_cpc"), 1600)
        self.assertEqual(database.ranking_points(7, 7, "sitngo_cpc"), 400)
        self.assertEqual(database.ranking_points(3, 10, "none"), 0)
        self.assertEqual(
            database.ranking_points_table(7, "sitngo_cpc"),
            [1600, 1400, 1200, 1000, 800, 600, 400],
        )

    def test_ordre_des_libelles_dans_ranking_formula_labels(self):
        self.assertEqual(
            list(database.RANKING_FORMULA_LABELS.values()),
            ["Aucun", "Classique", "Progressive", "Tournois CPC", "Sit & Go CPC"],
        )


class ResolveRankingFormulaCompatibiliteTest(unittest.TestCase):
    """Database.resolve_ranking_formula() — règle de compatibilité,
    validée explicitement avant codage."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_formula_test_")
        self.addCleanup(self._tmp.cleanup)

    def test_ranking_formula_absent_et_ranking_bonus_points_absent(self):
        db = _new_db(self._tmp.name)
        # Simule un TRÈS ancien fichier : retire même la ligne
        # ranking_formula stampée par défaut par certains chemins de
        # création — ici, Database() seule ne la stampe jamais (seul
        # App._choose_tournament_file le fait pour un nouveau tournoi),
        # donc ce cas est déjà couvert nativement.
        db.conn.execute("DELETE FROM settings WHERE key IN ('ranking_formula','ranking_bonus_points')")
        db.conn.commit()
        formula, legacy = db.resolve_ranking_formula()
        self.assertEqual(formula, "current")
        self.assertIsNone(legacy)

    def test_ranking_formula_absent_et_ranking_bonus_points_zero(self):
        db = _new_db(self._tmp.name, {"ranking_bonus_points": 0})
        db.conn.execute("DELETE FROM settings WHERE key='ranking_formula'")
        db.conn.commit()
        formula, legacy = db.resolve_ranking_formula()
        self.assertEqual(formula, "current")
        self.assertIsNone(legacy)

    def test_ranking_formula_absent_et_ranking_bonus_points_positif(self):
        db = _new_db(self._tmp.name, {"ranking_bonus_points": 42})
        db.conn.execute("DELETE FROM settings WHERE key='ranking_formula'")
        db.conn.commit()
        formula, legacy = db.resolve_ranking_formula()
        self.assertIsNotNone(legacy)
        self.assertEqual(legacy, 42)

    def test_ranking_formula_present_prime_sur_ranking_bonus_points(self):
        # Un tournoi déjà migré (ranking_formula explicitement enregistré)
        # ignore totalement un ranking_bonus_points résiduel, quel qu'il
        # soit.
        db = _new_db(self._tmp.name, {"ranking_bonus_points": 999, "ranking_formula": "progressive"})
        formula, legacy = db.resolve_ranking_formula()
        self.assertEqual(formula, "progressive")
        self.assertIsNone(legacy)

    def test_ranking_formula_none_explicite(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "none"})
        formula, legacy = db.resolve_ranking_formula()
        self.assertEqual(formula, "none")
        self.assertIsNone(legacy)


class GetRankingBonusesIntegrationTest(unittest.TestCase):
    """get_ranking_bonuses() bout en bout, pour chaque formule — vérifie
    aussi que get_primes_summary() (donc les exports/onglet Primes) en
    hérite correctement."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_formula_integ_")
        self.addCleanup(self._tmp.cleanup)

    def test_aucun_aucun_point_de_classement(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "none", "bounty_amount": 0})
        ranking = _eliminate_all_and_get_ranking(db, ["A", "B", "C", "D", "E"])
        for r in ranking.values():
            self.assertEqual(r["valeur"], 0)
            self.assertEqual(r["montant"], 0)
        # get_primes_summary hérite bien de la même source.
        summary = {r["name"]: r for r in db.get_primes_summary()}
        for name in ranking:
            self.assertEqual(summary[name]["cl_montant"], 0)

    def test_classique_valeurs_calculees(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "current"})
        names = ["A", "B", "C", "D", "E"]
        ranking = _eliminate_all_and_get_ranking(db, names)
        n = len(names)
        # Éliminés dans l'ordre A,B,C,D (voir _eliminate_all_and_get_
        # ranking) : A part le premier (5 actifs -> place 5), ..., D part
        # en dernier avant le vainqueur (2 actifs -> place 2).
        for name, place in zip(names[:-1], range(n, 1, -1)):
            self.assertEqual(ranking[name]["valeur"], database.ranking_points(place, n, "current"))
        self.assertEqual(ranking[names[-1]]["valeur"], database.ranking_points(1, n, "current"))

    def test_progressive_valeurs_calculees(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "progressive"})
        names = ["A", "B", "C", "D", "E"]
        ranking = _eliminate_all_and_get_ranking(db, names)
        n = len(names)
        self.assertEqual(ranking[names[-1]]["valeur"], database.ranking_points(1, n, "progressive"))

    def test_sitngo_cpc_valeurs_calculees_et_somme(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "sitngo_cpc"})
        names = [f"P{i}" for i in range(7)]
        ranking = _eliminate_all_and_get_ranking(db, names)
        self.assertEqual(sum(r["valeur"] for r in ranking.values()), 7000)
        self.assertEqual(ranking[names[-1]]["valeur"], 1600)  # vainqueur, N=7

    def test_tournois_cpc_valeurs_calculees_et_somme(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "tournois_cpc"})
        names = [f"P{i}" for i in range(15)]
        ranking = _eliminate_all_and_get_ranking(db, names)
        self.assertEqual(sum(r["valeur"] for r in ranking.values()), 15000)
        self.assertEqual(ranking[names[-1]]["valeur"], 2055)  # vainqueur, N=15
        # get_primes_summary hérite bien de la même source (même
        # vérification que pour les 4 formules existantes).
        summary = {r["name"]: r for r in db.get_primes_summary()}
        self.assertEqual(summary[names[-1]]["cl_montant"], 2055)

    def test_ancien_ranking_bonus_points_positif_preserve_dans_get_ranking_bonuses(self):
        db = _new_db(self._tmp.name, {"ranking_bonus_points": 55})
        db.conn.execute("DELETE FROM settings WHERE key='ranking_formula'")
        db.conn.commit()
        names = ["A", "B", "C"]
        ranking = _eliminate_all_and_get_ranking(db, names)
        for r in ranking.values():
            self.assertEqual(r["valeur"], 55)

    def test_ancien_ranking_bonus_points_positif_survit_a_fermeture_et_reouverture(self):
        """Vérification explicite demandée le 2026-09-11 : PAS seulement
        dans la même connexion (test ci-dessus) — un ancien fichier
        fermé puis rouvert (nouvelle connexion Database, comme le ferait
        vraiment l'application) doit renvoyer EXACTEMENT la même valeur
        fixe pour chaque joueur classé, avant et après."""
        path = os.path.join(self._tmp.name, "Legacy.tournoi")
        db1 = database.Database(path)
        db1.set_settings({"ranking_bonus_points": 37})
        db1.conn.execute("DELETE FROM settings WHERE key='ranking_formula'")
        db1.conn.commit()
        ranking_before = _eliminate_all_and_get_ranking(db1, ["A", "B", "C", "D"])
        db1.conn.close()

        db2 = database.Database(path)  # nouvelle connexion, vraie réouverture
        ranking_after = {r["name"]: r for r in db2.get_ranking_bonuses()}
        db2.conn.close()

        self.assertEqual(
            {n: r["valeur"] for n, r in ranking_before.items()},
            {n: r["valeur"] for n, r in ranking_after.items()},
        )
        for r in ranking_after.values():
            self.assertEqual(r["valeur"], 37)

    def test_ancien_absent_ou_zero_preserve_formule_classique_dans_get_ranking_bonuses(self):
        """LE test central de non-régression : un ancien tournoi qui
        n'a jamais touché ranking_bonus_points (absent ou 0) doit
        continuer à afficher EXACTEMENT les mêmes points qu'avant ce
        changement (formule Classique), jamais 0."""
        db = _new_db(self._tmp.name)
        db.conn.execute("DELETE FROM settings WHERE key IN ('ranking_formula','ranking_bonus_points')")
        db.conn.commit()
        names = ["A", "B", "C", "D", "E", "F", "G"]
        ranking = _eliminate_all_and_get_ranking(db, names)
        n = len(names)
        self.assertEqual(ranking[names[-1]]["valeur"], database.ranking_points(1, n, "current"))
        self.assertNotEqual(ranking[names[-1]]["valeur"], 0)


class SauvegardeReouvertureTest(unittest.TestCase):
    """Persistance du choix par tournoi (jamais dans PERSISTED_KEYS)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_formula_persist_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "A.tournoi")

    def test_chaque_choix_survit_a_une_fermeture_reouverture(self):
        for formula in ("none", "current", "progressive", "tournois_cpc", "sitngo_cpc"):
            db = database.Database(self.path)
            db.set_settings({"ranking_formula": formula})
            db.conn.close()
            db2 = database.Database(self.path)
            resolved, legacy = db2.resolve_ranking_formula()
            self.assertEqual(resolved, formula)
            self.assertIsNone(legacy)
            db2.conn.close()

    def test_nouveau_tournoi_defaut_aucun(self):
        """Un nouveau tournoi (jamais ouvert Paramètres) n'a PAS
        ranking_formula stampé par Database() seule (ce n'est pas dans
        DEFAULT_SETTINGS, volontairement — voir sa docstring) : c'est
        App._choose_tournament_file qui le stampe à la création. Ici on
        vérifie directement le comportement attendu de ce stamp."""
        db = database.Database(self.path)
        db.set_settings({"ranking_formula": "none"})  # simule le stamp de création
        formula, legacy = db.resolve_ranking_formula()
        self.assertEqual(formula, "none")
        self.assertIsNone(legacy)
        db.conn.close()

    def test_ranking_formula_absent_de_default_settings(self):
        """Vérifie explicitement que Database() seule (sans le stamp de
        App._choose_tournament_file) ne crée JAMAIS de ligne
        ranking_formula — condition nécessaire pour distinguer un
        nouveau tournoi (stampé ailleurs) d'un ancien fichier."""
        db = database.Database(self.path)
        row = db.conn.execute("SELECT value FROM settings WHERE key='ranking_formula'").fetchone()
        self.assertIsNone(row)
        db.conn.close()


class NonRegressionAutresPrimesTest(unittest.TestCase):
    """Ce changement ne doit rien modifier aux bounties, présence,
    assiduité, ni au système de tables/rééquilibrage/guidage BB."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="ranking_formula_nonregression_")
        self.addCleanup(self._tmp.cleanup)

    def test_bounty_inchange_quelle_que_soit_la_formule_de_classement(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "sitngo_cpc", "bounty_amount": 50})
        ids = [db.add_player(n) for n in ["A", "B", "C"]]
        db.eliminate_player(ids[0], eliminated_by_id=ids[1])
        bounties = {r["name"]: r for r in db.get_bounty_bonuses()}
        self.assertEqual(bounties["B"]["montant"], 50)  # bounty_amount fixe, inchangé

    def test_get_primes_summary_total_inclut_toujours_les_4_composantes(self):
        db = _new_db(self._tmp.name, {"ranking_formula": "current", "attendance_bonus_points": 10})
        db.add_player("A")
        db.add_player("B")
        summary = db.get_primes_summary()
        self.assertTrue(summary)
        for row in summary:
            self.assertEqual(
                row["total"],
                row["presence"] + row["assiduite"] + row["cl_montant"] + row["bo_montant"],
            )


if __name__ == "__main__":
    unittest.main()
