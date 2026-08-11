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
        """Construit les lignes de résultats (rang, nom, statut, gain,
        buy-ins, rebuys, add-ons, prime gagnée) sous forme de dicts, triées
        meilleur rang en premier. Utilisé par les exports CSV et XLSX.

        Le rang suit exactement la même convention que l'onglet Joueurs :
        1 pour le vainqueur (seul joueur encore actif, tournoi terminé),
        None (affiché "-") pour les autres joueurs encore actifs tant que
        le tournoi est en cours, et le rang habituel pour les éliminés."""
        payouts_by_place = {p["place"]: p["amount"] for p in self.get_payouts_amounts()}
        eliminated = [p for p in self.list_players() if p["status"] == "eliminated"]
        eliminated.sort(key=lambda p: p["place"])
        withdrawn = [p for p in self.list_players() if p["status"] == "withdrawn"]
        active = [p for p in self.list_players() if p["status"] == "active"]
        finished = len(active) == 1

        rows = []
        for p in active:
            rows.append({
                "rang": 1 if finished else None,
                "name": p["name"], "status": "En cours" if not finished else "Terminé",
                "gain": payouts_by_place.get(1) if finished else None,
                "buyin": p["buyin_count"], "rebuy": p["rebuy_count"], "addon": p["addon_count"],
                "bounty_won": p["bounty_won"],
            })
        for p in eliminated:
            rows.append({
                "rang": p["place"],
                "name": p["name"], "status": "Éliminé",
                "gain": payouts_by_place.get(p["place"]),
                "buyin": p["buyin_count"], "rebuy": p["rebuy_count"], "addon": p["addon_count"],
                "bounty_won": p["bounty_won"],
            })
        for p in withdrawn:
            rows.append({
                "rang": None,
                "name": p["name"], "status": "Forfait", "gain": None,
                "buyin": p["buyin_count"], "rebuy": p["rebuy_count"], "addon": p["addon_count"],
                "bounty_won": p["bounty_won"],
            })
        return rows

    def players_rows(self, sort_column=None, ascending=True):
        """Construit les lignes du tableau de l'onglet Joueurs (nom, table,
        siège, chips, achats, prime en jeu, statut, rang), avec le même
        calcul de rang que cet onglet. Utilisé pour son export dédié.

        `sort_column`/`ascending` reproduisent exactement le tri appliqué
        dans l'onglet (voir App._sort_players_by côté interface) : sans
        eux, l'ordre d'affichage à l'écran (par ex. trié par Rang) et
        l'ordre du fichier exporté pouvaient diverger."""
        status_labels = {"active": "Actif", "withdrawn": "Forfait", "eliminated": "Éliminé"}
        tables = {t["id"]: t["name"] for t in self.list_tables(active_only=False)}
        players = [dict(p) for p in self.list_players()]
        n_active = sum(1 for p in players if p["status"] == "active")

        rows = []
        for p in players:
            if p["status"] == "active":
                rang = 1 if n_active == 1 else None
            elif p["status"] == "eliminated":
                rang = p["place"]
            else:
                rang = None
            rows.append({
                "name": p["name"],
                "table": tables.get(p["table_id"], "-") if p["table_id"] else "-",
                "seat": p["seat"],
                "chips": p["chips"],
                "buyin": p["buyin_count"],
                "rebuy": p["rebuy_count"],
                "addon": p["addon_count"],
                "bounty": p["bounty"],
                "status": status_labels.get(p["status"], p["status"]),
                "rang": rang,
            })

        if sort_column == "name":
            rows.sort(key=lambda r: r["name"].lower())
        elif sort_column == "status":
            rows.sort(key=lambda r: r["status"].lower())
        elif sort_column == "table":
            rows.sort(key=lambda r: ((r["table"] or "").lower(), r["seat"] or 0))
        elif sort_column == "rang":
            rows.sort(key=lambda r: r["rang"] or 1)
        if sort_column and not ascending:
            rows.reverse()
        return rows

    def _bounty_in_use(self):
        if self.get_setting_int("bounty_amount", 0) > 0:
            return True
        return self.conn.execute(
            "SELECT COUNT(*) c FROM players WHERE bounty_won > 0"
        ).fetchone()["c"] > 0

    def export_results_csv(self, path, columns=None):
        """Exporte le classement final (éliminés triés par rang, puis
        joueurs encore actifs) avec le gain correspondant, en CSV.
        `columns` : sous-ensemble de clés de RESULT_COLUMNS à inclure
        (None = toutes celles pertinentes, primes comprises seulement si
        le bounty est utilisé)."""
        import csv

        cols = _selected_period_columns(RESULT_COLUMNS, columns)
        if columns is None and not self._bounty_in_use():
            cols = [c for c in cols if c[0] != "bounty_won"]
        rows = self._results_rows()
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in rows:
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_results_xlsx(self, path, columns=None):
        """Exporte le classement final au format Excel (.xlsx), avec
        mise en forme (en-têtes en gras, colonnes ajustées, ligne de
        synthèse du tournoi). `columns` : voir export_results_csv.
        Nécessite le paquet 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(RESULT_COLUMNS, columns)
        if columns is None and not self._bounty_in_use():
            cols = [c for c in cols if c[0] != "bounty_won"]
        rows = self._results_rows()

        wb = Workbook()
        ws = wb.active
        ws.title = "Résultats"

        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")

        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} €"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"rang": 10, "name": 26, "status": 12, "gain": 14,
                  "buyin": 10, "rebuy": 10, "addon": 10, "bounty_won": 16}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def export_payouts_csv(self, path, columns=None):
        """Exporte la grille de gains telle qu'affichée dans l'onglet
        Gains (place, pourcentage, montant — sans nom de joueur), en CSV.
        `columns` : sous-ensemble de clés de PAYOUT_COLUMNS (None = toutes)."""
        import csv

        cols = _selected_period_columns(PAYOUT_COLUMNS, columns)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in self.get_payouts_amounts():
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_payouts_xlsx(self, path, columns=None):
        """Exporte la grille de gains au format Excel (.xlsx). `columns` :
        voir export_payouts_csv. Nécessite le paquet 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(PAYOUT_COLUMNS, columns)
        rows = self.get_payouts_amounts()

        wb = Workbook()
        ws = wb.active
        ws.title = "Grille de gains"

        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")

        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} €"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"place": 10, "percentage": 16, "amount": 14}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def export_players_csv(self, path, columns=None, sort_column=None, ascending=True):
        """Exporte le tableau de l'onglet Joueurs tel qu'affiché (nom,
        table, siège, chips, achats, prime en jeu, statut, rang), en CSV.
        `columns` : sous-ensemble de clés de PLAYERS_TAB_COLUMNS (None =
        toutes). `sort_column`/`ascending` : voir players_rows — reprend
        le tri actuellement appliqué dans l'onglet."""
        import csv

        cols = _selected_period_columns(PLAYERS_TAB_COLUMNS, columns)
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow([h for _, h, _ in cols])
            for r in self.players_rows(sort_column=sort_column, ascending=ascending):
                writer.writerow([fn(r) for _, _, fn in cols])
        return path

    def export_players_xlsx(self, path, columns=None, sort_column=None, ascending=True):
        """Exporte le tableau de l'onglet Joueurs au format Excel (.xlsx).
        `columns`, `sort_column`, `ascending` : voir export_players_csv.
        Nécessite 'openpyxl'."""
        from openpyxl import Workbook
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter

        cols = _selected_period_columns(PLAYERS_TAB_COLUMNS, columns)
        rows = self.players_rows(sort_column=sort_column, ascending=ascending)

        wb = Workbook()
        ws = wb.active
        ws.title = "Joueurs"

        stats = self.get_stats()
        name = self.get_setting("tournament_name", "Tournoi")

        ws.append([name])
        ws["A1"].font = Font(bold=True, size=14)
        ws.append([f"Entrées : {stats['entries']}    Prize pool : {stats['prize_pool']:.2f} €"])
        ws["A2"].font = Font(italic=True)
        ws.append([])

        headers = [h for _, h, _ in cols]
        header_row = ws.max_row + 1
        ws.append(headers)
        header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=header_row, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")

        for r in rows:
            ws.append([fn(r) for _, _, fn in cols])

        widths = {"name": 22, "table": 14, "seat": 10, "chips": 12,
                  "buyin": 10, "rebuy": 10, "addon": 10, "bounty": 12,
                  "status": 12, "rang": 10}
        for i, (key, _, _) in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = widths.get(key, 14)

        wb.save(path)
        return path

    def close(self):
        self.conn.close()


