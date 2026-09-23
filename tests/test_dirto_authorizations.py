# -*- coding: utf-8 -*-
"""Autorisations DIRTO par tournoi — Phase 3 du chantier "Sécurisation
du Contrôle à distance" (demande du 2026-09-20) : table remote_
authorizations, Database.get_dirto_authorization/list_dirto_
authorizations/set_dirto_authorization/clear_dirto_authorization,
REMOTE_PERMISSION_LABELS.

RÈGLES EXPLICITEMENT DEMANDÉES, vérifiées ici :
- stockage DANS le tournoi concerné (fichier .tournoi), jamais global ;
- une ligne par DIRTO nominativement, jamais deux pour le même nom
  (remplacement, jamais accumulation) ;
- un même DIRTO peut avoir des permissions DIFFÉRENTES d'un tournoi à
  l'autre (étanchéité complète entre fichiers .tournoi) ;
- "Terminer le tournoi" ne doit JAMAIS pouvoir être accordée à un DIRTO
  (absente de REMOTE_PERMISSION_LABELS, filtrée si présentée quand même
  — dernier filet de sécurité, en plus de l'UI qui ne la propose jamais) ;
- aucune autorisation existante = aucune permission DIRTO (None) ;
- modification d'une autorisation existante (remplace, conserve
  granted_at, met à jour admin_name/updated_at) ;
- retrait complet (suppression de la ligne, pas un simple vidage) ;
- changement d'ADMIN accordant les droits (admin_name reflète TOUJOURS
  le dernier ADMIN à avoir agi) ;
- ancien tournoi sans la table = compatible (CREATE TABLE IF NOT EXISTS,
  exécuté à chaque ouverture — vérifié directement sur un VRAI ancien
  fichier .tournoi créé avec le schéma d'AVANT ce chantier)."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402


def _new_db(tmp_dir, name="A.tournoi"):
    return database.Database(os.path.join(tmp_dir, name))


class DirtoAuthorizationTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dirto_authorizations_test_")
        self.addCleanup(self._tmp.cleanup)


class AucuneAutorisationParDefautTest(DirtoAuthorizationTestCase):
    def test_dirto_jamais_autorise_renvoie_none(self):
        db = _new_db(self._tmp.name)
        self.assertIsNone(db.get_dirto_authorization("Marie"))
        db.conn.close()

    def test_liste_vide_sur_nouveau_tournoi(self):
        db = _new_db(self._tmp.name)
        self.assertEqual(db.list_dirto_authorizations(), [])
        db.conn.close()

    def test_nom_vide_renvoie_none(self):
        db = _new_db(self._tmp.name)
        self.assertIsNone(db.get_dirto_authorization(""))
        self.assertIsNone(db.get_dirto_authorization("   "))
        db.conn.close()


class OctroiTest(DirtoAuthorizationTestCase):
    def test_octroi_simple(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["eliminations", "tables"])
        auth = db.get_dirto_authorization("Marie")
        self.assertEqual(auth["dirto_name"], "Marie")
        self.assertEqual(auth["admin_name"], "Raj")
        self.assertEqual(sorted(auth["permissions"]), ["eliminations", "tables"])
        db.conn.close()

    def test_octroi_sans_dirto_name_ne_fait_rien(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("", "Raj", ["tables"])
        self.assertEqual(db.list_dirto_authorizations(), [])
        db.conn.close()

    def test_octroi_sans_admin_name_ne_fait_rien(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "", ["tables"])
        self.assertIsNone(db.get_dirto_authorization("Marie"))
        db.conn.close()

    def test_octroi_avec_liste_de_permissions_vide_cree_quand_meme_la_ligne(self):
        """Une autorisation "accordée" mais sans aucune fonction cochée
        doit rester représentable (pas juste équivalente à "jamais
        autorisé") — utile pour distinguer "je le retire" de "je le
        garde autorisé mais sans rien cocher pour l'instant"."""
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", [])
        auth = db.get_dirto_authorization("Marie")
        self.assertIsNotNone(auth)
        self.assertEqual(auth["permissions"], [])
        db.conn.close()

    def test_espaces_superflus_normalises(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("  Marie  ", "  Raj  ", ["tables"])
        self.assertIsNotNone(db.get_dirto_authorization("Marie"))
        db.conn.close()


class TerminerLeTournoiJamaisAccordableTest(DirtoAuthorizationTestCase):
    """Règle non négociable explicitement demandée."""

    def test_end_tournament_absent_de_remote_permission_labels(self):
        self.assertNotIn("end_tournament", database.REMOTE_PERMISSION_LABELS)
        self.assertNotIn("Terminer le tournoi", database.REMOTE_PERMISSION_LABELS.values())

    def test_end_tournament_filtre_si_presente_quand_meme(self):
        """Dernier filet de sécurité côté données : même si un appelant
        (bug, ancienne version, manipulation directe) tentait de
        l'accorder, elle ne doit JAMAIS survivre à l'écriture."""
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["eliminations", "end_tournament", "tables"])
        auth = db.get_dirto_authorization("Marie")
        self.assertNotIn("end_tournament", auth["permissions"])
        self.assertEqual(sorted(auth["permissions"]), ["eliminations", "tables"])
        db.conn.close()

    def test_toute_cle_inconnue_filtree(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["eliminations", "nimportequoi"])
        auth = db.get_dirto_authorization("Marie")
        self.assertEqual(auth["permissions"], ["eliminations"])
        db.conn.close()


class ModificationTest(DirtoAuthorizationTestCase):
    def test_reoctroi_remplace_jamais_naccumule(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["eliminations", "tables"])
        db.set_dirto_authorization("Marie", "Raj", ["moves"])
        auth = db.get_dirto_authorization("Marie")
        self.assertEqual(auth["permissions"], ["moves"])  # pas ["eliminations","tables","moves"]
        db.conn.close()

    def test_une_seule_ligne_par_dirto_meme_apres_plusieurs_modifications(self):
        db = _new_db(self._tmp.name)
        for perms in (["tables"], ["moves"], ["clock", "levels"]):
            db.set_dirto_authorization("Marie", "Raj", perms)
        self.assertEqual(len(db.list_dirto_authorizations()), 1)
        db.conn.close()

    def test_granted_at_preserve_updated_at_change(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["tables"])
        first = db.get_dirto_authorization("Marie")
        db.set_dirto_authorization("Marie", "Raj", ["moves"])
        second = db.get_dirto_authorization("Marie")
        self.assertEqual(second["granted_at"], first["granted_at"])
        self.assertGreaterEqual(second["updated_at"], first["updated_at"])
        db.conn.close()

    def test_changement_dadmin_accordant_les_droits(self):
        """"changement d'ADMIN accordant les droits" — admin_name reflète
        TOUJOURS le dernier ADMIN à avoir agi, jamais celui d'origine."""
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["tables"])
        db.set_dirto_authorization("Marie", "Julie", ["tables", "moves"])
        auth = db.get_dirto_authorization("Marie")
        self.assertEqual(auth["admin_name"], "Julie")
        db.conn.close()


