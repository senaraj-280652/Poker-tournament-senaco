# -*- coding: utf-8 -*-
"""Fenêtre « Licence Senaco » (bouton sur l'écran Bienvenue/Menu
principal, juste avant « À propos », demande du 2026-11-05) :
- Tests structurels : emplacement exact du bouton (ordre préservé,
  jamais ajouté aux menus Aide).
- Tests sur une VRAIE racine Tk (jamais un double) : la fenêtre
  s'affiche correctement, en ligne et hors ligne, V3 activée et
  désactivée, sans jamais déclencher de vérification réseau."""
import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import main  # noqa: E402
import license_client  # noqa: E402

try:
    import tkinter as tk
    from tkinter import ttk
    _root_probe = tk.Tk()
    _root_probe.destroy()
    _TK_AVAILABLE = True
except Exception:
    _TK_AVAILABLE = False

from _tk_cleanup import cleanup_tk  # noqa: E402


def _lire_main():
    chemin = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")
    with open(chemin, "r", encoding="utf-8") as f:
        return f.read()


def _lire_ecran_bienvenue():
    """Isole le corps de App._choose_tournament_file (l'écran
    "Bienvenue", c'est-à-dire le "Menu principal" au sens de la
    demande) — jamais tout le fichier, pour ne jamais confondre un
    bouton de cet écran avec un libellé similaire utilisé ailleurs
    dans main.py (ex. un autre bouton "Sit & Go")."""
    contenu = _lire_main()
    debut = contenu.index("    def _choose_tournament_file(self):")
    fin = contenu.index("    def _apply_sng_defaults(self, n_players):")
    return contenu[debut:fin]


class EmplacementStructurelTest(unittest.TestCase):
    """Emplacement validé : UNIQUEMENT sur l'écran Bienvenue, juste
    avant le bouton « À propos », jamais dans les menus Aide, sans
    changer l'ordre des autres boutons."""

    def test_bouton_licence_juste_avant_a_propos_sur_lecran_bienvenue(self):
        ecran = _lire_ecran_bienvenue()
        motif_bouton_licence = 'text="🪪  Licence", command=lambda: self._show_licence_window(win)'
        motif_bouton_a_propos = 'text="ℹ️  À propos", command=lambda: self._show_about(win)'
        self.assertIn(motif_bouton_licence, ecran)
        self.assertIn(motif_bouton_a_propos, ecran)
        # Rien d'autre entre les deux (une courte distance fixe —
        # fermeture du widget Licence puis ouverture immédiate du
        # widget À propos — confirme "juste avant", pas "quelque part
        # avant" ; un autre bouton inséré entre les deux allongerait
        # nettement cet écart).
        position_licence = ecran.index(motif_bouton_licence) + len(motif_bouton_licence)
        position_a_propos = ecran.index(motif_bouton_a_propos)
        self.assertLess(
            position_a_propos - position_licence, 80,
            "Un autre élément semble avoir été inséré entre les boutons Licence et À propos.",
        )

    def test_jamais_ajoute_aux_menus_aide(self):
        contenu = _lire_main()
        # Les deux constructions du menu "Aide" (barre réduite et barre
        # complète) ne doivent contenir aucune entrée "Licence" — seul
        # l'écran Bienvenue a été modifié, jamais les menus natifs.
        debut = 0
        trouvees = 0
        while True:
            position = contenu.find('helpmenu = tk.Menu(menubar', debut)
            if position == -1:
                break
            fin_bloc = contenu.find('menubar.add_cascade(label="Aide"', position)
            bloc = contenu[position:fin_bloc]
            self.assertNotIn("Licence", bloc)
            trouvees += 1
            debut = fin_bloc + 1
        self.assertEqual(trouvees, 2, "Les deux constructions du menu Aide doivent toujours exister, inchangées.")

    def test_ordre_des_autres_boutons_de_lecran_bienvenue_inchange(self):
        contenu = _lire_ecran_bienvenue()
        ordre_attendu = [
            '"🖥️  Ouvrir Senaco', '"🆕  Nouveau tournoi', '"🚀  Sit & Go',
            '"📂  Ouvrir un tournoi existant', '"📋  Lobby', '"💾  Sauvegarder sur clé USB',
            '"♻️  Restaurer depuis une clé USB', '"🗑️  Purger les données',
            '"⚙️  Paramètres de base', '"🪪  Licence', '"ℹ️  À propos',
        ]
        positions = []
        for motif in ordre_attendu:
            position = contenu.find(motif)
            self.assertNotEqual(position, -1, f"Bouton introuvable : {motif}")
            positions.append(position)
        self.assertEqual(positions, sorted(positions), "L'ordre des boutons de l'écran Bienvenue a changé.")


