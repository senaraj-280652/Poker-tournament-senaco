# -*- coding: utf-8 -*-
"""Recherche Nom/Table/Siège sur la page "Gestion des éliminations"
(demande du 2026-10-07, _ELIMINATE_PAGE, remote_control.py) — un DIRTO
retrouve rapidement un joueur (par son nom, ou par la place Table/Siège
qu'il occupe — y compris s'il est absent) pour agir sur lui par le
glisser-déposer déjà existant, sans jamais créer de fiche/résultat
séparé.

Principes (voir la conversation pour le diagnostic complet, vérifié en
plus par exécution réelle dans un navigateur avec des données simulées
avant d'écrire ce fichier) :
- Nom : sous-chaîne insensible à la casse, MÊME convention que la
  recherche déjà existante page /checkin (#search-input) — aucun repli
  d'accents, cohérence avec le reste de Senaco.
- Table : si la saisie est purement numérique, correspondance EXACTE
  avec le nombre extrait du nom de table ("7" -> "Table 7", jamais
  "Table 17/27/70") ; repli en sous-chaîne sinon (futur nom non
  numérique, actuellement inexistant).
- Siège : correspondance EXACTE uniquement (jamais une sous-chaîne —
  "4" ne doit jamais retrouver 14).
- Les 3 critères renseignés se combinent en ET logique ; un critère vide
  est ignoré.
- Le filtre ne concerne QUE la colonne gauche ("Éliminé") — la colonne
  droite ("Éliminateur") reste construite depuis `sorted`, intégralement
  inchangée.
- absFirstSortActive (chantier précédent, INCHANGÉ) s'applique APRÈS le
  filtre : "le filtre détermine QUELS joueurs, le tri détermine ensuite
  L'ORDRE".
- makeItem/onTouchStart/onTouchMove/onTouchEnd/handleAbsDesDrop/
  cancelPending : aucune modification — un joueur trouvé par la
  recherche reste l'exact même item glissable qu'aujourd'hui.
- Recherche dynamique (aucun bouton "Rechercher"), aucun appel réseau,
  aucune écriture BDD : purement un filtrage client des données déjà
  reçues via /players (même permission qu'aujourd'hui, aucune nouvelle
  route).

Pas de moteur JavaScript disponible dans cet environnement (ni Node, ni
js2py/PyMiniRacer) — ce fichier suit la même convention déjà établie
dans cette suite (inspection structurelle du source), complétée cette
fois par une vérification manuelle préalable dans un vrai navigateur
(données simulées, scénarios du cahier des charges) qui a d'ailleurs
révélé et fait corriger un bug réel avant d'écrire ces tests : le bouton
"×" ne devenait jamais visible (`style.display = ''` retombe sur le
`display:none` du CSS, corrigé en `'inline-block'` explicite)."""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import remote_control  # noqa: E402


class _JsSourceTestCase(unittest.TestCase):
    """Même doublure que tests/test_remote_abs_des_confirm_timeout.py et
    tests/test_remote_abs_first_sort.py."""

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

    def _listener_body(self, element_id, event="click"):
        pattern = (
            r"getElementById\('" + re.escape(element_id) + r"'\)\.addEventListener\('"
            + re.escape(event) + r"',\s*function\([^)]*\)\s*\{\{"
        )
        m = re.search(pattern, self.page)
        self.assertIsNotNone(m, f"listener {event} sur {element_id} introuvable")
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


# =========================================================================
# 1. Markup HTML des 3 champs + bouton ×
# =========================================================================
class SearchMarkupTest(_JsSourceTestCase):
    def test_trois_champs_presents_avec_les_bons_attributs(self):
        self.assertIn('id="search-name" type="text" placeholder="Nom"', self.page)
        self.assertIn(
            'id="search-table" type="text" inputmode="numeric" placeholder="Table"', self.page,
        )
        self.assertIn(
            'id="search-seat" type="text" inputmode="numeric" placeholder="Siège"', self.page,
        )

    def test_jamais_typenumber_sur_table_ou_siege(self):
        """type="number" bloquerait toute saisie non numérique — un futur
        nom de table non numérique doit toujours rester saisissable."""
        self.assertNotIn('id="search-table" type="number"', self.page)
        self.assertNotIn('id="search-seat" type="number"', self.page)

    def test_bouton_clear_present(self):
        self.assertIn('id="search-clear" type="button"', self.page)

    def test_champs_places_dans_conn_indicator(self):
        conn_block = self.page[self.page.index('id="conn-indicator"'):]
        conn_block = conn_block[:conn_block.index("</div>\n  <div id=\"columns\"")]
        self.assertIn("elim-search", conn_block)
        self.assertIn("search-name", conn_block)


