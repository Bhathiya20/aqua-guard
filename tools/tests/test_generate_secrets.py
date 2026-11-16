import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import generate_secrets as gs  # noqa: E402

TEMPLATE = gs.TEMPLATE


def run(tmp_path, *args):
    return gs.main(list(args), header=tmp_path / "aqua_secrets.h", env=tmp_path / ".env", template=TEMPLATE)


def keys_of(path):
    src = path.read_text()
    return {n: gs.read_header_key(src, n) for n in gs.KEYS}


def test_template_is_marked_and_all_zero():
    src = TEMPLATE.read_text()
    assert "#define AQUA_SECRETS_ARE_EXAMPLE 1" in src                       # every firmware build refuses it
    assert all(gs.read_header_key(src, n) == bytes(16) for n in gs.KEYS)


def test_generates_random_keys_and_matching_env(tmp_path):
    assert run(tmp_path) == 0
    h = tmp_path / "aqua_secrets.h"
    k = keys_of(h)
    assert all(v and v != bytes(16) for v in k.values())                      # no zero / default key
    assert len({v for v in k.values()}) == 4                                  # four independent keys
    assert "#define AQUA_SECRETS_ARE_EXAMPLE" not in re.sub(r"/\*.*?\*/", "", h.read_text(), flags=re.S)
    env = (tmp_path / ".env").read_text()
    assert f"AQUAGUARD_CMD_KEY={k['CMD_KEY'].hex()}" in env                    # firmware and backend can never drift


def test_existing_header_is_not_overwritten_without_force(tmp_path):
    run(tmp_path)
    before = keys_of(tmp_path / "aqua_secrets.h")
    assert run(tmp_path) == 1
    assert keys_of(tmp_path / "aqua_secrets.h") == before
    assert run(tmp_path, "--force") == 0
    assert keys_of(tmp_path / "aqua_secrets.h")["CMD_KEY"] != before["CMD_KEY"]


def test_env_other_settings_preserved_and_sync(tmp_path):
    (tmp_path / ".env").write_text("AQUAGUARD_MQTT_HOST=broker.example\nAQUAGUARD_CMD_KEY=00\n")
    run(tmp_path)
    env = (tmp_path / ".env").read_text()
    assert "AQUAGUARD_MQTT_HOST=broker.example" in env and env.count("AQUAGUARD_CMD_KEY=") == 1
    (tmp_path / ".env").write_text("AQUAGUARD_MQTT_HOST=broker.example\n")     # drift: key lost from .env
    assert run(tmp_path, "--sync-env") == 0
    assert f"AQUAGUARD_CMD_KEY={keys_of(tmp_path / 'aqua_secrets.h')['CMD_KEY'].hex()}" in (tmp_path / ".env").read_text()
