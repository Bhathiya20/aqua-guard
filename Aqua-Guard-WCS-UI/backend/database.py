"""SQLite access + versioned, non-destructive migrations (WAL mode, parameterised SQL only)."""
import sqlite3
from pathlib import Path

from werkzeug.security import generate_password_hash

DEFAULT_DB_PATH = Path(__file__).parent / "database" / "aquaguard.db"
_db_path = DEFAULT_DB_PATH


def set_db_path(path) -> None:
    global _db_path
    _db_path = Path(path)


def get_db_path() -> Path:
    return _db_path


def get_db() -> sqlite3.Connection:
    """New connection (WAL, FK on).  Use as `with closing(get_db()) as c` or via db_session()."""
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_db_path, timeout=15, isolation_level=None)  # autocommit; we manage txns explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA busy_timeout=15000;")
    return conn


class db_session:
    """Context manager: one transaction, always closed.  Commit on success, rollback on error."""

    def __init__(self, immediate: bool = True):
        self.immediate = immediate

    def __enter__(self) -> sqlite3.Connection:
        self.conn = get_db()
        self.conn.execute("BEGIN IMMEDIATE" if self.immediate else "BEGIN")
        return self.conn

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.execute("COMMIT")
            else:
                self.conn.execute("ROLLBACK")
        finally:
            self.conn.close()
        return False


def _script(conn, sql: str) -> None:
    """Run a multi-statement script INSIDE the current transaction (executescript would COMMIT first)."""
    import re
    sql = "\n".join(line.split("--", 1)[0] for line in sql.splitlines())   # strip line comments (no ';' or '--' in our string literals)
    for stmt in sql.split(";"):
        if stmt.strip():
            conn.execute(stmt)


