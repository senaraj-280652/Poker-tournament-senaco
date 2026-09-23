# -*- coding: utf-8 -*-
"""Répertoire — champs "Groupe" (ADMIN/DIRTO, exclusifs), "Téléphone" et
"Mail" (demande du 2026-09-20, Phase 1 du chantier "Sécurisation du
Contrôle à distance"). Ce champ "group" n'accorde AUCUN droit par
lui-même ici — il identifie seulement qui PEUT être choisi comme ADMIN/
DIRTO ailleurs (chantiers suivants, Contrôle à distance/LOG).

Compatibilité ascendante (exigée explicitement) : toute ancienne fiche
du Répertoire (liste de chaînes, ou dicts {"name", "club"} d'avant ce
chantier) doit continuer à se charger SANS ERREUR, avec group/phone/mail
valant "" par défaut — jamais une exception, jamais un déclassement
d'ADMIN à DIRTO ou l'inverse."""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import roster  # noqa: E402


class RosterFieldsTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="roster_admin_dirto_test_")
        self.addCleanup(self._tmp.cleanup)
        self.path = os.path.join(self._tmp.name, "roster.json")

        from unittest.mock import patch
        patcher = patch.object(roster, "_roster_path", return_value=self.path)
        self.addCleanup(patcher.stop)
        patcher.start()

    def _write_raw(self, data):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)


class CompatibiliteAnciennesFichesTest(RosterFieldsTestCase):
    """Aucune ancienne fiche du Répertoire ne doit être perturbée par
    l'ajout de ces 3 champs — vérifié directement sur des fichiers bruts
    représentant les formats réellement rencontrés AVANT ce chantier."""

    def test_ancien_format_liste_de_chaines(self):
        self._write_raw(["Alice", "Bob"])
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        for name in ("Alice", "Bob"):
            self.assertEqual(entries[name]["club"], "")
            self.assertEqual(entries[name]["group"], "")
            self.assertEqual(entries[name]["phone"], "")
            self.assertEqual(entries[name]["mail"], "")

    def test_ancien_format_dict_name_club_seulement(self):
        self._write_raw([{"name": "Alice", "club": "CPC"}])
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["club"], "CPC")  # préservé
        self.assertEqual(entries["Alice"]["group"], "")
        self.assertEqual(entries["Alice"]["phone"], "")
        self.assertEqual(entries["Alice"]["mail"], "")

    def test_fichier_absent_ne_leve_pas(self):
        self.assertEqual(roster.load_roster_entries(), [])

    def test_valeur_group_invalide_sur_le_disque_ramenee_a_non_classe(self):
        """Fichier corrompu/futur (valeur de group non reconnue) : jamais
        une exception, jamais une élévation implicite de privilège."""
        self._write_raw([{"name": "Alice", "group": "SUPERADMIN"}])
        self.assertEqual(roster.get_group("Alice"), "")

    def test_group_insensible_a_la_casse_sur_le_disque(self):
        self._write_raw([{"name": "Alice", "group": "admin"}])
        self.assertEqual(roster.get_group("Alice"), "ADMIN")


class GroupExclusiviteTest(RosterFieldsTestCase):
    """"Une personne appartient à UN SEUL groupe, jamais les deux
    simultanément" — décision explicite de l'utilisateur, garantie
    structurellement par un champ UNIQUE (jamais deux booléens)."""

    def test_set_group_admin_puis_lecture(self):
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        self.assertEqual(roster.get_group("Alice"), "ADMIN")

    def test_set_group_dirto_puis_lecture(self):
        roster.set_group("Bob", roster.ROSTER_GROUP_DIRTO)
        self.assertEqual(roster.get_group("Bob"), "DIRTO")

    def test_reaffecter_dirto_apres_admin_remplace_jamais_naccumule(self):
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Alice", roster.ROSTER_GROUP_DIRTO)
        self.assertEqual(roster.get_group("Alice"), "DIRTO")
        # Un seul champ possible : jamais "ADMIN,DIRTO" ni une liste.
        entries = roster.load_roster_entries()
        self.assertEqual(len([e for e in entries if e["name"] == "Alice"]), 1)

    def test_set_group_valeur_invalide_normalisee_a_non_classe(self):
        roster.set_group("Alice", "ADMIN")
        roster.set_group("Alice", "n'importe quoi")
        self.assertEqual(roster.get_group("Alice"), "")

    def test_set_group_cree_la_personne_si_absente(self):
        roster.set_group("Nouveau", roster.ROSTER_GROUP_ADMIN)
        self.assertEqual(roster.get_group("Nouveau"), "ADMIN")
        self.assertIn("Nouveau", roster.load_roster())

    def test_get_group_nom_inconnu_renvoie_non_classe(self):
        self.assertEqual(roster.get_group("Personne Inconnue"), "")


