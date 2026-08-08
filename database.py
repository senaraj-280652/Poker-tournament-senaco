# -*- coding: utf-8 -*-
"""
Couche d'accès aux données pour le gestionnaire de tournoi de poker.
Toutes les données d'un tournoi sont stockées dans un seul fichier SQLite.
"""
import sqlite3
import time
import math
import os
import glob

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS tables_pk (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    max_seats INTEGER NOT NULL DEFAULT 9,
    is_active INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    buyin_count INTEGER NOT NULL DEFAULT 1,
    rebuy_count INTEGER NOT NULL DEFAULT 0,
    addon_count INTEGER NOT NULL DEFAULT 0,
    chips INTEGER NOT NULL DEFAULT 0,
    table_id INTEGER,
    seat INTEGER,
    status TEXT NOT NULL DEFAULT 'active',   -- active | eliminated | withdrawn
    place INTEGER,
    elim_time TEXT,
    bounty INTEGER NOT NULL DEFAULT 0,       -- prime actuellement portée par ce joueur
    bounty_won INTEGER NOT NULL DEFAULT 0,   -- cumul des primes empochées (en cash)
    FOREIGN KEY(table_id) REFERENCES tables_pk(id)
);

CREATE TABLE IF NOT EXISTS blind_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level_order INTEGER NOT NULL,
    small_blind INTEGER NOT NULL,
    big_blind INTEGER NOT NULL,
    ante INTEGER NOT NULL DEFAULT 0,
    duration_minutes INTEGER NOT NULL,
    is_break INTEGER NOT NULL DEFAULT 0,
    break_label TEXT
);

CREATE TABLE IF NOT EXISTS payout_structure (
    place INTEGER PRIMARY KEY,
    percentage REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seat_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    player_name TEXT NOT NULL,
    old_table_name TEXT,
    old_seat INTEGER,
    new_table_name TEXT,
    new_seat INTEGER,
    moved_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bounty_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    eliminated_name TEXT NOT NULL,
    eliminator_name TEXT,
    amount_won INTEGER NOT NULL,
    added_to_eliminator_bounty INTEGER NOT NULL DEFAULT 0,
    event_time TEXT NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "tournament_name": "Nouveau tournoi",
    "buyin_amount": "50",
    "rebuy_amount": "50",
    "addon_amount": "50",
    "starting_chips": "10000",
    "rebuy_chips": "10000",
    "addon_chips": "10000",
    "max_seats_per_table": "9",
    "min_players_per_table": "4",
    "break_duration_minutes": "15",
    "movement_signal_frequency_hz": "880",
    "movement_signal_duration_ms": "300",
    "highlight_duration_minutes": "5",
    "rake_percent": "0",
    "bounty_amount": "0",
    "pko_mode": "0",
    "pko_cash_percent": "50",
    "current_level_order": "1",
    "level_start_epoch": "0",
    "is_paused": "1",
    "paused_accum_seconds": "0",
    "clock_started": "0",
    "tournament_date": "",  # AAAA-MM-JJ, fixée à la création (voir get_tournament_date)
}