def read_player_names_from_file(path):
    """Lit uniquement les noms des joueurs d'un fichier .tournoi existant,
    triés par ordre alphabétique, sans toucher au fichier ni reprendre
    leurs performances (chips, place, buy-ins...). Utilisé pour reprendre
    la liste des joueurs d'un tournoi précédent dans un nouveau tournoi.
    Ouvre la base en lecture seule (URI mode=ro) pour ne jamais créer ni
    modifier ce fichier, même par erreur. Lève une exception si le
    fichier n'est pas une base de tournoi valide."""
    uri = f"file:{os.path.abspath(path)}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        rows = conn.execute(
            "SELECT DISTINCT name FROM players ORDER BY name COLLATE NOCASE"
        ).fetchall()
        return [row[0] for row in rows if row[0] and row[0].strip()]
    finally:
        conn.close()


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


# Colonnes disponibles pour l'export de la synthèse par période, sous la
# forme (clé, en-tête, fonction d'extraction de la valeur à partir d'une
# ligne de tournoi/joueur). Définies une seule fois ici et réutilisées à
# la fois par l'export CSV, l'export Excel et la boîte de dialogue de
# sélection des colonnes (main.py) — ainsi les trois restent toujours en
# phase.
PERIOD_TOURNAMENT_COLUMNS = [
    ("date", "Date", lambda t: t["date"]),
    ("name", "Tournoi", lambda t: t["name"]),
    ("status", "Statut", lambda t: t["status"]),
    ("entries", "Entrées", lambda t: t["entries"]),
    ("prize_pool", "Prize pool (€)", lambda t: round(t["prize_pool"], 2)),
    ("winner", "Vainqueur", lambda t: t["winner"]),
    ("bounty_distributed", "Primes distribuées (€)", lambda t: t["bounty_distributed"]),
]

