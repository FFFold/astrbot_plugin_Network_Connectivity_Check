from datetime import datetime

import pytest

from main import NetworkConnectivityPlugin


class DummyContext:
    async def send_message(self, umo, chain):
        return None


class DummyEvent:
    def __init__(self):
        self.messages = []

    def get_sender_id(self):
        return "tester"

    def plain_result(self, message):
        self.messages.append(message)
        return message


def build_plugin(config=None):
    plugin = NetworkConnectivityPlugin(DummyContext(), config or {})
    plugin._save_state = lambda: None
    plugin._save_history = lambda: None
    return plugin


@pytest.mark.asyncio
async def test_update_target_state_notifies_once_when_threshold_reached():
    plugin = build_plugin(
        {
            "notification_settings": {
                "notify_on_status_change": True,
                "notify_on_success": False,
                "notify_on_failure": False,
                "consecutive_failures": 2,
                "silent_hours_start": -1,
                "silent_hours_end": 7,
            }
        }
    )
    sent_messages = []

    async def fake_send(message):
        sent_messages.append(message)

    plugin._send_notification = fake_send
    target = {"name": "example"}

    await plugin._update_target_state(
        target,
        {"timestamp": 1, "response_time": None, "success": False, "error": "超时"},
    )
    assert sent_messages == []

    await plugin._update_target_state(
        target,
        {"timestamp": 2, "response_time": None, "success": False, "error": "超时"},
    )
    assert len(sent_messages) == 1
    assert "已连续失败 2 次" in sent_messages[0]

    await plugin._update_target_state(
        target,
        {"timestamp": 3, "response_time": None, "success": False, "error": "超时"},
    )
    assert len(sent_messages) == 1


def test_get_target_config_normalizes_invalid_values():
    plugin = build_plugin(
        {
            "targets": [
                {
                    "name": " bad-target ",
                    "url": "https://example.com",
                    "method": "invalid",
                    "custom_settings": True,
                    "interval": 0,
                    "timeout": "oops",
                    "retry": -1,
                }
            ],
            "detection_settings": {"interval": 60, "timeout": 8, "retry": 2},
        }
    )

    targets = plugin._get_target_config()
    assert len(targets) == 1
    assert targets[0]["name"] == "bad-target"
    assert targets[0]["method"] == "http"
    assert targets[0]["interval"] == 60
    assert targets[0]["timeout"] == 8
    assert targets[0]["retry"] == 2


def test_parse_history_datetime_supports_date_end_of_day():
    plugin = build_plugin()
    parsed = plugin._parse_history_datetime("2026-04-01", is_end=True)
    assert parsed is not None
    dt = datetime.fromtimestamp(parsed)
    assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)


@pytest.mark.asyncio
async def test_net_history_supports_time_range_query():
    plugin = build_plugin()
    plugin.detection_history = {
        "site": [
            {
                "timestamp": datetime(2026, 4, 1, 12, 0, 0).timestamp(),
                "success": True,
                "response_time": 120,
                "error": None,
            },
            {
                "timestamp": datetime(2026, 4, 3, 12, 0, 0).timestamp(),
                "success": False,
                "response_time": None,
                "error": "超时",
            },
            {
                "timestamp": datetime(2026, 4, 8, 12, 0, 0).timestamp(),
                "success": True,
                "response_time": 90,
                "error": None,
            },
        ]
    }
    event = DummyEvent()

    results = []
    async for item in plugin.net_history(event, "site", "2026-04-01", "2026-04-07"):
        results.append(item)

    assert len(results) == 1
    message = results[0]
    assert "时间范围内共 2 条记录" in message
    assert "04-03 12:00:00" in message
    assert "04-08 12:00:00" not in message


@pytest.mark.asyncio
async def test_net_history_rejects_invalid_time_range():
    plugin = build_plugin()
    plugin.detection_history = {"site": []}
    event = DummyEvent()

    results = []
    async for item in plugin.net_history(event, "site", "bad-date", "2026-04-07"):
        results.append(item)

    assert results == [
        "⚠️ 时间格式错误，支持 YYYY-MM-DD、YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DDTHH:MM:SS"
    ]
