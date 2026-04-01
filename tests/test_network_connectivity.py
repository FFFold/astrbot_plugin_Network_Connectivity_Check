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


def test_coerce_int_uses_default_for_invalid_and_out_of_range_values():
    plugin = build_plugin()

    assert (
        plugin._coerce_int("5", default=1, field_name="test", minimum=1, maximum=10)
        == 5
    )
    assert (
        plugin._coerce_int(-10, default=1, field_name="test", minimum=1, maximum=10)
        == 1
    )
    assert (
        plugin._coerce_int(9999, default=1, field_name="test", minimum=1, maximum=10)
        == 1
    )
    assert (
        plugin._coerce_int("oops", default=7, field_name="test", minimum=1, maximum=10)
        == 7
    )


def test_notification_silent_hours_normalization_uses_defaults_for_out_of_range_values():
    plugin = build_plugin(
        {
            "notification_settings": {
                "silent_hours_start": -5,
                "silent_hours_end": 30,
            }
        }
    )

    notification_settings = plugin._normalize_notification_settings()
    assert notification_settings["silent_hours_start"] == -1
    assert notification_settings["silent_hours_end"] == 7


def test_notification_flags_parse_string_and_numeric_boolean_values():
    plugin = build_plugin(
        {
            "notification_settings": {
                "notify_on_status_change": "false",
                "notify_on_success": "1",
                "notify_on_failure": 0,
            }
        }
    )

    notification_settings = plugin._normalize_notification_settings()
    assert notification_settings["notify_on_status_change"] is False
    assert notification_settings["notify_on_success"] is True
    assert notification_settings["notify_on_failure"] is False


def test_detection_ssl_verify_parses_string_values():
    false_plugin = build_plugin({"detection_settings": {"ssl_verify": "false"}})
    assert false_plugin._normalize_detection_settings()["ssl_verify"] is False

    true_plugin = build_plugin({"detection_settings": {"ssl_verify": "yes"}})
    assert true_plugin._normalize_detection_settings()["ssl_verify"] is True

    fallback_plugin = build_plugin({"detection_settings": {"ssl_verify": "maybe"}})
    assert fallback_plugin._normalize_detection_settings()["ssl_verify"] is True


def test_detection_ssl_verify_accepts_numeric_boolean_values():
    false_plugin = build_plugin({"detection_settings": {"ssl_verify": 0}})
    assert false_plugin._normalize_detection_settings()["ssl_verify"] is False

    true_plugin = build_plugin({"detection_settings": {"ssl_verify": 1}})
    assert true_plugin._normalize_detection_settings()["ssl_verify"] is True


def test_parse_history_datetime_supports_date_end_of_day():
    plugin = build_plugin()
    parsed = plugin._parse_history_datetime("2026-04-01", is_end=True)
    assert parsed is not None
    dt = datetime.fromtimestamp(parsed)
    assert (dt.hour, dt.minute, dt.second) == (23, 59, 59)


def test_normalized_settings_cache_refreshes_after_config_change():
    plugin = build_plugin(
        {
            "detection_settings": {"interval": 60, "timeout": 8, "retry": 2},
            "notification_settings": {
                "notify_on_status_change": True,
                "consecutive_failures": 2,
                "notify_on_success": False,
                "notify_on_failure": False,
                "silent_hours_start": -1,
                "silent_hours_end": 7,
            },
        }
    )

    detection_settings = plugin._normalize_detection_settings()
    notification_settings = plugin._normalize_notification_settings()
    assert detection_settings["interval"] == 60
    assert notification_settings["consecutive_failures"] == 2

    plugin.config["detection_settings"]["interval"] = 120
    plugin.config["notification_settings"]["consecutive_failures"] = 5

    updated_detection_settings = plugin._normalize_detection_settings()
    updated_notification_settings = plugin._normalize_notification_settings()
    assert updated_detection_settings["interval"] == 120
    assert updated_notification_settings["consecutive_failures"] == 5