# =========================================================================
# 2. État : variables, écriture/lecture exclusives
# =========================================================================
class SearchStateTest(_JsSourceTestCase):
    def test_trois_variables_declarees_vides_par_defaut(self):
        self.assertIn("var nameFilter = '';", self.page)
        self.assertIn("var tableFilter = '';", self.page)
        self.assertIn("var seatFilter = '';", self.page)

    def test_variables_jamais_ecrites_dans_le_chemin_de_glissement(self):
        for name in ("onTouchStart", "onTouchMove", "onTouchEnd",
                     "handleAbsDesDrop", "cancelPending", "makeItem"):
            body = self._function_body(name)
            for var in ("nameFilter", "tableFilter", "seatFilter"):
                self.assertNotIn(var, body)


# =========================================================================
# 3. Listeners des 3 champs : dynamique, aucun appel réseau/BDD
# =========================================================================
class SearchInputListenersTest(_JsSourceTestCase):
    def test_chaque_champ_met_a_jour_sa_variable_et_redessine(self):
        pairs = [
            ("search-name", "nameFilter"),
            ("search-table", "tableFilter"),
            ("search-seat", "seatFilter"),
        ]
        for element_id, var in pairs:
            body = self._listener_body(element_id, "input")
            self.assertIn(var + " = e.target.value;", body)
            self.assertIn("renderLists();", body)
            self.assertIn("updateSearchClearVisibility();", body)

    def test_aucun_appel_reseau_ni_ecriture_bdd_dans_les_listeners(self):
        for element_id, event in (
            ("search-name", "input"), ("search-table", "input"),
            ("search-seat", "input"), ("search-clear", "click"),
        ):
            body = self._listener_body(element_id, event)
            self.assertNotIn("fetch(", body)
            self.assertNotIn("fetchWithTimeout(", body)
            self.assertNotIn("sendAction(", body)

    def test_recherche_dynamique_aucun_bouton_rechercher(self):
        self.assertNotIn(">Rechercher<", self.page)
        self.assertNotIn('id="search-submit"', self.page)


# =========================================================================
# 4. Bouton × : efface tout, ne touche jamais absFirstSortActive
# =========================================================================
class SearchClearButtonTest(_JsSourceTestCase):
    def setUp(self):
        self.body = self._listener_body("search-clear", "click")

    def test_efface_les_trois_variables(self):
        self.assertIn("nameFilter = '';", self.body)
        self.assertIn("tableFilter = '';", self.body)
        self.assertIn("seatFilter = '';", self.body)

    def test_vide_aussi_les_trois_champs_a_lecran(self):
        self.assertIn("getElementById('search-name').value = '';", self.body)
        self.assertIn("getElementById('search-table').value = '';", self.body)
        self.assertIn("getElementById('search-seat').value = '';", self.body)

    def test_redessine_et_met_a_jour_sa_propre_visibilite(self):
        self.assertIn("renderLists();", self.body)
        self.assertIn("updateSearchClearVisibility();", self.body)

    def test_ne_touche_jamais_absfirstsortactive(self):
        self.assertNotIn("absFirstSortActive", self.body)

    def test_visibilite_utilise_une_valeur_display_explicite_pas_vide(self):
        """Verrouille le correctif trouvé par le test manuel préalable :
        style.display = '' retombe sur le display:none du CSS et laisse
        le bouton invisible même quand un critère est renseigné. 'flex'
        (depuis l'agrandissement du 2026-10-07, remplace 'inline-block')
        — nécessaire pour que align-items/justify-content centrent la
        croix agrandie."""
        body = self._function_body("updateSearchClearVisibility")
        self.assertIn("'flex' : 'none'", body)
        self.assertNotIn("? '' : 'none'", body)

    def test_visible_seulement_si_au_moins_un_critere_rempli(self):
        body = self._function_body("updateSearchClearVisibility")
        self.assertIn("nameFilter.trim()", body)
        self.assertIn("tableFilter.trim()", body)
        self.assertIn("seatFilter.trim()", body)


