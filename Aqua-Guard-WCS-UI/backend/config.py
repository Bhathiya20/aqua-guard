"""Central backend configuration.

Values come from environment variables, optionally loaded from backend/.env
(never commit .env).  Leak-detection thresholds live here and ONLY here.
"""
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_dotenv(path: Path | None = None) -> None:
    """Tiny .env loader (no extra dependency).  Real environment wins."""
    path = path or (BASE_DIR / ".env")
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def _bool(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class LeakConfig:
    """Leak detection thresholds - the single source of truth."""
    upstream_node: int = 1               # node measuring flow INTO the monitored section
    downstream_node: int = 2             # node measuring flow OUT of it
    pair_tolerance_s: float = 2.5        # max time distance for two samples to be treated as simultaneous
    window_s: float = 60.0               # how much history is aligned and smoothed
    smooth_pairs: int = 5                # moving average over this many aligned pairs
    abs_threshold_lpm: float = 0.6       # upstream-downstream flow difference (L/min) ...
    rel_threshold_pct: float = 15.0      # ... AND relative to upstream flow (%) must both be exceeded
    min_flow_lpm: float = 0.5            # below this upstream flow no judgement is made
    suspect_after_s: float = 20.0        # discrepancy must persist this long -> SUSPECTED
    confirm_after_s: float = 60.0        # ... and this long -> CONFIRMED
    clear_suspect_after_s: float = 10.0  # normal for this long drops a SUSPECTED episode
    resolve_after_s: float = 45.0        # normal for this long resolves a CONFIRMED leak
    resolved_display_s: float = 300.0    # keep state RESOLVED this long before NORMAL
    stale_after_s: float = 20.0          # a flow node silent for this long => SENSOR_FAULT
    critical_diff_lpm: float = 2.5       # severity critical above this difference
    reverse_fault_lpm: float = 1.0       # downstream exceeding upstream by this much => sensor/calibration fault


@dataclass
class Settings:
    secret_key: str = ""
    db_path: Path = BASE_DIR / "database" / "aquaguard.db"
    host: str = "127.0.0.1"
    port: int = 5000
    cors_origins: list = field(default_factory=list)
    session_idle_minutes: int = 60
    session_absolute_hours: int = 12
    cookie_secure: bool = False
    cookie_samesite: str = "Lax"
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    min_password_length: int = 10

    mqtt_enabled: bool = True
    mqtt_host: str = "broker.emqx.io"
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_tls: bool = False
    mqtt_ca_file: str = ""
    mqtt_topic_prefix: str = "waterleak/project001"

    # Gateway heartbeat is published every 15 s. Declare it offline after 3 missed beats.
    gateway_heartbeat_s: int = 15
    gateway_offline_s: int = 45
    battery_min_plausible_mv: int = 4000   # a 2S pack that powers the node cannot read below this / above the max: such a value is a wiring or firmware fault, not a battery state
    battery_max_plausible_mv: int = 9500
    battery_low_mv: int = 6600           # pack voltage (after the 220k/100k divider maths) below which a LOW_BATTERY alert is raised (2S Li-ion)
    node_offline_s: int = 30             # node silent (data AND heartbeat) this long => sensor offline
    authoritative_node: int = 1          # node whose volume is "total consumption"
    comm_loss_alert_pct: float = 10.0    # loss ratio over recent window that raises COMM_DEGRADED
    cmd_key_hex: str = ""                # AQUAGUARD_CMD_KEY: 32 hex chars = the site command key (AQUA_CMD_KEY in aqua_secrets.h); AQC2 commands
    command_timeout_s: int = 20          # wait for the gateway's cmd/result before a command is marked "timeout"
    gmk_hex: str = ""                    # AQUAGUARD_GMK: 32 hex chars, pairing master key (same value as the gateway build)
    pairing_code_max_failures: int = 5
    pairing_lock_minutes: int = 15
    pairing_timeout_s: int = 120         # a started pairing must complete within this time
    default_admin_email: str = "admin@aquaguard.com"
    default_admin_password: str = ""     # empty => random password generated + printed ONCE on first start
    leak: LeakConfig = field(default_factory=LeakConfig)


def load_settings() -> Settings:
    load_dotenv()
    s = Settings()
    s.secret_key = os.environ.get("AQUAGUARD_SECRET_KEY", "")
    if os.environ.get("AQUAGUARD_DB_PATH"):
        s.db_path = Path(os.environ["AQUAGUARD_DB_PATH"])
    s.host = os.environ.get("AQUAGUARD_HOST", s.host)
    s.port = _int("AQUAGUARD_PORT", s.port)
    origins = os.environ.get(
        "AQUAGUARD_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    )
    s.cors_origins = [o.strip() for o in origins.split(",") if o.strip()]
    s.session_idle_minutes = _int("AQUAGUARD_SESSION_IDLE_MIN", s.session_idle_minutes)
    s.session_absolute_hours = _int("AQUAGUARD_SESSION_ABS_HOURS", s.session_absolute_hours)
    s.cookie_secure = _bool("AQUAGUARD_COOKIE_SECURE", False)
    s.cookie_samesite = os.environ.get("AQUAGUARD_COOKIE_SAMESITE", "Lax")
    s.mqtt_enabled = _bool("AQUAGUARD_MQTT_ENABLED", True)
    s.mqtt_host = os.environ.get("AQUAGUARD_MQTT_HOST", s.mqtt_host)
    s.mqtt_port = _int("AQUAGUARD_MQTT_PORT", s.mqtt_port)
    s.mqtt_user = os.environ.get("AQUAGUARD_MQTT_USER", "")
    s.mqtt_password = os.environ.get("AQUAGUARD_MQTT_PASSWORD", "")
    s.mqtt_tls = _bool("AQUAGUARD_MQTT_TLS", False)
    s.mqtt_ca_file = os.environ.get("AQUAGUARD_MQTT_CA_FILE", "")
    s.mqtt_topic_prefix = os.environ.get("AQUAGUARD_MQTT_TOPIC_PREFIX", s.mqtt_topic_prefix)
    s.gateway_heartbeat_s = _int("AQUAGUARD_GATEWAY_HEARTBEAT_S", s.gateway_heartbeat_s)
    s.gateway_offline_s = _int("AQUAGUARD_GATEWAY_OFFLINE_S", s.gateway_heartbeat_s * 3)
    s.node_offline_s = _int("AQUAGUARD_NODE_OFFLINE_S", s.node_offline_s)
    s.battery_low_mv = _int("AQUAGUARD_BATTERY_LOW_MV", s.battery_low_mv)
    s.battery_min_plausible_mv = _int("AQUAGUARD_BATTERY_MIN_PLAUSIBLE_MV", s.battery_min_plausible_mv)
    s.battery_max_plausible_mv = _int("AQUAGUARD_BATTERY_MAX_PLAUSIBLE_MV", s.battery_max_plausible_mv)
    s.authoritative_node = _int("AQUAGUARD_AUTH_NODE", s.authoritative_node)
    s.gmk_hex = os.environ.get("AQUAGUARD_GMK", "").strip()
    s.cmd_key_hex = os.environ.get("AQUAGUARD_CMD_KEY", "").strip()
    s.command_timeout_s = _int("AQUAGUARD_COMMAND_TIMEOUT_S", s.command_timeout_s)
    s.pairing_code_max_failures = _int("AQUAGUARD_PAIR_MAX_FAILURES", s.pairing_code_max_failures)
    s.pairing_lock_minutes = _int("AQUAGUARD_PAIR_LOCK_MIN", s.pairing_lock_minutes)
    s.pairing_timeout_s = _int("AQUAGUARD_PAIR_TIMEOUT_S", s.pairing_timeout_s)
    s.default_admin_email = os.environ.get("AQUAGUARD_ADMIN_EMAIL", s.default_admin_email)
    s.default_admin_password = os.environ.get("AQUAGUARD_ADMIN_PASSWORD", "")
    s.leak.abs_threshold_lpm = _float("AQUAGUARD_LEAK_ABS_LPM", s.leak.abs_threshold_lpm)
    s.leak.rel_threshold_pct = _float("AQUAGUARD_LEAK_REL_PCT", s.leak.rel_threshold_pct)
    s.leak.confirm_after_s = _float("AQUAGUARD_LEAK_CONFIRM_S", s.leak.confirm_after_s)
    s.leak.suspect_after_s = _float("AQUAGUARD_LEAK_SUSPECT_S", s.leak.suspect_after_s)
    return s


def ensure_secret_key(s: Settings) -> Settings:
    """Persist a random secret key next to the DB if none is configured."""
    if s.secret_key:
        return s
    key_file = Path(s.db_path).parent / ".secret_key"
    key_file.parent.mkdir(parents=True, exist_ok=True)
    if key_file.exists():
        s.secret_key = key_file.read_text().strip()
    else:
        s.secret_key = secrets.token_hex(32)
        key_file.write_text(s.secret_key)
    return s