class PhoneMailTest(RosterFieldsTestCase):
    def test_set_get_phone(self):
        roster.set_phone("Alice", "01 02 03 04 05")
        self.assertEqual(roster.get_phone("Alice"), "01 02 03 04 05")

    def test_set_get_mail(self):
        roster.set_mail("Alice", "alice@cpc.fr")
        self.assertEqual(roster.get_mail("Alice"), "alice@cpc.fr")

    def test_phone_et_mail_vides_par_defaut(self):
        roster.add_to_roster("Alice")
        self.assertEqual(roster.get_phone("Alice"), "")
        self.assertEqual(roster.get_mail("Alice"), "")

    def test_set_phone_efface_avec_valeur_vide(self):
        roster.set_phone("Alice", "0102030405")
        roster.set_phone("Alice", "")
        self.assertEqual(roster.get_phone("Alice"), "")

    def test_set_phone_cree_la_personne_si_absente(self):
        roster.set_phone("Nouveau", "0102030405")
        self.assertIn("Nouveau", roster.load_roster())

    def test_phone_avec_zero_initial_preserve(self):
        """Un numéro français commence typiquement par 0 : doit être
        conservé tel quel (chaîne), jamais interprété/tronqué comme un
        nombre."""
        roster.set_phone("Alice", "0601020304")
        self.assertEqual(roster.get_phone("Alice"), "0601020304")


class AddToRosterNeTouchePasGroupPhoneMailTest(RosterFieldsTestCase):
    """add_to_roster (création/import CSV standard) ne doit jamais
    attribuer group/phone/mail — une personne nouvellement créée
    démarre toujours "non classée", exactement comme une ancienne
    fiche."""

    def test_nouvelle_personne_non_classee_sans_contact(self):
        roster.add_to_roster("Alice", club="CPC")
        self.assertEqual(roster.get_group("Alice"), "")
        self.assertEqual(roster.get_phone("Alice"), "")
        self.assertEqual(roster.get_mail("Alice"), "")

    def test_appel_repete_ne_reinitialise_pas_group_phone_mail_deja_regles(self):
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        roster.set_phone("Alice", "0102030405")
        roster.add_to_roster("Alice", club="Nouveau Club")
        self.assertEqual(roster.get_group("Alice"), "ADMIN")
        self.assertEqual(roster.get_phone("Alice"), "0102030405")


class ListByGroupTest(RosterFieldsTestCase):
    def setUp(self):
        super().setUp()
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        roster.set_group("Bob", roster.ROSTER_GROUP_DIRTO)
        roster.set_group("Chris", roster.ROSTER_GROUP_DIRTO)
        roster.add_to_roster("Dave")  # non classé, ne doit apparaître nulle part

    def test_list_admins(self):
        names = [e["name"] for e in roster.list_by_group(roster.ROSTER_GROUP_ADMIN)]
        self.assertEqual(names, ["Alice"])

    def test_list_dirtos_tries_par_nom(self):
        names = [e["name"] for e in roster.list_by_group(roster.ROSTER_GROUP_DIRTO)]
        self.assertEqual(names, ["Bob", "Chris"])

    def test_non_classe_absent_des_deux_listes(self):
        admin_names = [e["name"] for e in roster.list_by_group(roster.ROSTER_GROUP_ADMIN)]
        dirto_names = [e["name"] for e in roster.list_by_group(roster.ROSTER_GROUP_DIRTO)]
        self.assertNotIn("Dave", admin_names)
        self.assertNotIn("Dave", dirto_names)

    def test_valeur_invalide_renvoie_liste_vide(self):
        self.assertEqual(roster.list_by_group("SUPERADMIN"), [])
        self.assertEqual(roster.list_by_group(""), [])

    def test_entrees_completes_pas_seulement_le_nom(self):
        entries = roster.list_by_group(roster.ROSTER_GROUP_ADMIN)
        self.assertEqual(entries[0]["group"], "ADMIN")
        self.assertIn("phone", entries[0])
        self.assertIn("mail", entries[0])