class _FakeApp:
    def __init__(self, decision=None):
        self._decision_licence_v3 = decision


@unittest.skipUnless(_TK_AVAILABLE, "Tkinter indisponible sur ce poste")
class FenetreLicenceReelleTest(unittest.TestCase):
    """Vraie fenêtre Tk (jamais une doublure) — confirme qu'elle se
    construit sans erreur, affiche les bons libellés, et ne déclenche
    jamais d'appel réseau."""

    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(lambda: cleanup_tk(self, "root"))

    def _texte_de_la_fenetre(self):
        toplevels = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertTrue(toplevels, "La fenêtre Licence Senaco doit avoir été créée.")
        fenetre = toplevels[-1]
        textes = []

        def _collecter(widget):
            try:
                textes.append(str(widget.cget("text")))
            except tk.TclError:
                pass
            for enfant in widget.winfo_children():
                _collecter(enfant)

        _collecter(fenetre)
        fenetre.destroy()
        return textes

    def _ouvrir(self, fake_app):
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            main.App._show_licence_window(fake_app, self.root)
        return self._texte_de_la_fenetre()

    def _fenetre_sans_la_detruire(self, fake_app):
        """Comme _ouvrir, mais laisse la fenêtre ouverte pour permettre
        une inspection de sa configuration (pack_info(), cget()).

        JAMAIS update_idletasks()/update() ici : voir
        tests/test_bienvenue_window_height.py (docstring de
        _reqheight_with_pady) — dans ce harnais précis (Python
        3.14/Tcl-Tk/macOS), ces appels provoquent un SIGSEGV
        reproductible à 100 % dès qu'un Toplevel ne pend pas du tout
        premier tk.Tk() du processus (systématiquement le cas lors
        d'un `discover`). Incident réel reproduit le 2026-11-04 avec
        une version antérieure de ce test — jamais à réintroduire."""
        with patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
            main.App._show_licence_window(fake_app, self.root)
        toplevels = [w for w in self.root.winfo_children() if isinstance(w, tk.Toplevel)]
        self.assertTrue(toplevels, "La fenêtre Licence Senaco doit avoir été créée.")
        return toplevels[-1]

    def _trouver_rangee(self, fenetre, libelle_attendu):
        """Retrouve, dans une fenêtre déjà construite, le couple
        (libellé, valeur) d'une rangée produite par le helper interne
        ligne() — identifié par le texte exact du libellé — afin de
        vérifier leur position l'un par rapport à l'autre."""
        corps = fenetre.winfo_children()[0]
        for rangee in corps.winfo_children():
            labels = [w for w in rangee.winfo_children() if isinstance(w, ttk.Label)]
            if len(labels) == 2 and str(labels[0].cget("text")) == libelle_attendu:
                return labels[0], labels[1]
        raise AssertionError(f"Rangée introuvable pour le libellé {libelle_attendu!r}")

    def test_v3_desactivee_jamais_activee_affiche_non_activee(self):
        fake_app = _FakeApp()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(main.POKER_TOURNAMENT_LICENSE_V3, None)
            with patch("export_prefs.load_value", return_value="Club Test"), \
                    patch("license.license_info", return_value=None):
                textes = self._ouvrir(fake_app)
        self.assertIn("Club Test", textes)
        self.assertIn("Non activée", textes)
        self.assertTrue(any("v3" not in t.lower() and "jeton" not in t.lower() for t in textes))
        self.assertFalse(any("v3" in t.lower() for t in textes), "Aucune terminologie technique v3 ne doit apparaître.")

    def test_v3_desactivee_avec_ancien_systeme_actif_affiche_active(self):
        fake_app = _FakeApp()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(main.POKER_TOURNAMENT_LICENSE_V3, None)
            with patch("export_prefs.load_value", return_value="Club Historique"), \
                    patch("license.license_info", return_value={"club_name": "Club Historique", "machine_id": "AAAA-0000"}):
                textes = self._ouvrir(fake_app)
        self.assertIn("Club Historique", textes)
        self.assertIn("Licence active sur cet ordinateur", textes)

    def test_valeur_immediatement_apres_son_libelle_pas_a_lextreme_droite(self):
        """Demande du 2026-11-04 : "CPC" et "Non activée" doivent
        apparaître juste après "Club :" et "Statut :", avec un
        espacement régulier et réduit — jamais plaqués à l'extrémité
        droite de la fenêtre (ancien bug : pack(side="right") sur une
        rangée fill="x", large comme la ligne la plus longue).

        Vérification PAR CONFIGURATION (pack_info()), jamais par une
        mesure réelle (winfo_rootx()/update_idletasks()) — voir
        l'avertissement dans _fenetre_sans_la_detruire ci-dessus."""
        fake_app = _FakeApp()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(main.POKER_TOURNAMENT_LICENSE_V3, None)
            with patch("export_prefs.load_value", return_value="CPC"), \
                    patch("license.license_info", return_value=None):
                fenetre = self._fenetre_sans_la_detruire(fake_app)
        for libelle in ("Club :", "Statut :"):
            with self.subTest(libelle=libelle):
                champ_valeur = self._trouver_rangee(fenetre, libelle)[1]
                info = champ_valeur.pack_info()
                self.assertEqual(
                    info.get("side"), "left",
                    "La valeur doit être empaquetée à GAUCHE, juste après son "
                    "libellé — pas à droite (ancien bug : valeur plaquée à "
                    "l'extrémité droite de la fenêtre).",
                )
                padx = info.get("padx")
                # pack_info() peut renvoyer soit un entier simple, soit une
                # paire "gauche droite" selon la plateforme/version Tcl —
                # seule la valeur de gauche sépare le libellé de sa valeur.
                if isinstance(padx, (tuple, list)):
                    ecart = int(padx[0])
                else:
                    ecart = int(str(padx).split()[0])
                self.assertLessEqual(
                    ecart, 10,
                    "L'espacement entre le libellé et sa valeur doit rester "
                    "réduit et régulier, pas un grand écart poussant la "
                    "valeur vers la droite.",
                )
        fenetre.destroy()

    def test_v3_activee_sans_jeton_local_affiche_non_activee(self):
        fake_app = _FakeApp()
        with patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"}):
            with patch("export_prefs.load_value", return_value="Club Test"), \
                    patch.object(license_client, "obtenir_informations_affichage",
                                 return_value=license_client.InformationsLicenceAffichage(disponible=False)) as mock_infos:
                textes = self._ouvrir(fake_app)
        mock_infos.assert_called_once()
        self.assertIn("Non activée", textes)

    def test_v3_activee_licence_essai_active_dates_et_sans_tolerance_trompeuse(self):
        """Scénario 1 (demande du 2026-11-04, à valider avant commit) :
        licence active, type essai gratuit — dates d'activation et
        d'expiration affichées, et AUCUNE indication de tolérance (la
        tolérance ne concerne que les licences expirées ; en afficher
        une pour une licence active serait trompeur)."""
        fake_app = _FakeApp(decision=license_client.DecisionLicence(license_client.ETAT_NORMAL, "cycle-1", ""))
        infos = license_client.InformationsLicenceAffichage(
            disponible=True, type="essai", etat=license_client.ETAT_NORMAL,
            date_debut="2026-10-01", date_expiration="2027-01-29", a_vie=False,
            tolerance_applicable=False, tolerance_utilisees=0, tolerance_max=2,
            derniere_confirmation_en_ligne=datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc),
        )
        with patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"}):
            with patch("export_prefs.load_value", return_value="Club Essai"), \
                    patch.object(license_client, "obtenir_informations_affichage", return_value=infos):
                textes = self._ouvrir(fake_app)
        self.assertIn("Essai gratuit", textes)
        self.assertIn("Licence active", textes)
        self.assertIn("01/10/2026", textes)
        self.assertIn("29/01/2027", textes)
        self.assertIn("Sans objet (licence active)", textes)
        self.assertFalse(
            any("journée" in t for t in textes),
            "Aucun compteur de tolérance ne doit être affiché pour une licence active (information trompeuse).",
        )

    def test_v3_activee_licence_expiree_tolerance_toutes_valeurs_distinctes(self):
        """Scénario 2 (demande du 2026-11-04) : les trois états de
        tolérance possibles (0/2, 1/2, 2/2) doivent être clairement
        distingués, avec le nombre de jours restants correctement
        calculé pour chacun."""
        cas = [
            (0, 2, "0 journée utilisée sur 2 — 2 journées restantes"),
            (1, 2, "1 journée utilisée sur 2 — 1 journée restante"),
            (2, 2, "2 journées utilisées sur 2 — Aucune journée restante"),
        ]
        for utilisees, maximum, texte_attendu in cas:
            with self.subTest(utilisees=utilisees, maximum=maximum):
                fake_app = _FakeApp(decision=license_client.DecisionLicence(license_client.ETAT_EXPIRE, "cycle-1", ""))
                infos = license_client.InformationsLicenceAffichage(
                    disponible=True, type="essai", etat=license_client.ETAT_EXPIRE,
                    date_debut="2026-08-01", date_expiration="2026-11-01", a_vie=False,
                    tolerance_applicable=True, tolerance_utilisees=utilisees, tolerance_max=maximum,
                    derniere_confirmation_en_ligne=None,
                )
                with patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"}):
                    with patch("export_prefs.load_value", return_value="Club Expire"), \
                            patch.object(license_client, "obtenir_informations_affichage", return_value=infos):
                        textes = self._ouvrir(fake_app)
                self.assertIn("Licence expirée", textes)
                self.assertIn("01/11/2026", textes)
                self.assertIn(texte_attendu, textes)

    def test_v3_activee_licence_offerte_a_vie(self):
        fake_app = _FakeApp(decision=license_client.DecisionLicence(license_client.ETAT_NORMAL, "cycle-1", ""))
        infos = license_client.InformationsLicenceAffichage(
            disponible=True, type="offerte", etat=license_client.ETAT_NORMAL,
            date_debut="2026-01-15", date_expiration="9999-12-31", a_vie=True,
            tolerance_applicable=False, tolerance_utilisees=0, tolerance_max=2,
            derniere_confirmation_en_ligne=datetime(2026, 11, 4, 14, 2, tzinfo=timezone.utc),
        )
        with patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"}):
            with patch("export_prefs.load_value", return_value="Club Offert"), \
                    patch.object(license_client, "obtenir_informations_affichage", return_value=infos):
                textes = self._ouvrir(fake_app)
        self.assertIn("Club Offert", textes)
        self.assertIn("Licence offerte par Senaco", textes)
        self.assertIn("Licence active", textes)
        self.assertIn("À vie", textes)
        self.assertFalse(any("9999" in t for t in textes), "La date technique ne doit jamais être affichée.")
        self.assertIn("Sans objet (licence active)", textes)
        self.assertTrue(any("Confirmée en ligne le 04/11/2026" in t for t in textes))
        # Scénario 3 (demande du 2026-11-04) : aucun message de
        # renouvellement payant ne doit apparaître pour une licence
        # offerte à vie — ce lot n'ajoute d'ailleurs aucun bouton de
        # paiement/renouvellement dans cette fenêtre, quel que soit
        # l'état affiché ; ce test protège ce principe dans le temps.
        mots_interdits = ("renouvel", "payer", "paiement", "€", "tarif", "abonnement")
        self.assertFalse(
            any(mot in t.lower() for t in textes for mot in mots_interdits),
            "Aucun message de renouvellement payant ne doit apparaître pour une licence offerte à vie.",
        )

    def test_v3_activee_licence_expiree_avec_tolerance(self):
        fake_app = _FakeApp(decision=license_client.DecisionLicence(license_client.ETAT_EXPIRE, "cycle-1", ""))
        infos = license_client.InformationsLicenceAffichage(
            disponible=True, type="essai", etat=license_client.ETAT_EXPIRE,
            date_debut="2026-08-01", date_expiration="2026-11-01", a_vie=False,
            tolerance_applicable=True, tolerance_utilisees=1, tolerance_max=2,
            derniere_confirmation_en_ligne=None,
        )
        with patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"}):
            with patch("export_prefs.load_value", return_value="Club Expire"), \
                    patch.object(license_client, "obtenir_informations_affichage", return_value=infos):
                textes = self._ouvrir(fake_app)
        self.assertIn("Essai gratuit", textes)
        self.assertIn("Licence expirée", textes)
        self.assertIn("01/08/2026", textes)
        self.assertIn("01/11/2026", textes)
        self.assertIn("1 journée utilisée sur 2 — 1 journée restante", textes)
        self.assertTrue(any("Jamais confirmée en ligne" in t for t in textes))

    def test_ouverture_ne_declenche_jamais_dappel_reseau(self):
        fake_app = _FakeApp(decision=license_client.DecisionLicence(license_client.ETAT_NORMAL, "cycle-1", ""))
        with patch.dict(os.environ, {main.POKER_TOURNAMENT_LICENSE_V3: "1"}):
            with patch("export_prefs.load_value", return_value="Club Test"), \
                    patch.object(license_client, "_post_json") as mock_post, \
                    patch.object(tk.Toplevel, "wait_window", lambda self, w=None: None):
                main.App._show_licence_window(fake_app, self.root)
        mock_post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
