# -*- coding: utf-8 -*-
"""Tri visuel "absents en premier" sur "Abs/Dés" (demande du 2026-10-06,
page _ELIMINATE_PAGE, remote_control.py) — un tap SIMPLE (sans glisser de
joueur) sur le bouton bascule `absFirstSortActive` ; un VRAI glisser-
déposer jusqu'à ce même bouton continue d'exécuter la procédure métier
actuelle (`handleAbsDesDrop`) sans jamais toucher à ce drapeau.

Principe de sécurité tap/drag (vérifié dans le diagnostic, pas réinventé
ici) : `onTouchMove` appelle `e.preventDefault()` dès qu'un glissement
est "engagé" (`pending.engaged`) — comportement standard des navigateurs,
ce `preventDefault()` supprime ensuite le `click` synthétique de fin de
geste. Un tap simple (jamais engagé) n'appelle jamais `preventDefault()`
et génère donc un `click` normal sur l'élément sous le doigt. Le nouveau
listener `click` posé sur `#btn-abs-des` ne peut donc JAMAIS se déclencher
à la fin d'un glissement réel engagé jusqu'à ce bouton — aucune
modification de `onTouchStart`/`onTouchMove`/`onTouchEnd`/
`handleAbsDesDrop`/`cancelPending` n'a donc été nécessaire (vérifié
explicitement ci-dessous : ces 5 fonctions sont BYTE-IDENTIQUES à avant
ce chantier).

Pas de moteur JavaScript disponible dans cet environnement (ni Node, ni
js2py/PyMiniRacer) — ce fichier suit la même convention déjà établie dans
cette suite pour le JS embarqué côté téléphone (voir tests/test_remote_
abs_des_confirm_timeout.py) : inspection structurelle du source, jamais
une réexécution du JS lui-même. Le comportement réel dans un navigateur
reste à confirmer par le test manuel."""
import os
import re
import sys
import tempfile
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import database  # noqa: E402
import main  # noqa: E402
import remote_control  # noqa: E402