class SaveEntriesPreserveNouveauxChampsTest(RosterFieldsTestCase):
    """Le risque de régression identifié en analyse : save_roster_entries
    ne doit JAMAIS écraser silencieusement group/phone/mail — seul point
    d'écriture centralisé, symétrique de load_roster_entries."""

    def test_save_roster_entries_persiste_les_3_nouveaux_champs(self):
        roster.save_roster_entries([
            {"name": "Alice", "club": "CPC", "group": "ADMIN", "phone": "0102030405", "mail": "a@b.fr"},
        ])
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["group"], "ADMIN")
        self.assertEqual(entries["Alice"]["phone"], "0102030405")
        self.assertEqual(entries["Alice"]["mail"], "a@b.fr")

    def test_save_roster_entries_sans_ces_champs_les_met_a_vide(self):
        """Un appelant qui ne fournit pas group/phone/mail (ex. ancien
        code non mis à jour) obtient "" pour ces champs — jamais une
        KeyError, jamais une valeur héritée par erreur."""
        roster.save_roster_entries([{"name": "Alice", "club": "CPC"}])
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["group"], "")
        self.assertEqual(entries["Alice"]["phone"], "")
        self.assertEqual(entries["Alice"]["mail"], "")

    def test_save_roster_names_seuls_preserve_group_phone_mail_existants(self):
        """save_roster(names) (compatibilité, ne reçoit QUE des noms) ne
        doit jamais perdre group/phone/mail déjà connus pour un nom
        qu'elle conserve."""
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        roster.set_phone("Alice", "0102030405")
        roster.set_mail("Alice", "a@b.fr")

        roster.save_roster(["Alice", "Bob"])  # Bob nouveau, sans rien

        self.assertEqual(roster.get_group("Alice"), "ADMIN")
        self.assertEqual(roster.get_phone("Alice"), "0102030405")
        self.assertEqual(roster.get_mail("Alice"), "a@b.fr")
        self.assertEqual(roster.get_group("Bob"), "")

    def test_save_roster_retire_un_nom_perd_toutes_ses_informations(self):
        """Comportement déjà existant pour "club", confirmé inchangé
        pour group/phone/mail : un nom absent de la nouvelle liste est
        retiré avec TOUTES ses informations."""
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        roster.save_roster(["Bob"])  # Alice disparaît
        self.assertNotIn("Alice", roster.load_roster())


class RenameInRosterPreserveNouveauxChampsTest(RosterFieldsTestCase):
    def test_renommer_conserve_group_phone_mail(self):
        roster.set_group("Alice", roster.ROSTER_GROUP_DIRTO)
        roster.set_phone("Alice", "0102030405")
        roster.set_mail("Alice", "a@b.fr")

        roster.rename_in_roster("Alice", "Alicia")

        self.assertEqual(roster.get_group("Alicia"), "DIRTO")
        self.assertEqual(roster.get_phone("Alicia"), "0102030405")
        self.assertEqual(roster.get_mail("Alicia"), "a@b.fr")
        self.assertNotIn("Alice", roster.load_roster())

    def test_fusion_avec_nom_cible_existant_garde_les_valeurs_non_vides(self):
        roster.set_group("Alice", roster.ROSTER_GROUP_ADMIN)
        roster.set_mail("Bob", "bob@cpc.fr")  # "Bob" existe déjà, avec un mail

        roster.rename_in_roster("Alice", "Bob")  # Alice fusionne dans Bob

        self.assertEqual(roster.get_group("Bob"), "ADMIN")  # venait d'Alice
        self.assertEqual(roster.get_mail("Bob"), "bob@cpc.fr")  # préservé (Alice n'en avait pas)


class DuplicatesOnDiskMergeTest(RosterFieldsTestCase):
    """Doublons dans le fichier brut (cas déjà géré pour "club" avant ce
    chantier) : la première valeur NON VIDE rencontrée l'emporte, pour
    chacun des 3 nouveaux champs indépendamment."""

    def test_fusion_group_phone_mail_sur_doublon(self):
        self._write_raw([
            {"name": "Alice", "club": "", "group": "", "phone": "0102030405", "mail": ""},
            {"name": "Alice", "club": "CPC", "group": "ADMIN", "phone": "", "mail": "a@b.fr"},
        ])
        entries = {e["name"]: e for e in roster.load_roster_entries()}
        self.assertEqual(entries["Alice"]["club"], "CPC")
        self.assertEqual(entries["Alice"]["group"], "ADMIN")
        self.assertEqual(entries["Alice"]["phone"], "0102030405")  # 1er rencontré, non vide
        self.assertEqual(entries["Alice"]["mail"], "a@b.fr")


if __name__ == "__main__":
    unittest.main()