PERIOD_PLAYER_COLUMNS = [
    ("name", "Joueur", lambda a: a["name"]),
    ("tournaments_played", "Tournois joués", lambda a: a["tournaments_played"]),
    ("wins", "Victoires", lambda a: a["wins"]),
    ("best_place", "Meilleur Rang", lambda a: a["best_place"]),
    ("total_cost", "Total investi (€)", lambda a: round(a["total_cost"], 2)),
    ("total_gain", "Gains classement (€)", lambda a: round(a["total_gain"], 2)),
    ("total_bounty_won", "Primes gagnées (€)", lambda a: a["total_bounty_won"]),
    ("net", "Net (€)", lambda a: round(a["net"], 2)),
]

# Colonnes disponibles pour l'export du classement final nominatif d'UN
# tournoi (menu Fichier > Exporter les résultats...). Le rang du vainqueur
# est un entier (1), pas une chaîne, pour éviter le souci d'alignement
# Excel entre texte et nombres dans la même colonne.
RESULT_COLUMNS = [
    ("rang", "Rang", lambda r: r["rang"]),
    ("name", "Nom", lambda r: r["name"]),
    ("status", "Statut", lambda r: r["status"]),
    ("gain", "Gain (€)", lambda r: round(r["gain"], 2) if r["gain"] else None),
    ("buyin", "Buy-ins", lambda r: r["buyin"]),
    ("rebuy", "Rebuys", lambda r: r["rebuy"]),
    ("addon", "Add-ons", lambda r: r["addon"]),
    ("bounty_won", "Prime gagnée (€)", lambda r: r["bounty_won"]),
]

# Colonnes disponibles pour l'export de la grille de gains telle
# qu'affichée dans l'onglet Gains (place -> pourcentage -> montant, sans
# nom de joueur) — distinct du classement nominatif ci-dessus.
PAYOUT_COLUMNS = [
    ("place", "Place", lambda r: r["place"]),
    ("percentage", "Pourcentage (%)", lambda r: round(r["percentage"], 1)),
    ("amount", "Montant (€)", lambda r: round(r["amount"], 2)),
]