class Database:
    def __init__(self, path):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self._migrate()
        self._init_defaults()
        self.conn.commit()

    def _migrate(self):
        """Ajoute les colonnes apparues après la création initiale du
        fichier .tournoi (les anciens fichiers n'ont pas 'bounty' /
        'bounty_won' sur la table players)."""
        cols = {row["name"] for row in self.conn.execute("PRAGMA table_info(players)")}
        if "bounty" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN bounty INTEGER NOT NULL DEFAULT 0")
        if "bounty_won" not in cols:
            self.conn.execute("ALTER TABLE players ADD COLUMN bounty_won INTEGER NOT NULL DEFAULT 0")

    # ---------- init ----------
    def _init_defaults(self):
        cur = self.conn.cursor()
        for k, v in DEFAULT_SETTINGS.items():
            cur.execute(
                "INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v)
            )
        cur.execute("SELECT COUNT(*) c FROM blind_levels")
        if cur.fetchone()["c"] == 0:
            from structures import default_blind_structure
            self.set_blind_structure(default_blind_structure())
        cur.execute("SELECT COUNT(*) c FROM payout_structure")
        if cur.fetchone()["c"] == 0:
            self.set_payout_structure({1: 100.0})
        cur.execute("SELECT COUNT(*) c FROM tables_pk")
        if cur.fetchone()["c"] == 0:
            self.add_table("Table 1")

    # ---------- settings ----------
    def get_setting(self, key, default=None):
        row = self.conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else default

    def get_setting_int(self, key, default=0):
        v = self.get_setting(key)
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return default

    def get_setting_float(self, key, default=0.0):
        v = self.get_setting(key)
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    def set_setting(self, key, value):
        self.conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )
        self.conn.commit()

    def get_tournament_date(self):
        """Date du tournoi (AAAA-MM-JJ), utilisée pour les synthèses par
        période. C'est la date fixée à la création du tournoi si elle est
        connue ; sinon (fichiers créés avant l'existence de ce paramètre),
        on retombe sur la date de création du fichier .tournoi lui-même
        (st_birthtime si disponible - macOS/BSD -, sinon la date de
        dernière modification). On évite volontairement de se baser sur
        la date de modification quand la vraie date de création est
        connue : le simple fait d'ouvrir un ancien fichier (migrations de
        réglages) le "touche" et modifierait sinon sa date à chaque
        utilisation."""
        d = self.get_setting("tournament_date", "")
        if d:
            return d
        try:
            st = os.stat(self.path)
            ts = getattr(st, "st_birthtime", None)
            if ts is None:
                ts = st.st_mtime
            return time.strftime("%Y-%m-%d", time.localtime(ts))
        except OSError:
            return ""

    def set_settings(self, mapping):
        for k, v in mapping.items():
            self.conn.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (k, str(v)),
            )
        self.conn.commit()

    # ---------- tables ----------
    def set_all_tables_max_seats(self, new_max):
        """Applique un nouveau nombre de sièges par table à TOUTES les
        tables existantes (actives ou fermées), pour que le changement du
        paramètre prenne effet immédiatement, y compris en cours de
        tournoi. À appeler suivi d'un rebalance_tables()."""
        new_max = max(2, int(new_max))
        self.conn.execute("UPDATE tables_pk SET max_seats=?", (new_max,))
        self.conn.commit()

    def add_table(self, name=None):
        max_seats = self.get_setting_int("max_seats_per_table", 9)
        if name is None:
            # Compte TOUTES les tables jamais créées (actives ou fermées) :
            # se baser uniquement sur les tables actives ferait repartir la
            # numérotation en arrière après une fermeture, et créerait des
            # doublons de nom (ex : "Table 2" utilisé deux fois).
            n = self.conn.execute("SELECT COUNT(*) c FROM tables_pk").fetchone()["c"]
            name = f"Table {n + 1}"
        cur = self.conn.execute(
            "INSERT INTO tables_pk(name, max_seats, is_active) VALUES (?, ?, 1)",
            (name, max_seats),
        )
        self.conn.commit()
        return cur.lastrowid

    def list_tables(self, active_only=True):
        q = "SELECT * FROM tables_pk"
        if active_only:
            q += " WHERE is_active=1"
        q += " ORDER BY id"
        return self.conn.execute(q).fetchall()

    def close_table(self, table_id):
        self.conn.execute(
            "UPDATE tables_pk SET is_active=0 WHERE id=?", (table_id,)
        )
        self.conn.commit()

    # ---------- players ----------
    def list_players(self, status=None):
        q = "SELECT * FROM players"
        params = ()
        if status:
            q += " WHERE status=?"
            params = (status,)
        q += " ORDER BY table_id, seat"
        return self.conn.execute(q, params).fetchall()

    def get_player(self, player_id):
        return self.conn.execute(
            "SELECT * FROM players WHERE id=?", (player_id,)
        ).fetchone()

    def add_player(self, name):
        starting_chips = self.get_setting_int("starting_chips", 10000)
        bounty_amount = self.get_setting_int("bounty_amount", 0)
        cur = self.conn.execute(
            "INSERT INTO players(name, buyin_count, rebuy_count, addon_count, "
            "chips, status, bounty) VALUES (?, 1, 0, 0, ?, 'active', ?)",
            (name, starting_chips, bounty_amount),
        )
        player_id = cur.lastrowid
        self.conn.commit()
        self._seat_player(player_id)
        self.rebalance_tables(record_moves=False)
        return player_id

    def rebuy_player(self, player_id):
        chips = self.get_setting_int("rebuy_chips", 10000)
        bounty_amount = self.get_setting_int("bounty_amount", 0)
        self.conn.execute(
            "UPDATE players SET rebuy_count = rebuy_count + 1, "
            "chips = chips + ?, bounty = bounty + ? WHERE id=?",
            (chips, bounty_amount, player_id),
        )
        self.conn.commit()

    def addon_player(self, player_id):
        chips = self.get_setting_int("addon_chips", 10000)
        self.conn.execute(
            "UPDATE players SET addon_count = addon_count + 1, "
            "chips = chips + ? WHERE id=?",
            (chips, player_id),
        )
        self.conn.commit()

    def rename_player(self, player_id, new_name):
        new_name = new_name.strip()
        if not new_name:
            return
        self.conn.execute(
            "UPDATE players SET name=? WHERE id=?", (new_name, player_id)
        )
        self.conn.commit()

    def set_purchase_counts(self, player_id, buyin_count, rebuy_count, addon_count):
        """Corrige manuellement les compteurs buy-in / rebuy / add-on d'un
        joueur (utile en cas d'erreur de saisie), sans toucher aux chips."""
        self.conn.execute(
            "UPDATE players SET buyin_count=?, rebuy_count=?, addon_count=? WHERE id=?",
            (max(0, int(buyin_count)), max(0, int(rebuy_count)), max(0, int(addon_count)), player_id),
        )
        self.conn.commit()

    def set_chips(self, player_id, chips):
        self.conn.execute(
            "UPDATE players SET chips=? WHERE id=?", (max(0, int(chips)), player_id)
        )
        self.conn.commit()

    def eliminate_player(self, player_id, eliminated_by_id=None):
        """Élimine un joueur. Si `eliminated_by_id` est fourni et que le
        joueur éliminé porte une prime (bounty), celle-ci est versée à
        l'éliminateur : intégralement en mode classique, ou selon le
        partage PKO (une partie en cash immédiat, le reste ajouté à la
        prime de l'éliminateur) en mode progressif."""
        active = self.list_players(status="active")
        place = len(active)  # ce joueur prend la place n° (nb d'actifs restants)
        eliminated = self.get_player(player_id)
        now = time.strftime("%Y-%m-%d %H:%M:%S")

        self.conn.execute(
            "UPDATE players SET status='eliminated', place=?, elim_time=?, "
            "table_id=NULL, seat=NULL WHERE id=?",
            (place, now, player_id),
        )

        if eliminated_by_id and eliminated and eliminated["bounty"] > 0:
            bounty = eliminated["bounty"]
            eliminator = self.get_player(eliminated_by_id)
            pko_mode = self.get_setting_int("pko_mode", 0) == 1
            if pko_mode:
                cash_pct = self.get_setting_int("pko_cash_percent", 50)
                cash_part = round(bounty * cash_pct / 100)
                grow_part = bounty - cash_part
            else:
                cash_part = bounty
                grow_part = 0
            self.conn.execute(
                "UPDATE players SET bounty_won = bounty_won + ?, bounty = bounty + ? "
                "WHERE id=?",
                (cash_part, grow_part, eliminated_by_id),
            )
            self.conn.execute("UPDATE players SET bounty=0 WHERE id=?", (player_id,))
            self.conn.execute(
                "INSERT INTO bounty_events(eliminated_name, eliminator_name, "
                "amount_won, added_to_eliminator_bounty, event_time) "
                "VALUES (?,?,?,?,?)",
                (eliminated["name"], eliminator["name"] if eliminator else "?",
                 cash_part, grow_part, now),
            )

        self.conn.commit()
        return self.rebalance_tables(record_moves=True)

    def withdraw_player(self, player_id):
        """Retire un joueur de la liste active sans lui attribuer de place
        au classement (forfait / inscription annulée), contrairement à
        eliminate_player. Libère sa table/siège comme une élimination."""
        self.conn.execute(
            "UPDATE players SET status='withdrawn', place=NULL, elim_time=?, "
            "table_id=NULL, seat=NULL WHERE id=?",
            (time.strftime("%Y-%m-%d %H:%M:%S"), player_id),
        )
        self.conn.commit()
        return self.rebalance_tables(record_moves=False)

    def reinstate_player(self, player_id):
        starting_chips = self.get_setting_int("starting_chips", 10000)
        bounty_amount = self.get_setting_int("bounty_amount", 0)
        self.conn.execute(
            "UPDATE players SET status='active', place=NULL, elim_time=NULL, "
            "chips=?, bounty=? WHERE id=?",
            (starting_chips, bounty_amount, player_id),
        )
        self.conn.commit()
        self._seat_player(player_id)
        return self.rebalance_tables(record_moves=False)

    def delete_player(self, player_id):
        self.conn.execute("DELETE FROM players WHERE id=?", (player_id,))
        self.conn.commit()
        return self.rebalance_tables(record_moves=False)

    # ---------- seating / balancing ----------
    def _seat_player(self, player_id):
        """Assoit un joueur à la table la moins remplie, sur le premier siège libre."""
        tables = self.list_tables()
        if not tables:
            self.add_table()
            tables = self.list_tables()
        best_table = None
        best_count = None
        for t in tables:
            occ = self.conn.execute(
                "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                (t["id"],),
            ).fetchone()["c"]
            if occ < t["max_seats"] and (best_count is None or occ < best_count):
                best_table, best_count = t, occ
        if best_table is None:
            new_id = self.add_table()
            best_table = self.conn.execute(
                "SELECT * FROM tables_pk WHERE id=?", (new_id,)
            ).fetchone()
        taken = {
            r["seat"]
            for r in self.conn.execute(
                "SELECT seat FROM players WHERE table_id=? AND status='active'",
                (best_table["id"],),
            )
        }
        seat = 1
        while seat in taken:
            seat += 1
        self.conn.execute(
            "UPDATE players SET table_id=?, seat=? WHERE id=?",
            (best_table["id"], seat, player_id),
        )
        self.conn.commit()

    def rebalance_tables(self, record_moves=False):
        """Rééquilibre les tables actives : comble les sièges vides en déplaçant
        des joueurs des tables les plus pleines, et ferme les tables devenues
        inutiles quand le nombre de joueurs restants tient sur moins de tables.
        Si record_moves est vrai, archive chaque déplacement réel (ancienne
        table/siège -> nouvelle table/siège) dans l'historique des
        mouvements (onglet Mouvements) — ce n'est le cas que pour les
        rééquilibrages déclenchés par une élimination de joueur. Renvoie
        dans tous les cas la liste des mouvements effectués."""
        active_players = [
            dict(p) for p in self.list_players(status="active")
        ]
        n_active = len(active_players)
        if n_active == 0:
            return []

        before_state = {p["id"]: (p["table_id"], p["seat"]) for p in active_players}

        max_seats = self.get_setting_int("max_seats_per_table", 9)
        min_players = self.get_setting_int("min_players_per_table", 4)
        tables = list(self.list_tables())
        n_tables_needed = max(1, math.ceil(n_active / max_seats))

        # En dessous du seuil minimum de joueurs par table, on regroupe
        # davantage (une table de plus en moins) tant que c'est possible
        # sans dépasser le nombre de sièges disponibles par table.
        if min_players > 1:
            while (
                n_tables_needed > 1
                and n_active / n_tables_needed < min_players
                and n_active <= (n_tables_needed - 1) * max_seats
            ):
                n_tables_needed -= 1

        # Ouvre des tables supplémentaires si le nombre de sièges disponibles
        # ne suffit plus (ex : réduction du nombre de sièges par table en
        # cours de tournoi).
        if len(tables) < n_tables_needed:
            for _ in range(n_tables_needed - len(tables)):
                self.add_table()
            tables = list(self.list_tables())

        # Ferme les tables en trop (en commençant par les plus récentes / moins peuplées)
        if len(tables) > n_tables_needed:
            occ_by_table = {}
            for p in active_players:
                occ_by_table.setdefault(p["table_id"], []).append(p)
            # trie les tables par nb de joueurs (asc) -> on vide les plus petites d'abord
            tables_sorted = sorted(
                tables, key=lambda t: len(occ_by_table.get(t["id"], []))
            )
            to_close = tables_sorted[: len(tables) - n_tables_needed]
            for t in to_close:
                players_to_move = occ_by_table.get(t["id"], [])
                self.close_table(t["id"])
                for p in players_to_move:
                    self.conn.execute(
                        "UPDATE players SET table_id=NULL, seat=NULL WHERE id=?",
                        (p["id"],),
                    )
                self.conn.commit()
                for p in players_to_move:
                    self._seat_player(p["id"])

        # Ré-équilibre : déplace un joueur de la table la plus pleine vers la
        # table la moins pleine tant que l'écart est >= 2
        for _ in range(200):  # garde-fou anti boucle infinie
            tables = list(self.list_tables())
            if len(tables) < 2:
                break
            counts = []
            for t in tables:
                occ = self.conn.execute(
                    "SELECT COUNT(*) c FROM players WHERE table_id=? AND status='active'",
                    (t["id"],),
                ).fetchone()["c"]
                counts.append((occ, t))
            counts.sort(key=lambda x: x[0])
            smallest_count, smallest_table = counts[0]
            largest_count, largest_table = counts[-1]
            if largest_count - smallest_count < 2:
                break
            if smallest_count >= smallest_table["max_seats"]:
                break
            mover = self.conn.execute(
                "SELECT id FROM players WHERE table_id=? AND status='active' LIMIT 1",
                (largest_table["id"],),
            ).fetchone()
            if not mover:
                break
            taken = {
                r["seat"]
                for r in self.conn.execute(
                    "SELECT seat FROM players WHERE table_id=? AND status='active'",
                    (smallest_table["id"],),
                )
            }
            seat = 1
            while seat in taken:
                seat += 1
            self.conn.execute(
                "UPDATE players SET table_id=?, seat=? WHERE id=?",
                (smallest_table["id"], seat, mover["id"]),
            )
            self.conn.commit()

        # Recompacte les numéros de sièges de chaque table (1, 2, 3... sans
        # trou, jamais au-delà du nombre de sièges défini) : nécessaire
        # notamment après une réduction du nombre de sièges par table en
        # cours de tournoi, où d'anciens numéros de siège (ex : 7, 8, 9)
        # pouvaient sinon rester affichés malgré le nouveau maximum.
        for t in self.list_tables():
            occupants = self.conn.execute(
                "SELECT id, seat FROM players WHERE table_id=? AND status='active' "
                "ORDER BY seat",
                (t["id"],),
            ).fetchall()
            for i, p in enumerate(occupants, start=1):
                if p["seat"] != i:
                    self.conn.execute(
                        "UPDATE players SET seat=? WHERE id=?", (i, p["id"])
                    )
        self.conn.commit()

        # Calcule les déplacements réels (avant -> après) et les archive.
        after_players = [dict(p) for p in self.list_players(status="active")]
        table_names = {t["id"]: t["name"] for t in self.list_tables(active_only=False)}
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        moves = []
        for p in after_players:
            old_table_id, old_seat = before_state.get(p["id"], (None, None))
            new_table_id, new_seat = p["table_id"], p["seat"]
            if old_table_id == new_table_id and old_seat == new_seat:
                continue
            move = {
                "player_name": p["name"],
                "old_table_name": table_names.get(old_table_id),
                "old_seat": old_seat,
                "new_table_name": table_names.get(new_table_id),
                "new_seat": new_seat,
                "moved_at": now,
            }
            moves.append(move)
        if moves and record_moves:
            # On efface les mouvements précédents : l'onglet Mouvements
            # n'affiche que le dernier lot de déplacements en date, pas un
            # historique cumulatif.
            self.conn.execute("DELETE FROM seat_moves")
            for move in moves:
                self.conn.execute(
                    "INSERT INTO seat_moves(player_name, old_table_name, old_seat, "
                    "new_table_name, new_seat, moved_at) VALUES (?,?,?,?,?,?)",
                    (move["player_name"], move["old_table_name"], move["old_seat"],
                     move["new_table_name"], move["new_seat"], move["moved_at"]),
                )
            self.conn.commit()
        return moves

    def get_seat_moves(self, limit=500):
        """Historique des déplacements de joueurs entre tables/sièges (le
        plus récent en premier), pour l'onglet Mouvements."""
        return self.conn.execute(
            "SELECT * FROM seat_moves ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def count_seat_moves(self):
        return self.conn.execute("SELECT COUNT(*) c FROM seat_moves").fetchone()["c"]

    # ---------- primes (bounty) ----------
    def get_bounty_events(self, limit=500):
        """Historique des primes gagnées (le plus récent en premier), pour
        l'onglet Primes."""
        return self.conn.execute(
            "SELECT * FROM bounty_events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()

    def get_active_bounties(self):
        """Joueurs actifs portant une prime, triés par prime décroissante."""
        return self.conn.execute(
            "SELECT * FROM players WHERE status='active' AND bounty > 0 "
            "ORDER BY bounty DESC"
        ).fetchall()

    def get_bounty_totals(self):
        """Classement des joueurs par cumul de primes empochées (cash),
        toutes primes gagnées, décroissant."""
        return self.conn.execute(
            "SELECT * FROM players WHERE bounty_won > 0 ORDER BY bounty_won DESC"
        ).fetchall()

    # ---------- blind structure ----------
    def get_blind_structure(self):
        return self.conn.execute(
            "SELECT * FROM blind_levels ORDER BY level_order"
        ).fetchall()

    def set_blind_structure(self, levels):
        """levels: liste de dicts {small_blind, big_blind, ante, duration_minutes,
        is_break, break_label}"""
        self.conn.execute("DELETE FROM blind_levels")
        for i, lvl in enumerate(levels, start=1):
            self.conn.execute(
                "INSERT INTO blind_levels(level_order, small_blind, big_blind, "
                "ante, duration_minutes, is_break, break_label) VALUES (?,?,?,?,?,?,?)",
                (
                    i,
                    lvl.get("small_blind", 0),
                    lvl.get("big_blind", 0),
                    lvl.get("ante", 0),
                    lvl.get("duration_minutes", 15),
                    1 if lvl.get("is_break") else 0,
                    lvl.get("break_label", "Pause"),
                ),
            )
        self.conn.commit()

    def get_current_level(self):
        order = self.get_setting_int("current_level_order", 1)
        row = self.conn.execute(
            "SELECT * FROM blind_levels WHERE level_order=?", (order,)
        ).fetchone()
        if row is None:
            row = self.conn.execute(
                "SELECT * FROM blind_levels ORDER BY level_order LIMIT 1"
            ).fetchone()
        return row

    def get_next_level(self):
        order = self.get_setting_int("current_level_order", 1)
        return self.conn.execute(
            "SELECT * FROM blind_levels WHERE level_order=?", (order + 1,)
        ).fetchone()

    # ---------- payout structure ----------
    def get_payout_structure(self):
        return self.conn.execute(
            "SELECT * FROM payout_structure ORDER BY place"
        ).fetchall()

    def set_payout_structure(self, place_to_pct):
        self.conn.execute("DELETE FROM payout_structure")
        for place, pct in place_to_pct.items():
            self.conn.execute(
                "INSERT INTO payout_structure(place, percentage) VALUES (?, ?)",
                (place, pct),
            )
        self.conn.commit()

    # ---------- stats / prize pool ----------
    def get_stats(self):
        all_players = self.list_players()
        active = [p for p in all_players if p["status"] == "active"]
        entries = sum(p["buyin_count"] for p in all_players)
        rebuys = sum(p["rebuy_count"] for p in all_players)
        addons = sum(p["addon_count"] for p in all_players)
        buyin_amount = self.get_setting_float("buyin_amount", 0)
        rebuy_amount = self.get_setting_float("rebuy_amount", 0)
        addon_amount = self.get_setting_float("addon_amount", 0)
        rake_pct = self.get_setting_float("rake_percent", 0)
        gross = entries * buyin_amount + rebuys * rebuy_amount + addons * addon_amount
        prize_pool = gross * (1 - rake_pct / 100.0)
        total_chips = sum(p["chips"] for p in active)
        avg_stack = total_chips / len(active) if active else 0
        return {
            "total_players_ever": len(all_players),
            "active_count": len(active),
            "entries": entries,
            "rebuys": rebuys,
            "addons": addons,
            "gross": gross,
            "prize_pool": prize_pool,
            "total_chips": total_chips,
            "avg_stack": avg_stack,
        }

    def get_payouts_amounts(self):
        stats = self.get_stats()
        pool = stats["prize_pool"]
        result = []
        for row in self.get_payout_structure():
            result.append(
                {
                    "place": row["place"],
                    "percentage": row["percentage"],
                    "amount": pool * row["percentage"] / 100.0,
                }
            )
        return result

    def _results_rows(self):
        """Construit les lignes de résultats (place, nom, statut, gain,
        buy-ins, rebuys, add-ons, prime gagnée), triées meilleure place en
        premier. Utilisé par les exports CSV et XLSX."""
        payouts_by_place = {p["place"]: p["amount"] for p in self.get_payouts_amounts()}
        eliminated = [p for p in self.list_players() if p["status"] == "eliminated"]
        eliminated.sort(key=lambda p: p["place"])
        withdrawn = [p for p in self.list_players() if p["status"] == "withdrawn"]
        active = [p for p in self.list_players() if p["status"] == "active"]

        rows = []
        if active:
            place_label = f"1-{len(active)}" if len(active) > 1 else "1"
            for p in active:
                rows.append((place_label, p["name"], "En cours", None,
                             p["buyin_count"], p["rebuy_count"], p["addon_count"],
                             p["bounty_won"]))
        for p in eliminated:
            gain = payouts_by_place.get(p["place"])
            rows.append((p["place"], p["name"], "Éliminé", gain,
                         p["buyin_count"], p["rebuy_count"], p["addon_count"],
                         p["bounty_won"]))
        for p in withdrawn:
            rows.append(("-", p["name"], "Forfait", None,
                         p["buyin_count"], p["rebuy_count"], p["addon_count"],
                         p["bounty_won"]))
        return rows

    def _bounty_in_use(self):
        if self.get_setting_int("bounty_amount", 0) > 0:
            return True
        return self.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE bounty_won > 0"
        ).fetchone()["c"] > 0

    def export_results_csv(self, path):
        """Exporte le classement final (éliminés triés par place, puis
        joueurs encore actifs) avec le gain correspondant, en CSV. La
        colonne "Prime gagnée" n'apparaît que si le bounty est utilisé."""
        import csv

        with_bounty = self._bounty_in_use()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            headers = ["Place", "Nom", "Statut", "Gain (€)", "Buy-ins", "Rebuys", "Add-ons"]
            if with_bounty:
                headers.append("Prime gagnée (€)")
            writer.writerow(headers)
            for place, name, status, gain, bi, rb, ad, bounty_won in self._results_rows():
                gain_txt = f"{gain:.2f}" if gain else ""
                row = [place, name, status, gain_txt, bi, rb, ad]
                if with_bounty:
                    row.append(bounty_won or "")
                writer.writerow(row)
        return path

    def export_results_xlsx(self, path):
        """Exporte le classement final au format Excel (.xlsx), avec
        mise en forme (en-têtes en gras, colonnes ajustées, ligne de
        synthèse du tournoi). Nécessite le paquet 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        wb = Workbook()
        ws = wb.active
        ws.title = "Résultats"

        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")
        with_bounty = self._bounty_in_use()

        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} €"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = ["Place", "Nom", "Statut", "Gain (€)", "Buy-ins", "Rebuys", "Add-ons"]
        if with_bounty:
            headers.append("Prime gagnée (€)")
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for place, pname, status, gain, bi, rb, ad, bounty_won in self._results_rows():
            row = [place, pname, status, round(gain, 2) if gain else None, bi, rb, ad]
            if with_bounty:
                row.append(bounty_won or None)
            ws.append(row)

        widths = [10, 26, 12, 14, 10, 10, 10, 16]
        for i, w in enumerate(widths[: len(headers)], start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

        wb.save(path)
        return path

    def close(self):
        self.conn.close()


# =====================================================================
# Synthèse multi-tournois par période (parcourt plusieurs fichiers
# .tournoi d'un dossier). Contrairement au reste de ce module, ces
# fonctions ne portent pas sur un seul tournoi mais en agrègent
# plusieurs — elles sont donc au niveau module plutôt que sur la classe
# Database.
# =====================================================================

def find_tournament_files(folder, recursive=True):
    """Liste (triée) des fichiers .tournoi trouvés dans `folder`, et ses
    sous-dossiers si `recursive` est vrai."""
    pattern = os.path.join(folder, "**", "*.tournoi") if recursive else os.path.join(folder, "*.tournoi")
    return sorted(glob.glob(pattern, recursive=recursive))


def build_period_summary(folder, date_from=None, date_to=None, recursive=True):
    """Parcourt tous les fichiers .tournoi d'un dossier et construit une
    synthèse des résultats pour la période indiquée. `date_from` /
    `date_to` sont des chaînes 'AAAA-MM-JJ' (bornes incluses), ou None
    pour ne pas borner. Renvoie un dict :

      {
        "tournaments": [ {name, date, path, entries, prize_pool, status,
                           winner, bounty_distributed}, ... ],  # triés par date
        "players": [ {name, tournaments_played, wins, best_place,
                       total_cost, total_gain, total_bounty_won, net}, ... ],
                                                    # triés par net décroissant
      }

    Le "net" par joueur = gains de classement + primes (bounty) empochées
    - montant investi (buy-in/rebuy/add-on), toutes tournois confondus sur
    la période."""
    tournaments = []
    players = {}

    for path in find_tournament_files(folder, recursive=recursive):
        try:
            db = Database(path)
        except Exception:
            continue
        try:
            date = db.get_tournament_date()
            if date_from and date < date_from:
                continue
            if date_to and date > date_to:
                continue

            name = db.get_setting(
                "tournament_name", os.path.splitext(os.path.basename(path))[0]
            )
            all_players = db.list_players()
            active = [p for p in all_players if p["status"] == "active"]
            finished = len(active) == 1
            winner = active[0]["name"] if finished else "-"
            bounty_distributed = sum(p["bounty_won"] or 0 for p in all_players)
            entries = sum(p["buyin_count"] for p in all_players)
            stats = db.get_stats()
            payouts_by_place = {r["place"]: r["amount"] for r in db.get_payouts_amounts()}

            tournaments.append({
                "name": name,
                "date": date,
                "path": path,
                "entries": entries,
                "prize_pool": stats["prize_pool"],
                "status": "Terminé" if finished else "En cours",
                "winner": winner,
                "bounty_distributed": bounty_distributed,
            })

            buyin_amount = db.get_setting_float("buyin_amount", 0)
            rebuy_amount = db.get_setting_float("rebuy_amount", 0)
            addon_amount = db.get_setting_float("addon_amount", 0)

            for p in all_players:
                place = None
                gain = 0.0
                if p["status"] == "eliminated":
                    place = p["place"]
                    gain = payouts_by_place.get(place, 0.0)
                elif p["status"] == "active" and finished:
                    place = 1
                    gain = payouts_by_place.get(1, 0.0)

                cost = (
                    p["buyin_count"] * buyin_amount
                    + p["rebuy_count"] * rebuy_amount
                    + p["addon_count"] * addon_amount
                )
                bounty_won = p["bounty_won"] or 0

                agg = players.setdefault(p["name"], {
                    "name": p["name"],
                    "tournaments_played": 0,
                    "wins": 0,
                    "best_place": None,
                    "total_cost": 0.0,
                    "total_gain": 0.0,
                    "total_bounty_won": 0,
                })
                agg["tournaments_played"] += 1
                agg["total_cost"] += cost
                agg["total_gain"] += gain
                agg["total_bounty_won"] += bounty_won
                if place == 1:
                    agg["wins"] += 1
                if place is not None and (agg["best_place"] is None or place < agg["best_place"]):
                    agg["best_place"] = place
        finally:
            db.close()

    for agg in players.values():
        agg["net"] = agg["total_gain"] + agg["total_bounty_won"] - agg["total_cost"]

    tournaments.sort(key=lambda t: t["date"])
    players_list = sorted(players.values(), key=lambda a: a["net"], reverse=True)
    return {"tournaments": tournaments, "players": players_list}


def export_period_summary_csv(summary, path):
    """Exporte une synthèse (issue de build_period_summary) en CSV : une
    section 'Tournois de la période', puis une section 'Classement des
    joueurs' incluant les primes (bounty) empochées."""
    import csv

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(["Tournois de la période"])
        writer.writerow([
            "Date", "Tournoi", "Statut", "Entrées", "Prize pool (€)",
            "Vainqueur", "Primes distribuées (€)",
        ])
        for t in summary["tournaments"]:
            writer.writerow([
                t["date"], t["name"], t["status"], t["entries"],
                f"{t['prize_pool']:.2f}", t["winner"], t["bounty_distributed"],
            ])
        writer.writerow([])
        writer.writerow(["Classement des joueurs sur la période"])
        writer.writerow([
            "Joueur", "Tournois joués", "Victoires", "Meilleure place",
            "Total investi (€)", "Total gains classement (€)",
            "Total primes gagnées (€)", "Net (€)",
        ])
        for a in summary["players"]:
            writer.writerow([
                a["name"], a["tournaments_played"], a["wins"],
                a["best_place"] or "-", f"{a['total_cost']:.2f}",
                f"{a['total_gain']:.2f}", a["total_bounty_won"],
                f"{a['net']:.2f}",
            ])
    return path
