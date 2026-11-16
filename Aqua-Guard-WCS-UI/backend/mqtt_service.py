"""MQTT subscriber + periodic evaluator.

* ONE wildcard subscription (<prefix>/#) - no overlapping subscriptions, so the
  broker cannot deliver a message twice because of our own subscriptions.
* Every message is validated (JSON, schema 2, ranges) before it touches the DB.
* Duplicates (retransmissions, redelivery) are rejected by the UNIQUE
  (node, boot, seq) key inside a single transaction.
"""
import json
import re
import threading
import time
import uuid

import paho.mqtt.client as mqtt

import analysis_service
import gateway_v2
import ingest
import pairing
import services
from config import Settings
from database import db_session

MAX_PAYLOAD_BYTES = 16384

state = {"connected": False, "lastMessageAt": None, "messages": 0, "rejected": 0, "duplicates": 0,
         "lastError": None, "broker": None, "tls": False}
_started = False
_lock = threading.Lock()
_client = None


_text_publisher = None             # callable(topic, text) -> bool ; set when the MQTT client exists (tests replace it)


def set_text_publisher(fn):
    global _text_publisher
    _text_publisher = fn


def publish_text(topic: str, text: str) -> bool:
    """Publish one message (used for signed AQC2 commands).  False when MQTT is down / disabled."""
    return bool(_text_publisher and _text_publisher(topic, text))


def handle_message(cfg: Settings, topic: str, payload: bytes, now=None) -> str:
    """Process one MQTT message.  Returns 'stored' | 'duplicate' | 'rejected' | 'ignored'.
    Separated from paho so tests can call it directly."""
    prefix = cfg.mqtt_topic_prefix.rstrip("/")
    if not topic.startswith(prefix + "/"):
        return "ignored"
    now = now or ingest.utc_now()                                    # the live MQTT thread passes no clock
    sub =topic[len(prefix) + 1:].rstrip("/")
    if len(payload) > MAX_PAYLOAD_BYTES:
        state["rejected"] += 1
        return "rejected"
    to_send = []
    text = payload.decode("utf-8", errors="replace").strip()
    if not text:
        return "ignored"
    try:
        with db_session() as conn:
            res = gateway_v2.route(conn, cfg, sub, text, now)          # protocol v2 topics first (None = not a v2 message)
            if res is not None:
                if res["result"] in ("rejected", "not_configured"):
                    state["rejected"] += 1
                    return "rejected"
            elif re.fullmatch(r"node(?:[12]|[4-9]|1[0-2])", sub):
                res = ingest.ingest_flow(conn, json.loads(text), now, expected_node=int(sub[4:]))
            elif sub == "rainwater":
                res = ingest.ingest_rain(conn, json.loads(text), now)
            elif sub == "gateway/heartbeat":
                res = ingest.ingest_gateway_heartbeat(conn, json.loads(text), now)
            elif sub == "gateway/status":
                res = ingest.ingest_gateway_status(conn, text, now)
            elif sub == "pairing/event":
                res = pairing.handle_pair_event(conn, cfg, text, now)
                to_send = res.get("commands", [])
                if res["result"] in ("rejected", "not_configured"):
                    state["rejected"] += 1
                    return "rejected"
            else:
                return "ignored"
    except (json.JSONDecodeError, ingest.IngestError) as e:
        state["rejected"] += 1
        state["lastError"] = f"{topic}: {e}"
        print(f"[MQTT] rejected {topic}: {e}")
        return "rejected"
    except Exception as e:  # never let one bad message kill the network thread
        state["rejected"] += 1
        state["lastError"] = f"{topic}: {e}"
        print(f"[MQTT] processing error on {topic}: {e}")
        return "rejected"
    for cmd in to_send:                                  # publish only AFTER the DB transaction committed
        pairing.send_cmd(cmd)
    if res.get("result") == "duplicate":
        state["duplicates"] += 1
        return "duplicate"
    state["messages"] += 1
    state["lastMessageAt"] = ingest.fmt_ts(ingest.utc_now())
    return "stored"


def start_mqtt(cfg: Settings):
    global _started, _client
    with _lock:
        if _started or not cfg.mqtt_enabled:
            return
        _started = True
    client_id = f"aquaguard-backend-{uuid.uuid4().hex[:8]}"
    client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    if cfg.mqtt_user:
        client.username_pw_set(cfg.mqtt_user, cfg.mqtt_password)
    if cfg.mqtt_tls:
        client.tls_set(ca_certs=cfg.mqtt_ca_file or None)     # verifies the broker certificate (default CA store if none given)
    state.update(broker=f"{cfg.mqtt_host}:{cfg.mqtt_port}", tls=cfg.mqtt_tls)
    topic = cfg.mqtt_topic_prefix.rstrip("/") + "/#"

    def on_connect(c, userdata, flags, reason_code, properties=None):
        if reason_code == 0:
            state["connected"] = True
            c.subscribe(topic, qos=1)
            print(f"[MQTT] connected to {cfg.mqtt_host}:{cfg.mqtt_port}{' (TLS)' if cfg.mqtt_tls else ' (PLAINTEXT)'}; subscribed {topic}")
        else:
            state["connected"] = False
            state["lastError"] = f"connect refused: {reason_code}"
            print(f"[MQTT] connect failed: {reason_code}")

    def on_disconnect(c, userdata, flags, reason_code, properties=None):
        state["connected"] = False
        print(f"[MQTT] disconnected ({reason_code}); automatic reconnect with back-off")

    def on_message(c, userdata, msg):
        handle_message(cfg, msg.topic, msg.payload)

    cmd_topic = cfg.mqtt_topic_prefix.rstrip("/") + "/gateway/cmd"

    def publish_cmd(text: str) -> bool:
        if not state["connected"]:
            return False
        return client.publish(cmd_topic, text, qos=1).rc == 0

    def _pub(topic: str, text: str) -> bool:
        if not state["connected"]:
            return False
        return client.publish(topic, text, qos=1).rc == 0

    set_text_publisher(_pub)
    pairing.publisher = publish_cmd
    client.on_connect, client.on_disconnect, client.on_message = on_connect, on_disconnect, on_message
    client.reconnect_delay_set(min_delay=1, max_delay=30)
    try:
        client.connect_async(cfg.mqtt_host, cfg.mqtt_port, keepalive=60)
        client.loop_start()
        _client = client
    except Exception as e:
        state["lastError"] = str(e)
        print(f"[MQTT] could not start: {e}")


class Evaluator(threading.Thread):
    """Leak lifecycle + alerts must keep running when NO messages arrive (that is when sensors go stale)."""

    def __init__(self, cfg: Settings, period_s: float = 2.0):
        super().__init__(daemon=True, name="aquaguard-evaluator")
        self.cfg, self.period, self._stop_evt = cfg, period_s, threading.Event()

    def run(self):
        while not self._stop_evt.wait(self.period):
            self.tick()

    def tick(self):
        try:
            with db_session() as conn:
                services.evaluate_alerts(conn, self.cfg)
                pairing.expire_stale_pairings(conn, self.cfg)
                gateway_v2.expire_commands(conn, self.cfg, ingest.utc_now())
                analysis_service.run_analysis(conn, self.cfg)
        except Exception as e:
            print(f"[EVAL] error: {e}")

    def stop(self):
        self._stop_evt.set()