# Colonnes disponibles pour l'export de l'onglet Joueurs, dans le même
# ordre que son tableau (nom, table, siège, chips, achats, prime en jeu,
# statut, rang) — distinct du classement final nominatif (RESULT_COLUMNS,
# qui a le gain et la prime déjà empochée plutôt que la prime en jeu).
PLAYERS_TAB_COLUMNS = [
    ("name", "Nom", lambda p: p["name"]),
    ("table", "Table", lambda p: p["table"]),
    ("seat", "Siège", lambda p: p["seat"]),
    ("chips", "Chips", lambda p: p["chips"]),
    ("buyin", "Buy-in", lambda p: p["buyin"]),
    ("rebuy", "Rebuys", lambda p: p["rebuy"]),
    ("addon", "Add-ons", lambda p: p["addon"]),
    ("bounty", "Prime", lambda p: p["bounty"]),
    ("status", "Statut", lambda p: p["status"]),
    ("rang", "Rang", lambda p: p["rang"]),
]


def _selected_period_columns(columns, keys):
    """Sous-ensemble de `columns` (une des listes ci-dessus) correspondant
    à `keys`, dans l'ordre d'origine ; toutes les colonnes si `keys` est
    None."""
    if keys is None:
        return columns
    keys = set(keys)
    return [c for c in columns if c[0] in keys]


def export_period_summary_csv(summary, path, tournament_keys=None, player_keys=None):
    """Exporte une synthèse (issue de build_period_summary) en CSV : une
    section 'Tournois de la période', puis une section 'Classement des
    joueurs' incluant les primes (bounty) empochées. `tournament_keys` /
    `player_keys` permettent de ne garder qu'un sous-ensemble de colonnes
    (voir PERIOD_TOURNAMENT_COLUMNS / PERIOD_PLAYER_COLUMNS) ; None = toutes."""
    import csv

    t_cols = _selected_period_columns(PERIOD_TOURNAMENT_COLUMNS, tournament_keys)
    p_cols = _selected_period_columns(PERIOD_PLAYER_COLUMNS, player_keys)

    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        if t_cols:
            writer.writerow(["Tournois de la période"])
            writer.writerow([h for _, h, _ in t_cols])
            for t in summary["tournaments"]:
                writer.writerow([fn(t) for _, _, fn in t_cols])
            writer.writerow([])
        if p_cols:
            writer.writerow(["Classement des joueurs sur la période"])
            writer.writerow([h for _, h, _ in p_cols])
            for a in summary["players"]:
                writer.writerow([fn(a) for _, _, fn in p_cols])
    return path


def export_period_summary_xlsx(summary, path, tournament_keys=None, player_keys=None):
    """Exporte une synthèse en Excel (.xlsx) : une feuille 'Tournois', une
    feuille 'Joueurs', avec les mêmes options de sélection de colonnes que
    export_period_summary_csv. Nécessite le paquet 'openpyxl'."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment, PatternFill
    from openpyxl.utils import get_column_letter

    t_cols = _selected_period_columns(PERIOD_TOURNAMENT_COLUMNS, tournament_keys)
    p_cols = _selected_period_columns(PERIOD_PLAYER_COLUMNS, player_keys)
    header_fill = PatternFill(start_color="1F4E24", end_color="1F4E24", fill_type="solid")

    def _write_sheet(ws, cols, rows):
        ws.append([h for _, h, _ in cols])
        for col in range(1, len(cols) + 1):
            cell = ws.cell(row=1, column=col)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center")
        for row in rows:
            ws.append([fn(row) for _, _, fn in cols])
        for i, _ in enumerate(cols, start=1):
            ws.column_dimensions[get_column_letter(i)].width = 20

    wb = Workbook()
    first = True
    if t_cols:
        ws_t = wb.active
        ws_t.title = "Tournois"
        first = False
        _write_sheet(ws_t, t_cols, summary["tournaments"])
    if p_cols:
        ws_p = wb.active if first else wb.create_sheet("Joueurs")
        ws_p.title = "Joueurs"
        _write_sheet(ws_p, p_cols, summary["players"])
    if not t_cols and not p_cols:
        wb.active.title = "Synthèse"

    wb.save(path)
    return path
