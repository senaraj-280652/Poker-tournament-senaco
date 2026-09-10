# -*- coding: utf-8 -*-
"""Petit script auxiliaire, PAS un fichier de test lui-même (nom
volontairement sans le préfixe test_, donc jamais collecté par
`unittest discover`) : exécute UNE action isolée liée à "Calculer les
primes", dans un process Python tout neuf à chaque appel — utilisé par
tests/test_primes_multi_process_real_subprocess.py pour reproduire
fidèlement deux (ou plus) VRAIS processus séparés, chacun sans le
moindre état Python partagé avec les autres (contrairement à deux
simples objets dans le même process) : seul le disque (HOME redirigé
par l'appelant) fait le lien entre eux, exactement comme en conditions
réelles (voir spawn_app_process dans main.py).

Usage : python3 _subprocess_primes_worker.py <action> [args...]
Chaque action imprime son résultat sur stdout (une ligne), pour que
l'appelant (subprocess.run(..., capture_output=True)) puisse le lire
sans avoir à relire les fichiers lui-même — mais l'appelant peut aussi
les relire directement s'il préfère (même HOME, mêmes chemins)."""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database  # noqa: E402
import main  # noqa: E402
import open_windows  # noqa: E402


def _open_new(path):
    """Reproduit EXACTEMENT le stamping fait par main.py:
    _choose_tournament_file au moment de la création d'un tournoi
    flambant neuf (bloc `if result.get("is_new"):`) — câblage exact
    vérifié séparément par tests/test_primes_enabled_toggle.py:
    WiringStructurelTest.test_choose_tournament_file_stampe_les_
    nouveaux_tournois. Registered AVANT de stamper, EXACTEMENT comme
    l'ordre réel main.py (_choose_tournament_file, PUIS open_windows.
    register(self.db.path) dans App.__init__) : stampe d'abord (pendant
    que ce process n'est pas encore dans le registre), enregistre
    ensuite."""
    db = database.Database(path)
    db.set_setting("primes_enabled", "1" if main._primes_enabled_proposed() else "0")
    open_windows.register(path)
    db.conn.close()


def _open_existing(path):
    """Reproduit EXACTEMENT le chemin d'ouverture d'un fichier .tournoi
    DÉJÀ EXISTANT (voir App.__init__, MÊME ORDRE) : Database(path) ->
    open_windows.register(path) -> _align_primes_enabled_on_open(db) —
    demande du 2026-09-09 (4e relecture, "ouvrir un tournoi EXISTANT
    après verrouillage garde à tort son ancienne valeur")."""
    db = database.Database(path)
    open_windows.register(path)
    main._align_primes_enabled_on_open(db)
    db.conn.close()


def _hold_open(path, is_new):
    """Représente UNE fenêtre de tournoi RÉELLEMENT ouverte, pour toute
    la durée où ce process reste vivant — exactement comme un vrai
    process App (voir spawn_app_process/App.__init__) : le registre
    open_windows.json lie une entrée au PID du process qui l'a ouverte
    (_pid_is_running), donc un simple "register puis sortir aussitôt"
    (comme le ferait un sous-process jetable) ne représente PAS
    fidèlement une fenêtre restée ouverte — son entrée serait aussitôt
    purgée (_prune) par le TOUT PROCHAIN appel de n'importe quel autre
    process, PID mort. Ce mode reste donc vivant, en train d'attendre
    une instruction sur stdin, jusqu'à recevoir "close" (ou EOF) :
    désenregistre alors PROPREMENT (unregister, depuis CE MÊME process,
    seul habilité à le faire — voir sa docstring) avant de quitter,
    exactement comme App._cleanup_for_close à la fermeture d'une vraie
    fenêtre."""
    if is_new:
        _open_new(path)
    else:
        _open_existing(path)
    print("ready", flush=True)
    for line in sys.stdin:
        if line.strip() == "close":
            break
    open_windows.unregister(path)
    print("closed", flush=True)


def main_cli():
    action = sys.argv[1]
    args = sys.argv[2:]

    if action == "hold_open":
        _hold_open(args[0], is_new=("--new" in args[1:]))

    elif action == "open_new":
        # Court-vécu (contrairement à "hold_open --new") : suffisant
        # pour vérifier UNIQUEMENT la valeur de stamping d'un tournoi
        # flambant neuf, sans avoir besoin qu'il "reste ouvert" pour la
        # suite du scénario (voir la fin de test_scenario_deux_vrais_
        # process_puis_nouvelle_session : C n'a besoin d'être vérifié
        # qu'une fois, pas d'y faire converger un autre tournoi après).
        _open_new(args[0])
        print("ok")

    elif action == "open_existing":
        # Court-vécu (contrairement à "hold_open <path>", sans --new) :
        # suffisant pour vérifier l'alignement d'un tournoi EXISTANT à
        # l'ouverture, sans avoir besoin qu'il "reste ouvert" ensuite.
        _open_existing(args[0])
        print("ok")

    elif action == "toggle_unchecked":
        # Simule un clic sur "Calculer les primes" (décochée) depuis LA
        # fenêtre du tournoi `args[0]` : même effet que App._on_primes_
        # enabled_toggle (propage au global ET à sa propre copie locale).
        path = args[0]
        db = database.Database(path)
        main._set_primes_enabled_proposed(False)
        db.set_setting("primes_enabled", "0")
        db.conn.close()
        print("ok")

    elif action == "toggle_checked":
        path = args[0]
        db = database.Database(path)
        main._set_primes_enabled_proposed(True)
        db.set_setting("primes_enabled", "1")
        db.conn.close()
        print("ok")

    elif action == "start_tournament":
        # Simule App._clock_resume au tout premier démarrage (voir
        # main.py, MÊME ORDRE) : resynchronisation défensive,
        # clock_started=1, puis verrouillage de la session à la valeur
        # de CE tournoi précis.
        path = args[0]
        db = database.Database(path)
        main._sync_primes_enabled_pref(db)
        db.set_settings({"clock_started": 1})
        open_windows.mark_primes_session_started(db.get_setting_int("primes_enabled", 1) == 1)
        db.conn.close()
        print("ok")

    elif action == "tick_and_read":
        # Simule un tick (voir App._tick) : fait converger la copie
        # locale de `args[0]` vers la valeur globale proposée, PUIS
        # affiche cette copie locale (comme le ferait la case à cocher
        # de cette fenêtre après _sync_primes_enabled_checkbox).
        path = args[0]
        db = database.Database(path)
        main._sync_primes_enabled_pref(db)
        print(db.get_setting("primes_enabled"))
        db.conn.close()

    elif action == "read_local":
        path = args[0]
        db = database.Database(path)
        print(db.get_setting("primes_enabled"))
        db.conn.close()

    elif action == "read_proposed":
        print("1" if main._primes_enabled_proposed() else "0")

    elif action == "list_open":
        print(",".join(open_windows.list_open_paths()))

    else:
        raise SystemExit(f"action inconnue : {action!r}")


if __name__ == "__main__":
    main_cli()
