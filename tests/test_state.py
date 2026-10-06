import json
import os
import stat

import pytest

from ovigyan_connector.state import LocalDevice, State, StateStore, default_home


def test_home_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("OVIGYAN_CONNECTOR_HOME", str(tmp_path / "x"))
    assert default_home() == tmp_path / "x"


def test_missing_file_is_an_empty_unpaired_state(tmp_path):
    state = StateStore(tmp_path).load()
    assert (state.paired, state.status, state.devices) == (False, "unpaired", [])


def test_save_and_load_round_trip(tmp_path):
    store = StateStore(tmp_path)
    state = State(server="https://s.test", credential="ovigyan_conn_x", connector_id="c1", connector_name="PC", status="paired")
    state.devices.append(LocalDevice(host="10.0.0.5", port=4371, password=7, serial="SN1", model="X2008", firmware="v1"))
    store.save(state)
    loaded = store.load()
    assert loaded.paired and loaded.server == "https://s.test" and loaded.connector_name == "PC"
    assert loaded.devices[0].serial == "SN1" and loaded.devices[0].password == 7 and loaded.devices[0].id == state.devices[0].id


@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_the_file_holding_secrets_is_owner_only(tmp_path):
    store = StateStore(tmp_path / "home")
    store.save(State(server="https://s.test", credential="secret", status="paired"))
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.home.stat().st_mode) == 0o700


def test_saving_leaves_no_temporary_files_and_replaces_atomically(tmp_path):
    store = StateStore(tmp_path)
    store.save(State(server="https://a.test"))
    store.save(State(server="https://b.test"))
    assert sorted(p.name for p in tmp_path.iterdir()) == ["connector.json"]
    assert store.load().server == "https://b.test"


def test_a_failed_save_keeps_the_old_file(tmp_path, monkeypatch):
    store = StateStore(tmp_path)
    store.save(State(server="https://keep.test"))
    monkeypatch.setattr(os, "replace", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError):
        store.save(State(server="https://lost.test"))
    monkeypatch.undo()
    assert store.load().server == "https://keep.test"
    assert [p.name for p in tmp_path.iterdir()] == ["connector.json"]


def test_unknown_fields_in_a_newer_file_are_ignored(tmp_path):
    (tmp_path / "connector.json").write_text(json.dumps({"server": "https://s.test", "future_field": 1, "devices": []}))
    assert StateStore(tmp_path).load().server == "https://s.test"


def test_a_paired_state_needs_both_server_and_credential():
    assert not State(status="paired", server="https://s.test").paired
    assert not State(status="revoked", server="https://s.test", credential="x").paired


def test_each_terminal_gets_its_own_outbox_with_a_safe_name(tmp_path):
    store = StateStore(tmp_path)
    assert store.outbox_path("NFZ824090078").name == "outbox-NFZ824090078.db"
    assert store.outbox_path("../../etc/passwd").parent == tmp_path
    assert store.outbox_path("a b/c").name == "outbox-a_b_c.db"