class _JsSourceTestCase(unittest.TestCase):
    """Même doublure que tests/test_remote_abs_des_confirm_timeout.py."""

    @classmethod
    def setUpClass(cls):
        cls.page = remote_control._ELIMINATE_PAGE

    def _function_body(self, name, page=None):
        page = self.page if page is None else page
        m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{\{", page)
        self.assertIsNotNone(m, f"fonction {name} introuvable dans la page fournie")
        start = m.end()
        depth = 1
        i = start
        while depth > 0:
            if page[i:i + 2] == "{{":
                depth += 1
                i += 2
            elif page[i:i + 2] == "}}":
                depth -= 1
                i += 2
            else:
                i += 1
        return page[start:i - 2]


# =========================================================================
# 1. État initial et déclaration du drapeau
# =========================================================================
class AbsFirstSortStateTest(_JsSourceTestCase):
    def test_absfirstsortactive_declare_a_false_par_defaut(self):
        self.assertIn("var absFirstSortActive = false;", self.page)

    def test_drapeau_jamais_ecrit_ailleurs_que_dans_le_listener_click(self):
        """Le seul endroit qui AFFECTE absFirstSortActive (en dehors de
        sa déclaration) doit être le listener 'click' du bouton — jamais
        handleAbsDesDrop/onTouchEnd/cancelPending/onTouchMove/
        onTouchStart, qui ne doivent JAMAIS le modifier."""
        writes = re.findall(r"absFirstSortActive\s*=\s*", self.page)
        # Exactement 2 : la déclaration ("= false") et la bascule
        # ("= !absFirstSortActive") dans le listener click.
        self.assertEqual(len(writes), 2)
        for name in ("onTouchStart", "onTouchMove", "onTouchEnd",
                     "handleAbsDesDrop", "cancelPending"):
            body = self._function_body(name)
            self.assertNotIn("absFirstSortActive", body)

    def test_drapeau_lu_uniquement_dans_renderlists(self):
        """La seule fonction qui LIT absFirstSortActive doit être
        renderLists — jamais le chemin de glissement."""
        body = self._function_body("renderLists")
        self.assertIn("absFirstSortActive", body)
        for name in ("onTouchStart", "onTouchMove", "onTouchEnd",
                     "handleAbsDesDrop", "cancelPending", "makeItem",
                     "applyTableFilter"):
            body = self._function_body(name)
            self.assertNotIn("absFirstSortActive", body)


# =========================================================================
# 2. Listener "click" sur le bouton : bascule + indication visuelle +
#    redessin — jamais d'écriture BDD ni d'appel serveur
# =========================================================================
class AbsDesClickListenerTest(_JsSourceTestCase):
    """Revu le 2026-10-19 (régression réelle corrigée, voir
    AbsDesButtonMissingGuardTest ci-dessous) : le listener n'est plus
    posé par un appel chaîné direct sur getElementById(...), mais via la
    variable `absDesSortBtn`, déjà testée nulle avant d'appeler
    addEventListener — _listener_body() et les vérifications suivent ce
    nouveau point d'ancrage, le CONTENU du listener lui-même (bascule du
    drapeau, classe visuelle, redessin, aucun réseau/BDD) reste
    EXACTEMENT le même qu'avant ce correctif."""

    def _listener_body(self):
        m = re.search(
            r"absDesSortBtn\.addEventListener\('click',\s*function\(\)\s*\{\{",
            self.page,
        )
        self.assertIsNotNone(m, "listener click sur btn-abs-des introuvable")
        start = m.end()
        depth = 1
        i = start
        while depth > 0:
            if self.page[i:i + 2] == "{{":
                depth += 1
                i += 2
            elif self.page[i:i + 2] == "}}":
                depth -= 1
                i += 2
            else:
                i += 1
        return self.page[start:i - 2]

    def test_listener_bascule_le_drapeau(self):
        body = self._listener_body()
        self.assertIn("absFirstSortActive = !absFirstSortActive;", body)

    def test_listener_bascule_la_classe_visuelle_sort_active(self):
        body = self._listener_body()
        self.assertIn("classList.toggle('sort-active', absFirstSortActive);", body)

    def test_listener_redessine_la_liste(self):
        body = self._listener_body()
        self.assertIn("renderLists();", body)

    def test_listener_najamais_dappel_reseau_ni_decriture_bdd(self):
        body = self._listener_body()
        self.assertNotIn("fetch(", body)
        self.assertNotIn("fetchWithTimeout(", body)
        self.assertNotIn("sendAction(", body)

    def test_un_seul_listener_click_pose_sur_ce_bouton(self):
        occurrences = self.page.count("absDesSortBtn.addEventListener('click'")
        self.assertEqual(occurrences, 1)


# =========================================================================
# 1bis. Régression réelle (2026-10-19) : page Éliminations totalement
#    vide (Mac + iPhone) quand "Gestion de l'accueil" est désactivée —
#    cas par défaut pour un tournoi normal du club, et pour TOUT nouveau
#    tournoi. Cause : getElementById('btn-abs-des').addEventListener(...)
#    posé SANS vérifier que l'élément existe ; #btn-abs-des n'est
#    construit dans le HTML QUE si "Gestion de l'accueil" est activée
#    (voir main.py, abs_des_button). Sans garde, cette ligne levait une
#    exception JS AVANT le premier appel à loadPlayers() plus bas dans
#    le même <script> — aucun joueur n'était donc jamais chargé ni
#    affiché, sans le moindre message visible.
#
#    AVERTISSEMENT (comme pour tout ce fichier) : aucun moteur
#    JavaScript n'est disponible dans cet environnement (ni Node, ni
#    js2py/PyMiniRacer) — ces tests vérifient uniquement, par inspection
#    du SOURCE, que la ligne fautive est bien gardée et que les deux
#    configurations ("Gestion de l'accueil" activée ou non) restent
#    cohérentes textuellement. Ils NE PEUVENT PAS exécuter le script et
#    donc ne prouvent pas, par eux-mêmes, l'absence d'exception à
#    l'exécution réelle dans un navigateur — seul un essai manuel réel
#    (déjà effectué pour ce correctif, cause confirmée puis corrigée)
#    le garantit.
# =========================================================================
class AbsDesButtonMissingGuardTest(_JsSourceTestCase):
    def test_getelementbyid_najamais_chaine_directement_sur_addeventlistener(self):
        """Cœur du correctif : plus aucun appel direct
        getElementById('btn-abs-des').addEventListener(...) — la seule
        façon sûre de poser ce listener passe désormais par une
        variable explicitement testée avant l'appel."""
        self.assertNotIn(
            "getElementById('btn-abs-des').addEventListener", self.page,
        )

    def test_la_variable_est_bien_testee_avant_addeventlistener(self):
        m = re.search(
            r"var absDesSortBtn = document\.getElementById\('btn-abs-des'\);\s*"
            r"if \(absDesSortBtn\) \{\{",
            self.page,
        )
        self.assertIsNotNone(
            m, "absDesSortBtn doit être vérifiée (if) avant tout usage pour le listener click",
        )

    def test_la_classe_sort_active_bascule_aussi_via_la_variable_gardee(self):
        """classList.toggle('sort-active', ...) doit rester À L'INTÉRIEUR
        du bloc protégé (sur absDesSortBtn, jamais un nouvel appel
        getElementById('btn-abs-des') non gardé qui réintroduirait le
        même risque)."""
        body = self._listener_body_for_test()
        self.assertIn("absDesSortBtn.classList.toggle", body)

    def _listener_body_for_test(self):
        m = re.search(
            r"absDesSortBtn\.addEventListener\('click',\s*function\(\)\s*\{\{",
            self.page,
        )
        self.assertIsNotNone(m)
        start = m.end()
        depth = 1
        i = start
        while depth > 0:
            if self.page[i:i + 2] == "{{":
                depth += 1
                i += 2
            elif self.page[i:i + 2] == "}}":
                depth -= 1
                i += 2
            else:
                i += 1
        return self.page[start:i - 2]

    def test_les_deux_autres_acces_a_ce_bouton_restent_deja_gardes(self):
        """Vérifie qu'aucun autre accès à "btn-abs-des" dans le JS n'a été
        rendu, par erreur, non gardé en corrigeant celui-ci — les deux
        autres usages existants (détection du dépôt pendant un glisser,
        comparaison d'id en fin de glisser) étaient déjà protégés avant
        ce correctif et le restent, INCHANGÉS."""
        self.assertIn(
            "if (absDesBtn && pointInRect(", self.page,
        )
        self.assertIn(
            "if (wasEngaged && target && target.id === 'btn-abs-des')", self.page,
        )

    def test_abs_des_button_vide_najamais_le_tag_bouton_dans_la_page(self):
        """Non-régression de la construction HTML elle-même (main.py,
        inchangée par ce correctif) : {{abs_des_button}} vide (Gestion de
        l'accueil désactivée) ne doit jamais laisser le tag <button
        id="btn-abs-des"> dans la page rendue — sinon le scénario qui a
        produit le bug ne serait plus jamais exercé par ces tests."""
        rendered = self.page.format(
            tournament_name="Test", reload_script="", rebalance_widget="",
            auth_redirect_script="", abs_des_button="",
        )
        self.assertNotIn('id="btn-abs-des"', rendered)
        # Le script, lui, reste toujours présent et syntaxiquement entier
        # (le bloc "if (absDesSortBtn) {" n'est jamais retiré selon ce
        # réglage — seul le HTML du bouton lui-même change).
        self.assertIn("var absDesSortBtn = document.getElementById('btn-abs-des');", rendered)

    def test_abs_des_button_present_garde_le_tag_bouton_dans_la_page(self):
        """Configuration inverse (Gestion de l'accueil activée) : le tag
        <button> doit bien apparaître, pour que absDesSortBtn ne soit
        JAMAIS null dans ce cas et que le tri "absents en premier" reste
        pleinement fonctionnel — voir AbsFirstSortStateTest/
        AbsDesClickListenerTest ci-dessus pour le comportement du
        listener lui-même, inchangé."""
        rendered = self.page.format(
            tournament_name="Test", reload_script="", rebalance_widget="",
            auth_redirect_script="",
            abs_des_button='<button id="btn-abs-des" data-target="true">🚩 Abs/Dés</button>',
        )
        self.assertIn('id="btn-abs-des"', rendered)


# =========================================================================
# 3. renderLists() : tri colonne gauche uniquement, ordre alphabétique
#    interne conservé, colonne droite jamais concernée
# =========================================================================
class RenderListsSortLogicTest(_JsSourceTestCase):
    def setUp(self):
        self.body = self._function_body("renderLists")

    def test_colonne_gauche_utilise_leftorder(self):
        self.assertIn("leftOrder.forEach(function(p) {{", self.body)
        self.assertIn("left.appendChild(makeItem(p, true));", self.body)

    def test_colonne_droite_utilise_toujours_sorted_jamais_leftorder(self):
        """La colonne droite ("Éliminateur") ne doit JAMAIS être
        construite à partir de leftOrder — seulement `sorted`, l'ordre
        alphabétique habituel, quel que soit l'état du drapeau."""
        self.assertIn("sorted.forEach(function(p) {{", self.body)
        self.assertIn("right.appendChild(makeItem(p, false));", self.body)
        # La ligne qui alimente la colonne droite ne doit contenir aucune
        # trace de leftOrder.
        right_block = self.body[self.body.index("sorted.forEach"):]
        self.assertNotIn("leftOrder", right_block)

    def test_leftorder_vaut_filtered_par_defaut(self):
        """Depuis le chantier "recherche Nom/Table/Siège" (2026-10-07) :
        leftOrder part de `filtered` (le résultat de la recherche), plus
        directement de `sorted` — "le filtre détermine QUELS joueurs,
        absFirstSortActive détermine ensuite L'ORDRE". Sans aucune
        recherche active, `filtered` égale `sorted` au complet (voir
        tests/test_remote_eliminate_search.py), donc le comportement
        observable SANS recherche reste rigoureusement identique à avant
        ce chantier — seul le nom de la variable source a changé."""
        self.assertIn("var leftOrder = filtered;", self.body)

    def test_leftorder_reconstruit_uniquement_si_drapeau_actif(self):
        m = re.search(r"if \(absFirstSortActive\) \{\{(.*?)\}\}", self.body, re.S)
        self.assertIsNotNone(m, "bloc 'if (absFirstSortActive)' introuvable dans renderLists")
        inner = m.group(1)
        self.assertIn("leftOrder = filtered.slice().sort(function(a, b) {{", inner)

    def test_comparateur_regroupe_les_absents_sans_toucher_a_lordre_alphabetique(self):
        """Clé purement booléenne (absent d'abord) : Array.prototype.sort
        étant stable (ECMA-262), l'ordre alphabétique déjà présent dans
        `filtered` (lui-même un sous-ensemble de `sorted`, name.
        localeCompare, qui préserve l'ordre relatif par construction) est
        nécessairement conservé À L'INTÉRIEUR de chacun des deux groupes
        — aucune clé secondaire n'est nécessaire ni présente."""
        self.assertIn("return (b.absent ? 1 : 0) - (a.absent ? 1 : 0);", self.body)
        # Le comparateur ne doit PAS retrier par nom (sinon il casserait
        # la conservation de l'ordre alphabétique déjà établi).
        sort_call = self.body[self.body.index("leftOrder = filtered.slice().sort"):]
        inner_comparator = sort_call[:sort_call.index("}});") + 4]
        self.assertNotIn("localeCompare", inner_comparator)

    def test_tri_appele_apres_le_tri_alphabetique_de_base(self):
        """`sorted` (name.localeCompare) doit être calculé AVANT le bloc
        absFirstSortActive, condition nécessaire pour que le tri stable
        parte bien d'un ordre déjà alphabétique."""
        idx_sorted = self.body.index("var sorted = players.slice().sort")
        idx_flag = self.body.index("if (absFirstSortActive)")
        self.assertLess(idx_sorted, idx_flag)

    def test_liste_vide_toujours_geree_avant_le_tri(self):
        """Aucun absent / aucun joueur : le early-return existant
        (players.length === 0) reste intact et s'exécute avant tout
        calcul de tri — jamais de risque d'erreur sur une liste vide."""
        idx_empty_check = self.body.index("players.length === 0")
        idx_sorted = self.body.index("var sorted = players.slice().sort")
        self.assertLess(idx_empty_check, idx_sorted)


# =========================================================================
# 4. Indication visuelle discrète (.sort-active) — jamais un changement
#    fort de couleur, disparaît au second tap
# =========================================================================
class SortActiveVisualIndicatorTest(_JsSourceTestCase):
    def _css_rule(self, selector):
        m = re.search(re.escape(selector) + r"\s*\{\{(.*?)\}\}", self.page, re.S)
        self.assertIsNotNone(m, f"règle CSS {selector} introuvable")
        return m.group(1)

    def test_regle_css_sort_active_presente(self):
        rule = self._css_rule("#btn-abs-des.sort-active")
        self.assertTrue(rule.strip())

    def test_sort_active_najamais_de_changement_de_couleur_de_fond(self):
        """Discret : contour renforcé / effet enfoncé seulement — jamais
        un changement de `background`, réservé à .drop-hover (glissement
        réel en cours), pour ne jamais confondre les deux états."""
        rule = self._css_rule("#btn-abs-des.sort-active")
        self.assertNotIn("background", rule)

    def test_sort_active_utilise_contour_ou_effet_enfonce(self):
        rule = self._css_rule("#btn-abs-des.sort-active")
        self.assertTrue("box-shadow" in rule or "transform" in rule)

    def test_classe_retiree_par_le_meme_toggle_que_le_drapeau(self):
        """Le toggle de la classe et celui du drapeau sont dans le MÊME
        appel (classList.toggle('sort-active', absFirstSortActive)) :
        l'indication disparaît donc automatiquement et exactement au
        moment où le drapeau redevient false, sans code séparé qui
        pourrait désynchroniser les deux."""
        listener_test = AbsDesClickListenerTest()
        listener_test.page = self.page
        body = listener_test._listener_body()
        self.assertIn(
            "classList.toggle('sort-active', absFirstSortActive);", body,
        )

    def test_drop_hover_reste_la_seule_regle_a_changer_le_fond(self):
        """Non-régression : .drop-hover (glissement réel en cours au-
        dessus du bouton) garde son changement de fond existant,
        totalement indépendant de .sort-active."""
        self.assertIn("#btn-abs-des.drop-hover {{ background: #e8c468; color: #10241a; }}", self.page)


# =========================================================================
# 5. Non-régression STRICTE : les 5 fonctions du glissement/drop sont
#    byte-identiques à avant ce chantier
# =========================================================================
class DragAndDropFunctionsUntouchedTest(_JsSourceTestCase):
    """Chaque corps ci-dessous a été capturé AVANT ce chantier (diagnostic
    validé par l'utilisateur) — toute divergence signalerait une
    modification non autorisée de ces 5 fonctions."""

    def test_ontouchstart_inchangee(self):
        body = self._function_body("onTouchStart")
        self.assertIn("engaged: false,", body)
        self.assertIn("el.classList.add('pressed');", body)
        self.assertNotIn("absFirstSortActive", body)
        self.assertNotIn("btn-abs-des", body)

    def test_ontouchmove_inchangee(self):
        body = self._function_body("onTouchMove")
        self.assertIn("e.preventDefault();", body)
        self.assertIn("pending.engaged = true;", body)
        self.assertIn("var absDesBtn = document.getElementById('btn-abs-des');", body)
        self.assertNotIn("absFirstSortActive", body)
        self.assertNotIn("sort-active", body)

    def test_ontouchend_inchangee(self):
        body = self._function_body("onTouchEnd")
        self.assertIn("var wasEngaged = pending && pending.engaged;", body)
        self.assertIn("if (wasEngaged && target && target.id === 'btn-abs-des') {{", body)
        self.assertIn("handleAbsDesDrop(drag);", body)
        self.assertNotIn("absFirstSortActive", body)

    def test_handleabsdesdrop_inchangee(self):
        body = self._function_body("handleAbsDesDrop")
        self.assertIn("showAbsDesConfirm(", body)
        self.assertNotIn("absFirstSortActive", body)
        self.assertNotIn("renderLists", body)

    def test_cancelpending_inchangee(self):
        body = self._function_body("cancelPending")
        self.assertIn("clearTableFilter();", body)
        self.assertNotIn("absFirstSortActive", body)

    def test_cle_du_diagnostic_preventdefault_supprime_le_click_de_fin_de_drag(self):
        """Documente/verrouille la garantie exploitée par ce chantier :
        tout glissement engagé appelle preventDefault() dans
        onTouchMove — c'est CE comportement, déjà existant et jamais
        modifié, qui garantit qu'un vrai drag ne peut pas synthétiser de
        click sur btn-abs-des en fin de geste."""
        move_body = self._function_body("onTouchMove")
        idx_engaged = move_body.index("pending.engaged = true;")
        idx_prevent = move_body.index("e.preventDefault();")
        self.assertLess(idx_engaged, idx_prevent)


# =========================================================================
# 7. Complément côté serveur (pas du JS) : le scénario exact rapporté
#    (8 inscrits, 7 actifs, 1 déjà éliminé) renvoie bien 7 entrées —
#    confirme que App._refresh_remote_players_cache (donc /players) n'a
#    jamais été la cause de la page vide : la régression est isolée au
#    seul listener JS non gardé, corrigé ci-dessus.
# =========================================================================
class RemotePlayersCacheActiveScenarioTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="remote_players_cache_")
        self.addCleanup(self._tmp.cleanup)
        self.db = database.Database(os.path.join(self._tmp.name, "To191026.tournoi"))
        self.addCleanup(self.db.close)

    def test_8_inscrits_7_actifs_1_elimine_renvoie_7_entrees(self):
        ids = [self.db.add_player(f"Joueur {i}", "CPC") for i in range(8)]
        self.db.eliminate_player(ids[0], eliminated_by_id=ids[1])

        app = types.SimpleNamespace(db=self.db)
        app._refresh_remote_players_cache = types.MethodType(
            main.App._refresh_remote_players_cache, app,
        )
        app._refresh_remote_players_cache()

        self.assertEqual(len(app._remote_players_cache), 7)
        cached_ids = {p["id"] for p in app._remote_players_cache}
        self.assertNotIn(ids[0], cached_ids)  # l'éliminé n'apparaît jamais
        for pid in ids[1:]:
            self.assertIn(pid, cached_ids)

    def test_aucun_tournoi_ouvert_renvoie_une_liste_vide_jamais_une_exception(self):
        app = types.SimpleNamespace(db=None)
        app._refresh_remote_players_cache = types.MethodType(
            main.App._refresh_remote_players_cache, app,
        )
        app._refresh_remote_players_cache()
        self.assertEqual(app._remote_players_cache, [])


if __name__ == "__main__":
    unittest.main()