# =========================================================================
# 5. Logique de filtrage dans renderLists() : ET logique, exactitude
#    Table/Siège, sous-chaîne Nom, ordre filtre -> tri, colonne droite
#    jamais filtrée, placeholder "Aucun joueur"
# =========================================================================
class RenderListsFilterLogicTest(_JsSourceTestCase):
    def setUp(self):
        self.body = self._function_body("renderLists")

    def test_nom_sous_chaine_insensible_a_la_casse(self):
        self.assertIn("var nameTerm = nameFilter.trim().toLowerCase();", self.body)
        self.assertIn("p.name.toLowerCase().indexOf(nameTerm) === -1", self.body)

    def test_meme_convention_que_la_recherche_checkin_existante(self):
        """Même forme exacte que #search-input (/checkin, déjà existant,
        JAMAIS modifié par ce chantier) : .trim().toLowerCase() +
        .indexOf(...) !== -1 — aucune nouvelle logique de normalisation
        (accents, etc.) inventée ici, cohérence avec le reste de Senaco."""
        self.assertIn(
            "var term = searchTerm.trim().toLowerCase();", remote_control._CHECKIN_PAGE,
        )
        self.assertIn(
            "p.name.toLowerCase().indexOf(term) !== -1", remote_control._CHECKIN_PAGE,
        )
        self.assertIn(
            "var nameTerm = nameFilter.trim().toLowerCase();", self.page,
        )
        self.assertIn(
            "p.name.toLowerCase().indexOf(nameTerm) === -1", self.page,
        )

    def test_table_exacte_sur_saisie_numerique_pas_de_sous_chaine(self):
        self.assertIn("var tableTerm = tableFilter.trim();", self.body)
        self.assertIn("tableMatchesQuery(p.table, tableTerm)", self.body)
        fn_body = self._function_body("tableMatchesQuery")
        self.assertIn("/^\\d+$/.test(query)", fn_body)
        self.assertIn("m[0] === query", fn_body)  # égalité stricte, jamais indexOf

    def test_table_repli_sous_chaine_si_non_numerique(self):
        fn_body = self._function_body("tableMatchesQuery")
        self.assertIn("tableName.toLowerCase().indexOf(query.toLowerCase())", fn_body)

    def test_siege_egalite_stricte_jamais_sous_chaine(self):
        self.assertIn("var seatTerm = seatFilter.trim();", self.body)
        self.assertIn("String(p.seat) !== seatTerm", self.body)
        # Jamais indexOf/sous-chaîne pour le siège (sinon "4" retrouverait 14).
        seat_line = [l for l in self.body.splitlines() if "seatTerm" in l and "if" in l]
        self.assertTrue(seat_line)
        self.assertNotIn("indexOf", seat_line[0])

    def test_criteres_combines_en_et_logique(self):
        """Les 3 conditions sont chacune un `if (critère && !correspond) return false;`
        à l'intérieur du MÊME filter() — combinaison ET par construction
        (toutes doivent passer pour que `true` soit atteint)."""
        filter_match = re.search(
            r"var filtered = sorted\.filter\(function\(p\) \{\{(.*?)\}\}\);", self.body, re.S,
        )
        self.assertIsNotNone(filter_match)
        inner = filter_match.group(1)
        self.assertIn("nameTerm &&", inner)
        self.assertIn("tableTerm &&", inner)
        self.assertIn("seatTerm &&", inner)
        self.assertIn("return true;", inner)

    def test_critere_vide_est_ignore(self):
        """Un critère vide ('') est "falsy" en JS : la condition `if
        (critère && ...)` ne s'exécute alors jamais, donc n'exclut
        aucun joueur sur ce critère-là — vérifié structurellement : la
        garde précède toujours l'appel de correspondance."""
        filter_match = re.search(
            r"var filtered = sorted\.filter\(function\(p\) \{\{(.*?)\}\}\);", self.body, re.S,
        )
        inner = filter_match.group(1)
        self.assertIn("if (nameTerm && ", inner)
        self.assertIn("if (tableTerm && ", inner)
        self.assertIn("if (seatTerm && ", inner)

    def test_tri_absents_en_premier_sapplique_sur_filtered_pas_sur_sorted(self):
        self.assertIn("var leftOrder = filtered;", self.body)
        self.assertIn("leftOrder = filtered.slice().sort(function(a, b) {{", self.body)
        self.assertNotIn("leftOrder = sorted.slice().sort", self.body)

    def test_filtre_calcule_avant_le_tri_absents_en_premier(self):
        idx_filtered = self.body.index("var filtered = sorted.filter")
        idx_leftorder = self.body.index("var leftOrder = filtered;")
        idx_flag_block = self.body.index("if (absFirstSortActive)")
        self.assertLess(idx_filtered, idx_leftorder)
        self.assertLess(idx_leftorder, idx_flag_block)

    def test_colonne_droite_toujours_construite_depuis_sorted_jamais_filtree(self):
        right_block = self.body[self.body.index("sorted.forEach(function(p) {{"):]
        self.assertIn("right.appendChild(makeItem(p, false));", right_block)
        self.assertNotIn("filtered", right_block)
        self.assertNotIn("leftOrder", right_block)

    def test_aucun_resultat_affiche_aucun_joueur_jamais_une_zone_vide(self):
        self.assertIn("if (leftOrder.length === 0) {{", self.body)
        self.assertIn("Aucun joueur</div>", self.body)
        # Distinct du cas "aucun joueur actif du tout" (déjà existant,
        # inchangé) : jamais le même message, pour ne pas laisser penser
        # qu'il n'y a réellement aucun joueur dans le tournoi.
        self.assertIn("Aucun joueur actif</div>", self.page)

    def test_makeitem_toujours_utilisee_jamais_de_fiche_separee(self):
        """Le joueur trouvé reste construit par makeItem — même fonction,
        mêmes arguments (p, true) qu'avant ce chantier — jamais un
        rendu de résultat de recherche séparé."""
        self.assertIn("leftOrder.forEach(function(p) {{", self.body)
        self.assertIn("left.appendChild(makeItem(p, true));", self.body)


