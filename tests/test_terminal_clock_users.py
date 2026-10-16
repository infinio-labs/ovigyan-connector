from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from fake_zk_device import FakeZkDevice
from ovigyan_connector.devices import ProbeError, clock_offset, read_users, set_clock

KOLKATA = ZoneInfo("Asia/Kolkata")


def kolkata_now() -> datetime:
    return datetime.now(KOLKATA).replace(tzinfo=None, microsecond=0)


def test_the_clock_offset_says_how_far_ahead_or_behind_the_terminal_is():
    from datetime import timedelta

    with FakeZkDevice(clock=kolkata_now() - timedelta(minutes=12)) as behind:
        offset = clock_offset("127.0.0.1", behind.port, 0, 5, "Asia/Kolkata")
    assert -725 <= offset <= -715  # twelve minutes behind, give or take the seconds the test took
    with FakeZkDevice(clock=kolkata_now() + timedelta(hours=2)) as ahead:
        assert 7195 <= clock_offset("127.0.0.1", ahead.port, 0, 5, "Asia/Kolkata") <= 7205
    with FakeZkDevice(clock=kolkata_now()) as right:
        assert abs(clock_offset("127.0.0.1", right.port, 0, 5, "Asia/Kolkata")) <= 5


def test_setting_the_clock_puts_the_terminal_on_this_pcs_local_time():
    from datetime import timedelta

    with FakeZkDevice(clock=kolkata_now() - timedelta(days=1)) as terminal:
        set_clock("127.0.0.1", terminal.port, 0, 5, "Asia/Kolkata")
        assert abs((terminal.clock - kolkata_now()).total_seconds()) <= 5
        assert abs(clock_offset("127.0.0.1", terminal.port, 0, 5, "Asia/Kolkata")) <= 5


def test_the_people_on_the_terminal_are_read_with_their_names():
    users = [
        {"uid": 1, "user_id": "7", "name": "Aarav Sharma"},
        {"uid": 2, "user_id": "0042", "name": "Diya Rao"},
        {"uid": 3, "user_id": "9", "name": ""},
    ]
    with FakeZkDevice(users=users) as terminal:
        found = read_users("127.0.0.1", terminal.port, 0, 5)
    assert found == [
        {"userId": "7", "name": "Aarav Sharma"},
        {"userId": "0042", "name": "Diya Rao"},
        {"userId": "9", "name": ""},  # pyzk's "NN-9" placeholder is not a name
    ]


def test_a_terminal_with_nobody_stored_gives_an_empty_list():
    with FakeZkDevice(users=[]) as terminal:
        assert read_users("127.0.0.1", terminal.port, 0, 5) == []


def test_a_terminal_that_cannot_be_reached_is_explained_not_crashed():
    with pytest.raises(ProbeError) as caught:
        clock_offset("127.0.0.1", 1, 0, 1, "Asia/Kolkata")
    assert caught.value.kind == "unreachable"