def _cols(conn, table):
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_col(conn, table, name, ddl):
    if name not in _cols(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


# ---------------------------------------------------------------------------
# Migrations.  Each runs once, in order, inside a transaction.  Never edit an
# applied migration - append a new one.
# ---------------------------------------------------------------------------
def _m1_base(conn):
    """Legacy tables (kept so existing databases and endpoints keep working)."""
    _script(conn, """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        first_name TEXT, last_name TEXT,
        email TEXT UNIQUE COLLATE NOCASE,
        password_hash TEXT, phone TEXT, address TEXT,
        role TEXT DEFAULT 'user',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS flow_readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, node_id INTEGER, flow_rate REAL,
        total_liters REAL, uptime INTEGER, received_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS rainwater_readings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, rain_detected INTEGER, water_level REAL, distance REAL,
        turbidity_adc INTEGER, water_clear INTEGER, valve_open INTEGER, uptime INTEGER,
        received_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS gateway_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT, gateway_status TEXT, received_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS system_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT, gateway_status TEXT, received_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS leak_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, node1_flow REAL, node2_flow REAL, flow_difference REAL,
        difference_percentage REAL, severity TEXT DEFAULT 'warning', status TEXT DEFAULT 'active',
        detected_at TEXT DEFAULT CURRENT_TIMESTAMP, resolved_at TEXT);
    CREATE TABLE IF NOT EXISTS leakage_reports (
        id INTEGER PRIMARY KEY AUTOINCREMENT, report_code TEXT UNIQUE, building TEXT, location TEXT,
        description TEXT, status TEXT DEFAULT 'pending', reported_by TEXT,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    """)


def _m2_telemetry(conn):
    _script(conn, """
    CREATE TABLE devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        device_key TEXT NOT NULL UNIQUE,         -- node1 | node2 | rainwater | gateway:<id>
        kind TEXT NOT NULL,                      -- flow | rain | gateway
        node_id INTEGER,
        label TEXT,
        first_seen TEXT, last_seen TEXT,         -- last_seen = last sample/heartbeat accepted by the backend
        gw_last_seen TEXT,                       -- last time the GATEWAY says it heard the node
        last_boot_id INTEGER, last_seq INTEGER,
        channel INTEGER, rssi INTEGER, uptime_s INTEGER, status INTEGER,
        node_retransmits INTEGER, node_dropped INTEGER, node_rediscoveries INTEGER,
        sessions INTEGER DEFAULT 0
    );
    CREATE TABLE flow_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id INTEGER NOT NULL, boot_id INTEGER NOT NULL, seq INTEGER NOT NULL,
        measured_at TEXT NOT NULL,               -- gateway NTP time if available, else backend receive time
        received_at TEXT NOT NULL,
        recovered INTEGER DEFAULT 0, backlog INTEGER DEFAULT 0,
        cum_pulses INTEGER NOT NULL, interval_pulses INTEGER NOT NULL,
        delta_pulses INTEGER NOT NULL,           -- PULSES attributed to this row (cumulative-counter based)
        gap_before INTEGER DEFAULT 0,            -- sequence numbers missing right before this sample
        pulses_per_liter_x10 INTEGER NOT NULL,
        flow_lpm REAL NOT NULL, interval_ms INTEGER, uptime_s INTEGER, status INTEGER DEFAULT 0,
        channel INTEGER, rssi INTEGER, anomaly INTEGER DEFAULT 0,
        UNIQUE (node_id, boot_id, seq)
    );
    CREATE INDEX idx_flow_node_time ON flow_samples(node_id, measured_at);
    CREATE INDEX idx_flow_time ON flow_samples(measured_at);

    CREATE TABLE rain_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        boot_id INTEGER NOT NULL, seq INTEGER NOT NULL,
        measured_at TEXT NOT NULL, received_at TEXT NOT NULL,
        recovered INTEGER DEFAULT 0, backlog INTEGER DEFAULT 0,
        rain INTEGER, level_pct REAL, level_valid INTEGER, distance_cm REAL,
        turbidity_adc INTEGER, turbidity_valid INTEGER, water_clear INTEGER, tank_full INTEGER,
        valve_open INTEGER, valve_state INTEGER, valve_reason INTEGER,
        buzzer INTEGER, first_flush INTEGER, dry_run INTEGER, faults INTEGER, rain_events INTEGER,
        uptime_s INTEGER, channel INTEGER, rssi INTEGER,
        UNIQUE (boot_id, seq)
    );
    CREATE INDEX idx_rain_time ON rain_samples(measured_at);

    CREATE TABLE rain_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL, last_rain_at TEXT NOT NULL, ended_at TEXT,
        start_level_pct REAL, end_level_pct REAL, peak_level_pct REAL,
        valve_open_seconds REAL DEFAULT 0, unsafe_seconds REAL DEFAULT 0, samples INTEGER DEFAULT 0
    );

    CREATE TABLE gateway_heartbeats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gateway_id TEXT, received_at TEXT NOT NULL, uptime_s INTEGER, epoch INTEGER,
        wifi_up INTEGER, wifi_rssi INTEGER, channel INTEGER, mqtt_up INTEGER, free_heap INTEGER,
        queue_depth INTEGER, queue_overflow INTEGER, published INTEGER,
        wifi_reconnects INTEGER, mqtt_reconnects INTEGER, channel_changes INTEGER,
        rx_frames INTEGER, rx_rejected INTEGER, rx_bad_tag INTEGER, acks INTEGER, nacks INTEGER
    );
    CREATE INDEX idx_gwhb_time ON gateway_heartbeats(received_at);

    CREATE TABLE node_comm_health (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id INTEGER NOT NULL, received_at TEXT NOT NULL, boot_id INTEGER,
        received INTEGER, duplicates INTEGER, recovered INTEGER, lost INTEGER, missing INTEGER,
        out_of_order INTEGER, nacks_sent INTEGER, sessions INTEGER, invalid INTEGER,
        node_retransmits INTEGER, node_dropped INTEGER, node_rediscoveries INTEGER,
        age_s INTEGER, rssi INTEGER, node_uptime_s INTEGER
    );
    CREATE INDEX idx_comm_node_time ON node_comm_health(node_id, received_at);

    CREATE TABLE leak_detector_state (
        id INTEGER PRIMARY KEY CHECK (id = 1),
        state TEXT NOT NULL, since TEXT, reason TEXT, event_id INTEGER,
        disc_since REAL, normal_since REAL, resolved_at REAL, pre_fault_state TEXT, updated_at TEXT
    );
    INSERT INTO leak_detector_state (id, state) VALUES (1, 'NORMAL');

    CREATE TABLE alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        alert_key TEXT NOT NULL,                 -- e.g. LEAK_CONFIRMED, SENSOR_OFFLINE:1
        kind TEXT NOT NULL, severity TEXT NOT NULL, message TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, cleared_at TEXT, acknowledged_by TEXT, acknowledged_at TEXT
    );
    CREATE UNIQUE INDEX idx_alert_active_key ON alerts(alert_key) WHERE active = 1;

    CREATE TABLE audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        user_id INTEGER, email TEXT, action TEXT NOT NULL, detail TEXT, ip TEXT
    );
    """)
    for col, ddl in [("state", "TEXT"), ("suspected_at", "TEXT"), ("confirmed_at", "TEXT"),
                     ("peak_difference", "REAL"), ("peak_percentage", "REAL"), ("fault_reason", "TEXT"),
                     ("sample_count", "INTEGER DEFAULT 0")]:
        _add_col(conn, "leak_events", col, ddl)


def _m3_auth(conn):
    _script(conn, """
    CREATE TABLE sessions (
        token_hash TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        csrf_token TEXT NOT NULL,
        created_at REAL NOT NULL, last_seen REAL NOT NULL, expires_at REAL NOT NULL,
        ip TEXT, user_agent TEXT
    );
    CREATE INDEX idx_sessions_user ON sessions(user_id);
    """)
    _add_col(conn, "users", "active", "INTEGER DEFAULT 1")
    _add_col(conn, "users", "failed_logins", "INTEGER DEFAULT 0")
    _add_col(conn, "users", "locked_until", "REAL")
    _add_col(conn, "users", "must_change_password", "INTEGER DEFAULT 0")


def _m4_registry(conn):
    """Milestone 3: device registry + pairing.  Legacy devices 1,2 (flow) and 3 (rain) are seeded as paired."""
    _script(conn, """
    CREATE TABLE device_registry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        uid INTEGER NOT NULL UNIQUE,                 -- application device id (immutable). Legacy: 1,2,3. New devices: >= 100
        device_id TEXT NOT NULL UNIQUE,              -- human label printed on the device, e.g. AG-FLOW-104
        device_type TEXT NOT NULL,                   -- flow | rain
        display_name TEXT NOT NULL,
        mac TEXT,                                    -- hardware MAC (informational; NEVER used as authentication)
        pairing_status TEXT NOT NULL,                -- provisioned | pairing | paired | unpaired
        slot INTEGER,                                -- radio slot (gateway nodeId) while paired
        legacy INTEGER NOT NULL DEFAULT 0,
        firmware_version TEXT,
        calibration_ppl REAL,                        -- pulses per litre override (NULL = trust the device-reported value)
        code_hash TEXT, code_failures INTEGER NOT NULL DEFAULT 0, code_locked_until REAL,
        challenge TEXT, pairing_started REAL, pairing_error TEXT,
        last_boot_id INTEGER, last_verified_at TEXT, last_seen TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, paired_at TEXT, paired_by TEXT, unpaired_at TEXT
    );
    CREATE UNIQUE INDEX idx_registry_slot ON device_registry(slot) WHERE slot IS NOT NULL;
    CREATE TABLE discovered_devices (
        uid INTEGER NOT NULL, mac TEXT NOT NULL,
        device_type TEXT, firmware_version TEXT, channel INTEGER, rssi INTEGER,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, announces INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY (uid, mac)
    );
    CREATE TABLE pairing_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        uid INTEGER, mac TEXT, kind TEXT NOT NULL, ok INTEGER NOT NULL DEFAULT 0, detail TEXT, user_email TEXT, ip TEXT
    );
    CREATE INDEX idx_pair_attempts_ts ON pairing_attempts(ts);
    """)
    for uid, dev_id, typ, name in ((1, "AG-FLOW-001", "flow", "Flow node 1"), (2, "AG-FLOW-002", "flow", "Flow node 2"),
                                   (3, "AG-RAIN-003", "rain", "Rainwater node")):
        conn.execute("INSERT INTO device_registry (uid,device_id,device_type,display_name,pairing_status,slot,legacy,paired_at)"
                     " VALUES (?,?,?,?, 'paired', ?, 1, CURRENT_TIMESTAMP)", (uid, dev_id, typ, name, uid))


def _m5_v2(conn):
    """Protocol v2: gateways + nodes mirror of the gateway registry, boot sessions, signed commands, pipelines, zone state.
    Additive only: v1 history (flow_samples, devices, alerts ...) stays readable."""
    _script(conn, """
    CREATE TABLE gateways (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gateway_id TEXT NOT NULL UNIQUE, name TEXT, fw TEXT, proto INTEGER, mode TEXT, ip TEXT, channel INTEGER, rssi INTEGER,
        setup_done INTEGER, time_synced INTEGER, tls INTEGER, tls_verified INTEGER, remote_add INTEGER,
        status_raw TEXT, status_at TEXT, last_heartbeat_at TEXT, last_info_at TEXT, last_epoch INTEGER,
        registry_rev INTEGER, registry_at TEXT, uptime_s INTEGER, free_heap INTEGER, queue_depth INTEGER, queue_overflow INTEGER,
        rx_frames INTEGER, rx_rejected INTEGER, rx_bad_tag INTEGER, rx_bad_value INTEGER, rx_unknown_mac INTEGER, rx_queue_dropped INTEGER,
        first_seen TEXT
    );
    CREATE TABLE nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        gateway_ref INTEGER NOT NULL REFERENCES gateways(id),
        type TEXT NOT NULL CHECK (type IN ('flow','rain')),
        number INTEGER NOT NULL,                 -- per type, assigned by the gateway registry, never recycled
        mac TEXT NOT NULL,                       -- CURRENT hardware (lower-case hex, 12 chars); changes on Replace
        name TEXT, state TEXT NOT NULL DEFAULT 'pending',     -- pending | paired | removed
        online INTEGER DEFAULT 0, battery_mv INTEGER, rssi INTEGER, last_seen TEXT, last_seen_epoch INTEGER,
        ppl_x10 INTEGER, tx_reduce INTEGER DEFAULT 0, added_epoch INTEGER, registry_at TEXT,
        created_at TEXT, removed_at TEXT,
        UNIQUE (gateway_ref, type, number)
    );
    CREATE INDEX idx_nodes_mac ON nodes(mac);
    CREATE TABLE node_hw_history (                -- which physical device stood behind a node number, and when
        id INTEGER PRIMARY KEY AUTOINCREMENT, node_ref INTEGER NOT NULL REFERENCES nodes(id), mac TEXT NOT NULL,
        first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, UNIQUE (node_ref, mac)
    );
    CREATE TABLE node_sessions (                  -- one row per node boot; anchors node uptime to wall-clock time
        id INTEGER PRIMARY KEY AUTOINCREMENT, node_ref INTEGER NOT NULL REFERENCES nodes(id), hw_mac TEXT NOT NULL, boot_id INTEGER NOT NULL,
        anchor_epoch REAL NOT NULL,               -- unix time at which this boot had uptime 0 (lowest-latency estimate)
        anchor_src TEXT NOT NULL,                 -- gw_epoch (gateway NTP time) | recv (backend receive time, lower confidence)
        first_cum INTEGER, last_cum INTEGER, last_seq INTEGER, samples INTEGER DEFAULT 0, first_seen TEXT, last_seen TEXT,
        UNIQUE (node_ref, boot_id)
    );
    ALTER TABLE flow_samples ADD COLUMN node_ref INTEGER;
    ALTER TABLE flow_samples ADD COLUMN hw_mac TEXT;
    ALTER TABLE flow_samples ADD COLUMN session_id INTEGER;
    ALTER TABLE flow_samples ADD COLUMN measured_src TEXT DEFAULT 'legacy';   -- legacy | gw_epoch | recv
    CREATE INDEX idx_flow_ref_time ON flow_samples(node_ref, measured_at);
    CREATE TABLE gateway_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT, gateway_ref INTEGER, op TEXT NOT NULL, dev_type INTEGER, number INTEGER, mac TEXT,
        created_at TEXT NOT NULL, created_by TEXT, nonce TEXT, status TEXT NOT NULL DEFAULT 'sent',   -- sent | ok | error | timeout | not_sent
        result TEXT, rc INTEGER, resolved_at TEXT
    );
    CREATE TABLE pairing_events_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT, gateway_ref INTEGER, received_at TEXT NOT NULL, kind TEXT NOT NULL,    -- nearby | joined | failed
        number INTEGER, mac TEXT, dev_type INTEGER, fw INTEGER, channel INTEGER, rssi INTEGER, boot_id TEXT, epoch INTEGER, tag_ok INTEGER
    );
    CREATE TABLE pipelines (
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, created_by TEXT, created_at TEXT NOT NULL,
        active_version INTEGER, deleted INTEGER DEFAULT 0
    );
    CREATE TABLE pipeline_versions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pipeline_id INTEGER NOT NULL REFERENCES pipelines(id), version INTEGER NOT NULL,
        doc TEXT NOT NULL, note TEXT, valid INTEGER DEFAULT 0, created_by TEXT, created_at TEXT NOT NULL, UNIQUE (pipeline_id, version)
    );
    CREATE TABLE zone_state (                    -- persisted detection state (one row per zone of the ACTIVE pipeline version)
        pipeline_id INTEGER NOT NULL, zone_id TEXT NOT NULL, version INTEGER NOT NULL,
        zone_class TEXT, state TEXT NOT NULL DEFAULT 'normal', since TEXT, processed_until INTEGER DEFAULT 0, state_json TEXT,
        q_in_lpm REAL, q_out_lpm REAL, unaccounted_lpm REAL, complete INTEGER, confidence TEXT, alert_id INTEGER, updated_at TEXT,
        PRIMARY KEY (pipeline_id, zone_id)
    );
    CREATE TABLE zone_buckets (                  -- finalised per-bucket balance (kept for charts and leak history)
        pipeline_id INTEGER NOT NULL, zone_id TEXT NOT NULL, bucket_ts INTEGER NOT NULL,
        q_in_l REAL, q_out_l REAL, unaccounted_l REAL, complete INTEGER NOT NULL, confidence TEXT, state TEXT, version INTEGER,
        PRIMARY KEY (pipeline_id, zone_id, bucket_ts)
    );
    ALTER TABLE alerts ADD COLUMN zone_id TEXT;
    ALTER TABLE alerts ADD COLUMN pipeline_id INTEGER;
    ALTER TABLE alerts ADD COLUMN pipeline_version INTEGER;     -- the configuration that was active when the alert was raised
    ALTER TABLE alerts ADD COLUMN node_ref INTEGER;
    ALTER TABLE alerts ADD COLUMN state TEXT;
    ALTER TABLE alerts ADD COLUMN updated_at TEXT;
    """)


def _m6_leak_metrics(conn):
    """Numbers the dashboard shows for a leak event (flows when it was last evaluated and the peak imbalance); additive."""
    _script(conn, """
    ALTER TABLE alerts ADD COLUMN q_in_lpm REAL;
    ALTER TABLE alerts ADD COLUMN q_out_lpm REAL;
    ALTER TABLE alerts ADD COLUMN unaccounted_lpm REAL;
    ALTER TABLE alerts ADD COLUMN peak_unaccounted_lpm REAL;
    ALTER TABLE alerts ADD COLUMN confirmed_at TEXT;
    """)


def _m7_tx_power(conn):
    """Per-node TX power as the gateway reports it (0.1 dBm: 85, 110, 130, 150); additive."""
    _script(conn, "ALTER TABLE nodes ADD COLUMN tx_dbm_x10 INTEGER;")


def _m8_out_of_range(conn):
    """Rain node: ultrasonic hears nothing in range (no tank in front) vs a real sensor fault; additive."""
    _script(conn, "ALTER TABLE rain_samples ADD COLUMN out_of_range INTEGER DEFAULT 0;")


def _m9_out_of_service(conn):
    """Per-node maintenance flag: a node that is physically out of service raises no offline / missing-packet alerts; additive."""
    _script(conn, "ALTER TABLE nodes ADD COLUMN out_of_service INTEGER DEFAULT 0;")


def _m10_demo_mode(conn):
    """Per-pipeline demo mode (auto-off after 1 h) and the 'demo' tag on alerts raised while it was on; additive."""
    _script(conn, "ALTER TABLE pipelines ADD COLUMN demo_until INTEGER;")
    _script(conn, "ALTER TABLE alerts ADD COLUMN demo INTEGER DEFAULT 0;")


def _m12_balance_thresholds(conn):
    """Responsive balance: per-zone x+ / x- / T_leak chosen on the Leak Detection page (typed or learned), and the guided no-leak learning sessions; additive."""
    _script(conn, """
    CREATE TABLE zone_thresholds (
        pipeline_id INTEGER NOT NULL, zone_id TEXT NOT NULL,
        x_plus_lpm REAL, x_minus_lpm REAL, t_leak_s REAL,
        source TEXT, updated_at TEXT, updated_by TEXT,
        PRIMARY KEY (pipeline_id, zone_id)
    );
    CREATE TABLE threshold_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pipeline_id INTEGER NOT NULL, zone_id TEXT NOT NULL,
        started_at INTEGER NOT NULL, ended_at INTEGER, status TEXT NOT NULL DEFAULT 'running',     -- running | done | cancelled
        created_by TEXT, result_json TEXT
    );
    """)


def _m13_usage_and_history(conn):
    """Per-node usage profiles (duration detector) and the resolved-history fields of alerts; additive.
    usage_settings: scope 'default' | 'node:<ref>' | 'demo:<ref>' | 'zone:<pipeline>:<zone>' (time rules) | 'demo_clock' -> JSON.  usage_state: the running flow of one node (restart safe)."""
    _script(conn, """
    CREATE TABLE usage_settings (
        scope TEXT PRIMARY KEY, json TEXT NOT NULL, updated_by TEXT, updated_at TEXT
    );
    CREATE TABLE usage_state (
        node_ref INTEGER PRIMARY KEY, flowing INTEGER NOT NULL DEFAULT 0, flow_since REAL, last_flow_at REAL, processed_until REAL NOT NULL DEFAULT 0,
        peak_lpm REAL DEFAULT 0, last_lpm REAL, status TEXT, alert_id INTEGER, updated_at TEXT
    );
    ALTER TABLE alerts ADD COLUMN suspected_at TEXT;
    ALTER TABLE alerts ADD COLUMN resolved_by TEXT;          -- NULL on a cleared alert = resolved automatically
    ALTER TABLE alerts ADD COLUMN resolve_reason TEXT;       -- fixed_leak | false_alarm | test | tap_left_open | sensor_fault | other
    ALTER TABLE alerts ADD COLUMN resolve_note TEXT;
    ALTER TABLE alerts ADD COLUMN peak_flow_lpm REAL;
    ALTER TABLE alerts ADD COLUMN lost_l REAL DEFAULT 0;     -- estimated water lost: integral of D (leaks) or of the flow (usage alerts) over the event
    ALTER TABLE alerts ADD COLUMN acc_at TEXT;               -- last time lost_l was accumulated
    CREATE INDEX idx_alerts_cleared ON alerts(active, cleared_at);
    """)
    conn.execute("UPDATE alerts SET suspected_at=created_at WHERE suspected_at IS NULL AND kind IN ('LEAK','measurement_inconsistency')")


def _m11_calibration(conn):
    """Calibration wizard: sessions (pause leak detection for their nodes), runs with per-node cumulative-counter snapshots, per-node history; additive."""
    _script(conn, """
    CREATE TABLE calibration_sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, created_by TEXT, created_at TEXT NOT NULL, last_activity INTEGER NOT NULL, ended_at TEXT,
        status TEXT NOT NULL DEFAULT 'running',          -- running | done (applied) | cancelled
        pipeline_id INTEGER, method TEXT
    );
    CREATE TABLE calibration_session_nodes (
        session_id INTEGER NOT NULL REFERENCES calibration_sessions(id), node_ref INTEGER NOT NULL REFERENCES nodes(id), pos INTEGER NOT NULL,
        old_ppl_x10 INTEGER, PRIMARY KEY (session_id, node_ref)
    );
    CREATE TABLE calibration_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, session_id INTEGER NOT NULL REFERENCES calibration_sessions(id), idx INTEGER NOT NULL,
        started_at TEXT NOT NULL, stop_requested_at TEXT, stopped_at TEXT, volume_l REAL,
        status TEXT NOT NULL DEFAULT 'active',          -- active | stopping | done | invalid
        invalid_reason TEXT
    );
    CREATE TABLE calibration_run_nodes (
        run_id INTEGER NOT NULL REFERENCES calibration_runs(id), node_ref INTEGER NOT NULL, boot_id INTEGER, cum_start INTEGER, cum_end INTEGER,
        pulses INTEGER, valid INTEGER NOT NULL DEFAULT 1, reason TEXT, PRIMARY KEY (run_id, node_ref)
    );
    CREATE TABLE calibration_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, node_ref INTEGER NOT NULL REFERENCES nodes(id), at TEXT NOT NULL, user TEXT,
        old_ppl_x10 INTEGER, new_ppl_x10 INTEGER, runs INTEGER, spread_pct REAL, session_id INTEGER,
        source TEXT NOT NULL,                           -- wizard | manual | revert
        command_id INTEGER, method TEXT
    );
    CREATE INDEX idx_calhist_node ON calibration_history(node_ref, at);
    """)


def _m14_calibration_recalc(conn):
    _script(conn, "ALTER TABLE calibration_history ADD COLUMN recalculated_at TEXT;")       # set when the node's history was recalculated with the new value (admin option)


MIGRATIONS = [(1, _m1_base), (2, _m2_telemetry), (3, _m3_auth), (4, _m4_registry), (5, _m5_v2), (6, _m6_leak_metrics), (7, _m7_tx_power), (8, _m8_out_of_range), (9, _m9_out_of_service), (10, _m10_demo_mode), (11, _m11_calibration), (12, _m12_balance_thresholds), (13, _m13_usage_and_history), (14, _m14_calibration_recalc)]


def init_db(admin_email: str = "admin@aquaguard.com", admin_password: str = "") -> str | None:
    """Apply pending migrations; create the first admin if none exists.

    Returns a generated admin password (to be shown ONCE) or None.
    """
    conn = get_db()
    generated = None
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT DEFAULT CURRENT_TIMESTAMP)")
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for version, fn in MIGRATIONS:
            if version in done:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                fn(conn)
                conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise
        # The previous version shipped a hard-coded admin password.  Any account still using it must change it.
        from werkzeug.security import check_password_hash
        for u in conn.execute("SELECT id, password_hash FROM users").fetchall():
            try:
                if u["password_hash"] and check_password_hash(u["password_hash"], "Admin.AquaGuard"):
                    conn.execute("UPDATE users SET must_change_password=1 WHERE id=?", (u["id"],))
                    print("[SECURITY] An account still uses the old default password; a password change will be forced at next login.")
            except Exception:
                pass
        has_admin = conn.execute("SELECT 1 FROM users WHERE role='admin' AND COALESCE(active,1)=1 LIMIT 1").fetchone()
        if not has_admin:
            import secrets as _s
            pw = admin_password
            if not pw:
                pw = _s.token_urlsafe(12)
                generated = pw
            existing = conn.execute("SELECT id FROM users WHERE lower(email)=lower(?)", (admin_email,)).fetchone()
            if existing:
                conn.execute("UPDATE users SET role='admin', password_hash=?, active=1, must_change_password=? WHERE id=?",
                             (generate_password_hash(pw), 1 if generated else 0, existing["id"]))
            else:
                conn.execute(
                    "INSERT INTO users (first_name,last_name,email,password_hash,phone,address,role,must_change_password)"
                    " VALUES ('Admin','User',?,?,'','','admin',?)",
                    (admin_email, generate_password_hash(pw), 1 if generated else 0))
    finally:
        conn.close()
    return generated