# =========================================================================
# 6. Non-régression stricte : glissement/drop/élimination inchangés
# =========================================================================
class DragDropAndEliminationUntouchedTest(_JsSourceTestCase):
    def test_ontouchstart_inchangee(self):
        body = self._function_body("onTouchStart")
        self.assertIn("engaged: false,", body)
        self.assertNotIn("tableMatchesQuery", body)

    def test_ontouchmove_inchangee(self):
        body = self._function_body("onTouchMove")
        self.assertIn("e.preventDefault();", body)
        self.assertIn("var absDesBtn = document.getElementById('btn-abs-des');", body)
        self.assertNotIn("tableMatchesQuery", body)

    def test_ontouchend_elimination_inchangee(self):
        body = self._function_body("onTouchEnd")
        self.assertIn("if (wasEngaged && target && target.id === 'btn-abs-des') {{", body)
        self.assertIn("confirmElimination(", body)
        self.assertNotIn("nameFilter", body)

    def test_handleabsdesdrop_inchangee(self):
        body = self._function_body("handleAbsDesDrop")
        self.assertIn("showAbsDesConfirm(", body)
        self.assertNotIn("nameFilter", body)

    def test_cancelpending_inchangee(self):
        body = self._function_body("cancelPending")
        self.assertIn("clearTableFilter();", body)
        self.assertNotIn("nameFilter", body)

    def test_abs_first_sort_toggle_toujours_independant_de_la_recherche(self):
        """Non-régression du chantier précédent : le listener click sur
        Abs/Dés ne doit toujours référencer QUE absFirstSortActive,
        jamais les variables de recherche.

        Revu le 2026-10-19 (régression réelle corrigée — page vide quand
        "Gestion de l'accueil" est désactivée, voir tests/test_remote_
        abs_first_sort.py::AbsDesButtonMissingGuardTest pour le détail
        complet du correctif) : le listener est désormais posé via la
        variable `absDesSortBtn` (vérifiée non nulle avant tout appel),
        jamais par un appel chaîné direct sur getElementById(...) — le
        contenu du listener lui-même reste inchangé."""
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
        body = self.page[start:i - 2]
        self.assertIn("absFirstSortActive = !absFirstSortActive;", body)
        for var in ("nameFilter", "tableFilter", "seatFilter"):
            self.assertNotIn(var, body)


# =========================================================================
# 7. Permissions : aucune nouvelle route, même garde-fou qu'aujourd'hui
# =========================================================================
class SearchPermissionsTest(unittest.TestCase):
    def test_players_et_eliminate_restent_sur_la_meme_permission(self):
        self.assertEqual(
            remote_control._GET_ROUTE_PERMISSION.get("/players"),
            remote_control._GET_ROUTE_PERMISSION.get("/eliminate"),
        )

    def test_aucune_nouvelle_route_liee_a_la_recherche(self):
        for route in remote_control._GET_ROUTE_PERMISSION:
            self.assertNotIn("search", route)


if __name__ == "__main__":
    unittest.main()