class RetraitTest(DirtoAuthorizationTestCase):
    def test_retrait_complet_supprime_la_ligne(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["tables"])
        db.clear_dirto_authorization("Marie")
        self.assertIsNone(db.get_dirto_authorization("Marie"))
        self.assertEqual(db.list_dirto_authorizations(), [])
        db.conn.close()

    def test_retrait_dun_dirto_inconnu_ne_leve_pas(self):
        db = _new_db(self._tmp.name)
        db.clear_dirto_authorization("Personne Inconnue")  # ne doit pas lever
        db.conn.close()

    def test_retrait_naffecte_pas_les_autres_dirto(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["tables"])
        db.set_dirto_authorization("Bob", "Raj", ["moves"])
        db.clear_dirto_authorization("Marie")
        self.assertIsNone(db.get_dirto_authorization("Marie"))
        self.assertIsNotNone(db.get_dirto_authorization("Bob"))
        db.conn.close()


class ListeTest(DirtoAuthorizationTestCase):
    def test_liste_triee_par_nom(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Zoe", "Raj", ["tables"])
        db.set_dirto_authorization("Alice", "Raj", ["moves"])
        names = [a["dirto_name"] for a in db.list_dirto_authorizations()]
        self.assertEqual(names, ["Alice", "Zoe"])
        db.conn.close()


class PersistanceApresFermetureReouvertureTest(DirtoAuthorizationTestCase):
    def test_autorisation_survit_a_une_fermeture_reouverture(self):
        path = os.path.join(self._tmp.name, "A.tournoi")
        db1 = database.Database(path)
        db1.set_dirto_authorization("Marie", "Raj", ["eliminations", "photos"])
        db1.conn.close()

        db2 = database.Database(path)  # nouvelle connexion, vraie réouverture
        auth = db2.get_dirto_authorization("Marie")
        self.assertEqual(auth["admin_name"], "Raj")
        self.assertEqual(sorted(auth["permissions"]), ["eliminations", "photos"])
        db2.conn.close()

    def test_retrait_survit_aussi_a_une_reouverture(self):
        path = os.path.join(self._tmp.name, "A.tournoi")
        db1 = database.Database(path)
        db1.set_dirto_authorization("Marie", "Raj", ["tables"])
        db1.clear_dirto_authorization("Marie")
        db1.conn.close()

        db2 = database.Database(path)
        self.assertIsNone(db2.get_dirto_authorization("Marie"))
        db2.conn.close()


class EtancheiteMultiTournoisTest(DirtoAuthorizationTestCase):
    """"Un DIRTO peut avoir des permissions différentes selon le
    tournoi" + "les droits du tournoi A ne doivent jamais donner des
    droits sur le tournoi B" — vérifié avec deux VRAIS fichiers
    .tournoi séparés."""

    def test_meme_dirto_permissions_differentes_par_tournoi(self):
        db_a = _new_db(self._tmp.name, "A.tournoi")
        db_b = _new_db(self._tmp.name, "B.tournoi")

        db_a.set_dirto_authorization("Marie", "Raj", ["eliminations"])
        db_b.set_dirto_authorization("Marie", "Raj", ["moves", "photos"])

        self.assertEqual(db_a.get_dirto_authorization("Marie")["permissions"], ["eliminations"])
        self.assertEqual(sorted(db_b.get_dirto_authorization("Marie")["permissions"]), ["moves", "photos"])
        db_a.conn.close()
        db_b.conn.close()

    def test_retrait_sur_un_tournoi_najamais_deffet_sur_lautre(self):
        db_a = _new_db(self._tmp.name, "A.tournoi")
        db_b = _new_db(self._tmp.name, "B.tournoi")
        db_a.set_dirto_authorization("Marie", "Raj", ["tables"])
        db_b.set_dirto_authorization("Marie", "Raj", ["tables"])

        db_a.clear_dirto_authorization("Marie")

        self.assertIsNone(db_a.get_dirto_authorization("Marie"))
        self.assertIsNotNone(db_b.get_dirto_authorization("Marie"))  # intact
        db_a.conn.close()
        db_b.conn.close()

    def test_dirto_autorise_sur_a_absent_de_b(self):
        db_a = _new_db(self._tmp.name, "A.tournoi")
        db_b = _new_db(self._tmp.name, "B.tournoi")
        db_a.set_dirto_authorization("Marie", "Raj", ["tables"])

        self.assertIsNone(db_b.get_dirto_authorization("Marie"))
        self.assertEqual(db_b.list_dirto_authorizations(), [])
        db_a.conn.close()
        db_b.conn.close()


class CompatibiliteAncienTournoiTest(DirtoAuthorizationTestCase):
    """"Ancien tournoi sans nouvelle table = compatible" — vérifié sur un
    VRAI fichier .tournoi créé avec le schéma d'AVANT ce chantier (extrait
    de HEAD via git show), jamais un fichier simulé."""

    def test_ancien_fichier_tournoi_sans_la_table_souvre_normalement(self):
        import subprocess
        old_db_src = subprocess.run(
            ["git", "show", "HEAD:database.py"],
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            capture_output=True, text=True, check=True,
        ).stdout
        old_module_dir = os.path.join(self._tmp.name, "_old_module")
        os.makedirs(old_module_dir, exist_ok=True)
        with open(os.path.join(old_module_dir, "database_old.py"), "w", encoding="utf-8") as f:
            f.write(old_db_src)

        path = os.path.join(self._tmp.name, "Ancien.tournoi")
        script = (
            f"import sys; sys.path.insert(0, {old_module_dir!r}); "
            f"import database_old as old_db; "
            f"d = old_db.Database({path!r}); d.add_player('Alice'); d.conn.close()"
        )
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(os.path.exists(path))

        # Réouverture avec le code ACTUEL (celui de ce fichier de test) :
        # doit fonctionner sans erreur, table créée automatiquement.
        db = database.Database(path)
        self.assertEqual([p["name"] for p in db.list_players()], ["Alice"])  # données préservées
        self.assertIsNone(db.get_dirto_authorization("Marie"))  # aucune autorisation, jamais une erreur
        db.set_dirto_authorization("Marie", "Raj", ["tables"])
        self.assertIsNotNone(db.get_dirto_authorization("Marie"))  # fonctionne normalement ensuite
        db.conn.close()


class SuppressionReclassementRepertoireTest(DirtoAuthorizationTestCase):
    """"Suppression/reclassement ultérieur d'une personne du Répertoire
    sans crash" — la couche base de données (celle testée ici) ne
    valide JAMAIS dirto_name/admin_name contre le Répertoire (cette
    responsabilité vit dans l'UI, voir main.py) : elle doit donc rester
    parfaitement fonctionnelle même pour un nom qui n'existe plus/jamais
    existé dans roster.py — aucun import de roster ici, exprès."""

    def test_autorisation_dun_nom_absent_du_repertoire_fonctionne_quand_meme(self):
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("PersonneSupprimeeDuRepertoire", "Raj", ["tables"])
        auth = db.get_dirto_authorization("PersonneSupprimeeDuRepertoire")
        self.assertIsNotNone(auth)
        db.conn.close()

    def test_lecture_dune_autorisation_existante_ne_leve_jamais(self):
        """Simule une personne reclassée/supprimée APRÈS l'octroi : la
        ligne reste lisible normalement (get/list), aucune validation
        croisée avec le Répertoire à ce niveau."""
        db = _new_db(self._tmp.name)
        db.set_dirto_authorization("Marie", "Raj", ["eliminations"])
        # ... "Marie" est ensuite supprimée/reclassée dans roster.py,
        # mais ça n'a AUCUNE incidence ici : simple chaîne de caractères.
        self.assertIsNotNone(db.get_dirto_authorization("Marie"))
        self.assertEqual(len(db.list_dirto_authorizations()), 1)
        db.conn.close()


if __name__ == "__main__":
    unittest.main()
