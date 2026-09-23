# -*- coding: utf-8 -*-
"""Nettoyage centralisé des racines Tk réelles utilisées par la suite de
tests (demande du 2026-09-19, chantier "crash Tcl/Tk" — voir le
diagnostic livré séparément dans la conversation).

Diagnostic résumé : `root.destroy()` ne détruit que le côté Tcl d'un
widget/d'une racine — jamais les objets Python (Variable, widgets,
méthodes greffées) qui les référencent encore. Quand ces objets sont
pris dans un cycle de références (méthode liée `self.win.meth =
types.MethodType(..., self.win)`, `Variable.trace_add(...)` fermé sur
son propriétaire, `Tooltip`/`bind()` mutuellement référencés — tous
recensés dans les 23 fichiers de tests créant une vraie racine Tk),
seul le ramasse-miettes CYCLIQUE peut les réclamer — jamais le simple
comptage de références. Ce passage peut s'exécuter sur N'IMPORTE QUEL
thread Python (déclenché par les seuils d'allocation, pas seulement
par le thread principal), y compris un `process_request_thread` HTTP
d'un tout autre test `RemoteControlServer` — la finalisation Tcl qui
en résulte (`Variable.__del__`, `Tkapp_Dealloc`) se produit alors
depuis le mauvais thread et fait planter l'interpréteur
("Tcl_AsyncDelete: async handler deleted by the wrong thread").

`cleanup_tk` ci-dessous : détruit chaque objet Tk réel exactement une
fois (déduplique par identité — utile quand `self.win is self.root`,
un alias très répandu dans cette suite), supprime ENSUITE les
références Python de haut niveau nommées (racine, alias, widgets,
Variables, méthodes greffées — tout ce que l'appelant fournit), puis
déclenche `gc.collect()` sur CE thread : comme cette fonction n'est
jamais appelée que depuis le thread principal (tearDown/tearDownClass/
addCleanup d'un test), la collecte des cycles ainsi rendus déchets a
lieu — et donc la finalisation Tcl qu'elle entraîne éventuellement —
au bon endroit, avant qu'un test ultérieur (HTTP ou autre) ne puisse
en hériter par hasard.

N'AVALE JAMAIS une exception réelle de destroy() : seul le cas
explicitement attendu et inoffensif d'un widget déjà détruit (par la
cascade du destroy() d'un parent listé plus tôt dans le même appel)
est ignoré, jamais un vrai problème Tk — voir _still_exists ci-dessous,
dont le rôle est uniquement de détecter "déjà parti", jamais de
masquer un échec de destroy() lui-même (destroy() est toujours appelé
sans aucun try/except autour)."""
import gc


def _still_exists(obj):
    """True si `obj` (widget/racine Tk) a un `winfo_exists()` accessible
    et renvoie vrai — False s'il n'a pas cette méthode (n'est pas un
    widget Tk), si `winfo_exists()` renvoie faux, ou si l'appeler lève
    (racine déjà entièrement détruite, plus d'interpréteur Tcl
    derrière) : dans ce dernier cas, "déjà parti" est la seule
    interprétation possible, jamais un problème à faire remonter."""
    winfo_exists = getattr(obj, "winfo_exists", None)
    if winfo_exists is None:
        return True  # pas un widget Tk (ex. simple objet) : on tente destroy() normalement
    try:
        return bool(winfo_exists())
    except Exception:
        return False


def cleanup_tk(obj, *attr_names):
    """Détruit puis dé-référence les objets Tk nommés par `attr_names`
    sur `obj` (une instance de TestCase dans un tearDown/addCleanup, ou
    la CLASSE elle-même dans un tearDownClass), puis force gc.collect()
    sur le thread appelant.

    `attr_names` doit lister TOUS les attributs concernés : la racine
    elle-même, ses éventuels alias (ex. "win" quand self.win is
    self.root), et tout widget/Variable/méthode greffée conservé comme
    attribut séparé (ex. "settings_vars", "_pending_rebalance_frame",
    "test_mode_var", "tooltip"...) — voir le diagnostic en tête de
    fichier : nommer ces attributs explicitement ici est ce qui
    garantit qu'AUCUNE référence volontaire ne subsiste au moment de
    gc.collect(), plutôt que de deviner ou de parcourir __dict__.

    Chaque objet destructible (a un attribut `destroy`) n'est détruit
    qu'UNE SEULE FOIS même s'il est nommé par plusieurs attr_names
    (déduplication par identité) ou déjà détruit par la cascade d'un
    parent listé plus tôt — jamais deux fois, jamais une exception
    réelle de destroy() avalée."""
    seen_ids = set()
    to_destroy = []
    for name in attr_names:
        value = getattr(obj, name, None)
        if value is None:
            continue
        if id(value) in seen_ids:
            continue
        seen_ids.add(id(value))
        if hasattr(value, "destroy"):
            to_destroy.append(value)

    for value in to_destroy:
        if _still_exists(value):
            value.destroy()

    for name in attr_names:
        if hasattr(obj, name):
            setattr(obj, name, None)

    gc.collect()