@pytest.mark.asyncio
async def test_check_target_coerces_invalid_retry_value():
    plugin = build_plugin()
    calls = []

    async def fake_check_http(url, timeout, ssl_verify):
        calls.append((url, timeout, ssl_verify))
        return True, ""

    async def fake_update_target_state(target, result):
        return None

    plugin._check_http = fake_check_http
    plugin._update_target_state = fake_update_target_state

    result = await plugin._check_target(
        {
            "name": "site",
            "url": "https://example.com",
            "method": "http",
            "timeout": 5,
            "retry": "oops",
        }
    )

    assert result["success"] is True
    assert len(calls) == 1


def test_save_history_normalizes_max_history_lower_bound():
    plugin = build_plugin({"advanced_settings": {"max_history": -5}})
    plugin._save_history = NetworkConnectivityPlugin._save_history.__get__(
        plugin, NetworkConnectivityPlugin
    )
    plugin.detection_history = {
        "site": [
            {
                "timestamp": i,
                "response_time": 1,
                "success": True,
                "error": None,
            }
            for i in range(5)
        ]
    }

    plugin._save_history()

    assert len(plugin.detection_history["site"]) == 5
    assert (
        plugin._coerce_int(
            -5, 100, "advanced_settings.max_history", minimum=1, maximum=10000
        )
        == 100
    )


def test_save_history_normalizes_max_history_upper_bound():
    plugin = build_plugin({"advanced_settings": {"max_history": 1000000}})
    plugin._save_history = NetworkConnectivityPlugin._save_history.__get__(
        plugin, NetworkConnectivityPlugin
    )
    plugin.detection_history = {
        "site": [
            {
                "timestamp": i,
                "response_time": 1,
                "success": True,
                "error": None,
            }
            for i in range(10005)
        ]
    }

    plugin._save_history()

    assert len(plugin.detection_history["site"]) == 100
    assert plugin.detection_history["site"][0]["timestamp"] == 9905
    assert (
        plugin._coerce_int(
            1000000,
            100,
            "advanced_settings.max_history",
            minimum=1,
            maximum=10000,
        )
        == 100
    )


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
async def test_net_history_time_range_message_mentions_50_item_display_limit():
    plugin = build_plugin()
    plugin.detection_history = {
        "site": [
            {
                "timestamp": datetime(2026, 4, 1, 0, 0, 0).timestamp() + i,
                "success": True,
                "response_time": 100,
                "error": None,
            }
            for i in range(60)
        ]
    }
    event = DummyEvent()

    results = []
    async for item in plugin.net_history(event, "site", "2026-04-01", "2026-04-01"):
        results.append(item)

    assert len(results) == 1
    message = results[0]
    assert "时间范围内共 60 条记录，当前仅展示最近 50 条" in message


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


@pytest.mark.asyncio
async def test_net_history_reports_empty_valid_time_range():
    plugin = build_plugin()
    plugin.detection_history = {
        "site": [
            {
                "timestamp": datetime(2026, 4, 1, 12, 0, 0).timestamp(),
                "success": True,
                "response_time": 120,
                "error": None,
            }
        ]
    }
    event = DummyEvent()

    results = []
    async for item in plugin.net_history(event, "site", "2026-04-02", "2026-04-03"):
        results.append(item)

    assert results == [
        "⚠️ 目标 'site' 在 2026-04-02 00:00:00 到 2026-04-03 23:59:59 之间暂无历史记录"
    ]


@pytest.mark.asyncio
async def test_net_history_rejects_start_after_end():
    plugin = build_plugin()
    plugin.detection_history = {
        "site": [
            {
                "timestamp": datetime(2026, 4, 3, 12, 0, 0).timestamp(),
                "success": True,
                "response_time": 120,
                "error": None,
            }
        ]
    }
    event = DummyEvent()

    results = []
    async for item in plugin.net_history(event, "site", "2026-04-05", "2026-04-03"):
        results.append(item)

    assert results == ["⚠️ 开始时间不能晚于结束时间"]
