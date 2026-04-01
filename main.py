import asyncio
import json
import platform
import re
import time
from datetime import datetime
from typing import Any

import aiohttp

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register


@register("network_connectivity_check", "Fold", "网络连接监测插件", "1.0.0")
class NetworkConnectivityPlugin(Star):
    """网络连接监测插件 - 定时监测网络连接状态，支持多种检测方式"""

    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}

        # 数据存储路径 - 使用 StarTools.get_data_dir() 获取插件数据目录
        self.data_dir = StarTools.get_data_dir("network_connectivity_check")
        self.state_file = self.data_dir / "state.json"
        self.history_file = self.data_dir / "history.json"

        # 确保数据目录存在
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 加载状态和历史记录
        self.target_states = self._load_state()
        self.detection_history = self._load_history()

        # 后台任务
        self.monitor_tasks: dict[str, asyncio.Task] = {}
        self.running = False

        # HTTP session（在 initialize 中创建，复用连接池）
        self.session: aiohttp.ClientSession | None = None
        self._normalized_detection_settings_cache: tuple[str, dict[str, Any]] | None = (
            None
        )
        self._normalized_notification_settings_cache: (
            tuple[str, dict[str, Any]] | None
        ) = None

    def _config_section_cache_key(self, section_name: str) -> str:
        """为配置片段生成缓存 key，内容变更时自动失效。"""
        section = self.config.get(section_name, {})
        try:
            return json.dumps(section, sort_keys=True, ensure_ascii=False)
        except TypeError:
            return repr(section)

    def _coerce_int(
        self,
        value: Any,
        default: int,
        field_name: str,
        minimum: int | None = None,
        maximum: int | None = None,
    ) -> int:
        """将配置值规范化为整数，并在越界时回退到默认值。"""
        try:
            result = int(value)
        except (TypeError, ValueError):
            logger.warning(f"配置项 {field_name} 非法，使用默认值 {default}: {value}")
            return default

        if minimum is not None and result < minimum:
            logger.warning(f"配置项 {field_name} 过小，使用默认值 {default}: {result}")
            return default
        if maximum is not None and result > maximum:
            logger.warning(f"配置项 {field_name} 过大，使用默认值 {default}: {result}")
            return default
        return result

    def _coerce_bool(self, value: Any, default: bool, field_name: str) -> bool:
        """将配置值规范化为布尔值，字符串需显式解析。"""
        if isinstance(value, bool):
            return value

        if isinstance(value, int):
            if value in (0, 1):
                return bool(value)

        if isinstance(value, float):
            if value in (0.0, 1.0):
                return bool(value)

        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"true", "1", "yes", "y", "on"}:
                return True
            if normalized in {"false", "0", "no", "n", "off"}:
                return False

        logger.warning(f"配置项 {field_name} 非法，使用默认值 {default}: {value}")
        return default

    def _normalize_detection_settings(self) -> dict[str, Any]:
        """获取规范化后的全局检测设置。"""
        cache_key = self._config_section_cache_key("detection_settings")
        if self._normalized_detection_settings_cache is not None:
            cached_key, cached_value = self._normalized_detection_settings_cache
            if cached_key == cache_key:
                return cached_value

        settings = self.config.get("detection_settings", {})
        normalized = {
            "interval": self._coerce_int(
                settings.get("interval", 300),
                300,
                "detection_settings.interval",
                minimum=1,
            ),
            "timeout": self._coerce_int(
                settings.get("timeout", 10), 10, "detection_settings.timeout", minimum=1
            ),
            "retry": self._coerce_int(
                settings.get("retry", 3), 3, "detection_settings.retry", minimum=0
            ),
            "ssl_verify": self._coerce_bool(
                settings.get("ssl_verify", True),
                True,
                "detection_settings.ssl_verify",
            ),
        }
        self._normalized_detection_settings_cache = (cache_key, normalized)
        return normalized

    def _normalize_notification_settings(self) -> dict[str, Any]:
        """获取规范化后的通知设置。"""
        cache_key = self._config_section_cache_key("notification_settings")
        if self._normalized_notification_settings_cache is not None:
            cached_key, cached_value = self._normalized_notification_settings_cache
            if cached_key == cache_key:
                return cached_value

        settings = self.config.get("notification_settings", {})
        start = self._coerce_int(
            settings.get("silent_hours_start", -1),
            -1,
            "notification_settings.silent_hours_start",
            minimum=-1,
            maximum=23,
        )
        end = self._coerce_int(
            settings.get("silent_hours_end", 7),
            7,
            "notification_settings.silent_hours_end",
            minimum=0,
            maximum=23,
        )

        normalized = {
            "notify_on_status_change": self._coerce_bool(
                settings.get("notify_on_status_change", True),
                True,
                "notification_settings.notify_on_status_change",
            ),
            "consecutive_failures": self._coerce_int(
                settings.get("consecutive_failures", 2),
                2,
                "notification_settings.consecutive_failures",
                minimum=1,
            ),
            "notify_on_success": self._coerce_bool(
                settings.get("notify_on_success", False),
                False,
                "notification_settings.notify_on_success",
            ),
            "notify_on_failure": self._coerce_bool(
                settings.get("notify_on_failure", False),
                False,
                "notification_settings.notify_on_failure",
            ),
            "silent_hours_start": start,
            "silent_hours_end": end,
        }
        self._normalized_notification_settings_cache = (cache_key, normalized)
        return normalized

    def _parse_history_datetime(self, value: str, is_end: bool = False) -> float | None:
        """解析 history 命令中的时间参数。"""
        value = value.strip()
        formats = [
            ("%Y-%m-%d %H:%M:%S", False),
            ("%Y-%m-%dT%H:%M:%S", False),
            ("%Y-%m-%d", True),
        ]
        for fmt, is_date_only in formats:
            try:
                parsed = datetime.strptime(value, fmt)
                if is_date_only and is_end:
                    parsed = parsed.replace(hour=23, minute=59, second=59)
                return parsed.timestamp()
            except ValueError:
                continue
        return None

    def _load_state(self) -> dict[str, Any]:
        """加载目标状态"""
        if self.state_file.exists():
            try:
                with self.state_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载状态文件失败: {e}")
        return {}

    def _save_state(self):
        """保存目标状态"""
        try:
            with self.state_file.open("w", encoding="utf-8") as f:
                json.dump(self.target_states, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存状态文件失败: {e}")

    def _load_history(self) -> dict[str, list[dict]]:
        """加载检测历史"""
        if self.history_file.exists():
            try:
                with self.history_file.open("r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载历史文件失败: {e}")
        return {}

    def _save_history(self):
        """保存检测历史"""
        try:
            # 限制历史记录数量
            max_history = self._coerce_int(
                self.config.get("advanced_settings", {}).get("max_history", 100),
                100,
                "advanced_settings.max_history",
                minimum=1,
                maximum=10000,
            )
            for target_name in self.detection_history:
                if len(self.detection_history[target_name]) > max_history:
                    self.detection_history[target_name] = self.detection_history[
                        target_name
                    ][-max_history:]

            with self.history_file.open("w", encoding="utf-8") as f:
                json.dump(self.detection_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史文件失败: {e}")

    def _get_target_config(self) -> list[dict]:
        """获取监测目标配置（返回处理后的目标列表）"""
        targets = self.config.get("targets", [])
        processed_targets = []

        # 获取全局检测设置
        detection_settings = self._normalize_detection_settings()
        global_interval = detection_settings["interval"]
        global_timeout = detection_settings["timeout"]
        global_retry = detection_settings["retry"]
        valid_methods = {"http", "ping", "tcp"}

        for target in targets:
            # 深拷贝避免修改原始配置
            processed = dict(target)

            # 跳过空名字的目标
            target_name = processed.get("name", "").strip()
            if not target_name:
                logger.warning(f"跳过空名字的目标配置: {processed}")
                continue

            # 确保 name 是有效的
            processed["name"] = target_name
            method = str(processed.get("method", "http")).lower()
            if method not in valid_methods:
                logger.warning(
                    f"目标 {target_name} 的检测方式非法，回退为 http: {method}"
                )
                method = "http"
            processed["method"] = method

            # 如果没有启用自定义设置，使用全局设置
            if not processed.get("custom_settings", False):
                processed["interval"] = global_interval
                processed["timeout"] = global_timeout
                processed["retry"] = global_retry
            else:
                processed["interval"] = self._coerce_int(
                    processed.get("interval", global_interval),
                    global_interval,
                    f"targets[{target_name}].interval",
                    minimum=1,
                )
                processed["timeout"] = self._coerce_int(
                    processed.get("timeout", global_timeout),
                    global_timeout,
                    f"targets[{target_name}].timeout",
                    minimum=1,
                )
                processed["retry"] = self._coerce_int(
                    processed.get("retry", global_retry),
                    global_retry,
                    f"targets[{target_name}].retry",
                    minimum=0,
                )

            processed_targets.append(processed)

        return processed_targets

    def _get_notify_targets(self) -> list[dict]:
        """获取通知目标列表"""
        return self.config.get("notify_targets", [])

    def _get_notification_settings(self) -> dict:
        """获取通知全局设置"""
        return self._normalize_notification_settings()

    def _is_silent_hours(self) -> bool:
        """检查当前是否在免打扰时段"""
        notification_settings = self._get_notification_settings()
        start = notification_settings.get("silent_hours_start", -1)
        end = notification_settings.get("silent_hours_end", 7)

        if start < 0:
            return False

        current_hour = datetime.now().hour
        if start <= end:
            return start <= current_hour < end
        else:  # 跨天的情况，如 22:00 - 07:00
            return current_hour >= start or current_hour < end

    def _add_umo_to_notify_targets(self, umo: str, description: str = "") -> bool:
        """添加UMO到通知目标列表"""
        notify_targets = self._get_notify_targets()

        # 检查是否已存在
        for target in notify_targets:
            if target.get("umo") == umo:
                logger.debug(f"UMO {umo} 已存在于通知目标列表中")
                return False

        # 添加到列表（template_list 需要 __template_key 字段）
        notify_targets.append(
            {
                "__template_key": "notify_target",
                "umo": umo,
                "description": description
                or f"添加于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            }
        )

        # 更新配置
        self.config["notify_targets"] = notify_targets
        # 防护检查：确保 config 对象有 save_config 方法
        if hasattr(self.config, "save_config"):
            self.config.save_config()
            logger.info(f"已添加通知目标: {umo} ({description})")
        else:
            logger.warning(f"已添加通知目标到内存，但无法保存配置: {umo}")
        return True

    async def initialize(self):
        """插件初始化 - 启动后台监测任务"""
        # 幂等保护：如果已经启动过，不再重复初始化
        if self.running or self.monitor_tasks:
            logger.warning(
                f"网络监测插件已经初始化，跳过重复初始化。当前任务数: {len(self.monitor_tasks)}"
            )
            return

        self.running = True

        # 创建全局 HTTP session（复用连接池）
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
            logger.debug("已创建 HTTP ClientSession")

        targets = self._get_target_config()
        notify_targets = self._get_notify_targets()

        logger.info(
            f"网络监测插件初始化，配置目标数: {len(targets)}, 通知目标数: {len(notify_targets)}"
        )

        if not targets:
            logger.warning("网络监测插件：未配置任何监测目标")
            self.running = False  # 恢复状态，允许后续重新初始化
            return

        # 为每个目标启动监测任务
        for target in targets:
            target_name = target.get("name", "unknown")
            interval = target.get("interval", 300)
            logger.debug(
                f"配置目标: {target_name}, URL: {target.get('url')}, "
                f"方法: {target.get('method')}, 间隔: {interval}s, "
                f"超时: {target.get('timeout')}s, 重试: {target.get('retry')}次"
            )

            # 如果同名任务已存在，先取消旧任务（防止重复和幽灵任务）
            if target_name in self.monitor_tasks:
                old_task = self.monitor_tasks[target_name]
                old_task.cancel()
                logger.warning(f"发现同名监测任务 {target_name}，已取消旧任务")

            task = asyncio.create_task(
                self._monitor_target(target), name=f"monitor_{target_name}"
            )
            self.monitor_tasks[target_name] = task
            logger.info(f"启动监测任务: {target_name} (每 {interval} 秒检测一次)")

    async def terminate(self):
        """插件销毁 - 停止所有监测任务"""
        self.running = False

        # 取消所有监测任务
        for target_name, task in self.monitor_tasks.items():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            logger.info(f"停止监测任务: {target_name}")

        self.monitor_tasks.clear()

        # 关闭 HTTP session
        if self.session and not self.session.closed:
            await self.session.close()
            logger.debug("已关闭 HTTP ClientSession")

        # 保存状态和历史
        self._save_state()
        self._save_history()

    async def _monitor_target(self, target: dict):
        """监测单个目标的后台任务"""
        target_name = target.get("name", "").strip()
        if not target_name:
            target_name = "未命名目标"
            logger.warning("监测任务启动时发现空名字目标，使用默认名称")

        interval = target.get("interval", 300)

        logger.info(f"监测任务 {target_name} 已启动，检测间隔: {interval} 秒")

        # 初始化状态
        if target_name not in self.target_states:
            self.target_states[target_name] = {
                "last_status": None,  # None: 未知, True: 正常, False: 异常
                "consecutive_failures": 0,
                "last_check_time": None,
                "last_response_time": None,
            }

        while self.running:
            try:
                logger.debug(f"[定时任务] 开始检测目标: {target_name}")
                result = await self._check_target(target)
                logger.debug(
                    f"[定时任务] 目标 {target_name} 检测完成: "
                    f"{'成功' if result['success'] else '失败'}, "
                    f"响应时间: {result.get('response_time', '-')}ms"
                )
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info(f"监测任务 {target_name} 已取消")
                break
            except Exception as e:
                logger.error(f"监测目标 {target_name} 时出错: {e}", exc_info=True)
                await asyncio.sleep(60)  # 出错后等待60秒再继续

    async def _check_target(self, target: dict) -> dict:
        """检测单个目标"""
        target_name = target.get("name", "").strip()
        if not target_name:
            target_name = "未命名目标"
            logger.warning("检测到空名字目标，使用默认名称")

        url = target.get("url", "")
        method = target.get("method", "http")
        timeout = target.get("timeout", 10)
        retry = self._coerce_int(
            target.get("retry", 3),
            3,
            f"targets[{target_name}].retry",
            minimum=0,
        )

        # 获取 SSL 验证配置
        detection_settings = self._normalize_detection_settings()
        ssl_verify = detection_settings["ssl_verify"]

        logger.debug(
            f"开始检测目标: {target_name}, URL: {url}, 方法: {method}, "
            f"超时: {timeout}s, 最大重试: {retry}次, SSL验证: {ssl_verify}"
        )

        # 确保状态字典存在
        if target_name not in self.target_states:
            self.target_states[target_name] = {
                "last_status": None,
                "consecutive_failures": 0,
                "last_check_time": None,
                "last_response_time": None,
            }

        result = {
            "target": target_name,
            "url": url,
            "timestamp": time.time(),
            "success": False,
            "response_time": None,
            "error": None,
        }

        # 执行检测
        attempt = 0
        for attempt in range(retry + 1):
            try:
                start_time = time.time()

                if method == "http":
                    logger.debug(
                        f"[{target_name}] HTTP 检测尝试 {attempt + 1}/{retry + 1}"
                    )
                    success, error_msg = await self._check_http(
                        url, timeout, ssl_verify
                    )
                elif method == "ping":
                    logger.debug(
                        f"[{target_name}] Ping 检测尝试 {attempt + 1}/{retry + 1}"
                    )
                    success, error_msg = await self._check_ping(url, timeout)
                elif method == "tcp":
                    logger.debug(
                        f"[{target_name}] TCP 检测尝试 {attempt + 1}/{retry + 1}"
                    )
                    success, error_msg = await self._check_tcp(url, timeout)
                else:
                    result["error"] = f"未知的检测方法: {method}"
                    logger.error(f"[{target_name}] {result['error']}")
                    break

                result["response_time"] = round(
                    (time.time() - start_time) * 1000, 2
                )  # 毫秒
                result["success"] = success

                if success:
                    result["error"] = None  # 成功时清除错误
                    logger.debug(
                        f"[{target_name}] 检测成功，响应时间: {result['response_time']}ms"
                    )
                    break  # 成功则跳出重试
                else:
                    # 检测失败时设置错误信息
                    result["error"] = error_msg or f"{method.upper()} 检测失败"
                    logger.debug(f"[{target_name}] 检测失败: {error_msg}，准备重试...")
                    if attempt < retry:
                        await asyncio.sleep(1)  # 重试前等待1秒

            except Exception as e:
                result["error"] = f"检测异常: {str(e)[:100]}"
                logger.debug(f"[{target_name}] 检测异常: {e}")
                if attempt < retry:
                    await asyncio.sleep(1)  # 重试前等待1秒

        logger.info(
            f"目标 {target_name} 检测完成: {'成功' if result['success'] else '失败'}, "
            f"响应: {result.get('response_time', '-')}ms, "
            f"重试: {attempt + 1}次"
        )

        # 更新状态
        await self._update_target_state(target, result)

        # 保存历史记录
        if target_name not in self.detection_history:
            self.detection_history[target_name] = []
        self.detection_history[target_name].append(result)
        self._save_history()

        return result

    async def _check_http(
        self, url: str, timeout: int, ssl_verify: bool = True
    ) -> tuple[bool, str]:
        """HTTP 检测，返回 (成功状态, 错误信息)，复用 ClientSession"""
        ssl_context = None if ssl_verify else False
        last_error = ""
        should_close_session = False

        # 使用全局 session（复用连接池）
        session = self.session
        if session is None or session.closed:
            logger.warning("HTTP session 未初始化或已关闭，创建临时 session")
            session = aiohttp.ClientSession()
            should_close_session = True

        try:
            # 先尝试 HEAD 请求
            try:
                logger.debug(
                    f"HTTP HEAD 请求: {url}, 超时: {timeout}s, SSL验证: {ssl_verify}"
                )
                async with session.head(url, timeout=timeout, ssl=ssl_context) as resp:
                    logger.debug(f"HTTP HEAD 响应状态码: {resp.status}")
                    if 200 <= resp.status < 400:
                        return True, ""
                    else:
                        last_error = f"HTTP 状态码异常: {resp.status}"
            except asyncio.TimeoutError:
                last_error = "HTTP 请求超时"
            except aiohttp.ClientError as e:
                last_error = f"HTTP 连接错误: {type(e).__name__}"
            except Exception as e:
                last_error = f"HTTP 请求异常: {str(e)[:50]}"

            # HEAD 失败时尝试 GET
            try:
                logger.debug(f"HTTP HEAD 失败: {last_error}，尝试 GET 请求")
                async with session.get(url, timeout=timeout, ssl=ssl_context) as resp:
                    logger.debug(f"HTTP GET 响应状态码: {resp.status}")
                    if 200 <= resp.status < 400:
                        return True, ""
                    else:
                        return False, f"HTTP 状态码异常: {resp.status}"
            except asyncio.TimeoutError:
                return False, "HTTP 请求超时"
            except aiohttp.ClientError:
                return False, "HTTP 连接被拒绝或无法访问"
            except Exception as e:
                return False, f"HTTP 请求失败: {str(e)[:50]}"
        except Exception as e:
            return False, f"HTTP 会话失败: {str(e)[:50]}"
        finally:
            if should_close_session and not session.closed:
                await session.close()

    async def _check_ping(self, host: str, timeout: int) -> tuple[bool, str]:
        """Ping 检测，返回 (成功状态, 错误信息)，支持跨平台"""
        try:
            # 移除协议头并解析主机名
            original_host = host
            if host.startswith(("http://", "https://")):
                from urllib.parse import urlparse

                parsed = urlparse(host)
                host = parsed.hostname or ""
            elif host.startswith("["):
                # IPv6 格式 [::1]
                bracket_end = host.find("]")
                if bracket_end != -1:
                    host = host[1:bracket_end]  # 去掉方括号
            elif ":" in host and host.count(":") > 2:
                # 可能是 IPv6 地址
                if not host.startswith("["):
                    # 为 Ping 命令添加方括号（某些系统需要）
                    host = host
            elif ":" in host:
                # 普通 host:port 格式，去掉端口
                host = host.split(":")[0]

            # 验证 host 参数，防止命令注入（确保不以 - 开头）
            if not host or host.startswith("-"):
                return False, f"Ping 主机地址无效或包含非法字符: '{host}'"

            # 进一步验证：只允许合法的域名、IPv4、IPv6 字符
            # 支持字母数字、点、冒号（IPv6）、连字符（域名）
            if not re.match(r"^[a-zA-Z0-9\.\:\-]+$", host):
                return False, f"Ping 主机地址包含非法字符: '{host}'"

            logger.debug(f"Ping 检测: {host} (原始: {original_host}), 超时: {timeout}s")

            # 根据平台选择参数
            if platform.system().lower() == "windows":
                # Windows: -n 次数, -w 超时(毫秒)
                ping_cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
            else:
                # Linux/macOS: -c 次数, -W 超时(秒)
                ping_cmd = ["ping", "-c", "1", "-W", str(timeout), host]

            proc = await asyncio.create_subprocess_exec(
                *ping_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout + 2)
                success = proc.returncode == 0
                logger.debug(
                    f"Ping 结果: {host} - {'成功' if success else '失败'} (returncode: {proc.returncode})"
                )

                if success:
                    return True, ""
                else:
                    return False, "Ping 失败（主机不可达或请求超时）"
            except asyncio.TimeoutError:
                # 超时后强制终止子进程，避免僵尸进程
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=2)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
                logger.debug(f"Ping 超时并终止: {host}")
                return False, "Ping 超时"
        except asyncio.TimeoutError:
            return False, "Ping 超时"
        except Exception as e:
            logger.debug(f"Ping 异常: {e}")
            return False, f"Ping 执行失败: {str(e)[:50]}"

    async def _check_tcp(self, url: str, timeout: int) -> tuple[bool, str]:
        """TCP 连接检测，返回 (成功状态, 错误信息)，支持 IPv6"""
        host = ""
        port = 0
        try:
            # 解析主机和端口，支持 IPv6
            if url.startswith(("http://", "https://")):
                from urllib.parse import urlparse

                parsed = urlparse(url)
                host = parsed.hostname or ""
                port = parsed.port or (443 if url.startswith("https://") else 80)
            else:
                # 处理 IPv6 格式 [::1]:80 或普通 host:port
                if url.startswith("["):
                    # IPv6 格式 [::1]:80
                    bracket_end = url.find("]")
                    if bracket_end == -1:
                        return False, "TCP IPv6 地址格式错误: 缺少闭合方括号"
                    host = url[1:bracket_end]  # 去掉方括号，只取地址部分
                    port_part = url[bracket_end + 1 :]
                    if port_part.startswith(":"):
                        try:
                            port = int(port_part[1:])
                        except ValueError:
                            return False, f"TCP 端口格式非法: '{port_part[1:]}'"
                    else:
                        port = 80
                elif ":" in url:
                    # 可能是 IPv6 无括号，或普通 host:port
                    if url.count(":") > 2:
                        # 大概率是 IPv6 地址没有括号，尝试解析
                        host = url
                        port = 80
                    else:
                        # 普通 host:port 格式
                        host, port_str = url.rsplit(":", 1)
                        try:
                            port = int(port_str)
                        except ValueError:
                            return False, f"TCP 端口格式非法: '{port_str}'"
                else:
                    host = url
                    port = 80

            if not host:
                return False, "TCP 主机地址不能为空"
            if port < 1 or port > 65535:
                return False, f"TCP 端口超出范围: {port}"

            logger.debug(f"TCP 连接检测: {host}:{port}, 超时: {timeout}s")

            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            logger.debug(f"TCP 连接成功: {host}:{port}")
            return True, ""
        except asyncio.TimeoutError:
            return False, f"TCP 连接超时（端口 {port} 无响应）"
        except ConnectionRefusedError:
            return False, f"TCP 连接被拒绝（端口 {port} 已关闭）"
        except OSError as e:
            return False, f"TCP 连接失败: {str(e)[:50]}"
        except Exception as e:
            logger.debug(f"TCP 连接失败: {host}:{port} - {e}")
            return False, f"TCP 连接失败: {str(e)[:50]}"

    async def _update_target_state(self, target: dict, result: dict):
        """更新目标状态并决定是否发送通知"""
        # 使用 target 参数获取名字，确保不为空
        target_name = target.get("name", "").strip()
        if not target_name:
            target_name = "未命名目标"
            logger.warning(f"检测到空名字目标，使用默认名称: {result}")

        # 确保状态字典存在
        if target_name not in self.target_states:
            self.target_states[target_name] = {
                "last_status": None,
                "consecutive_failures": 0,
                "last_check_time": None,
                "last_response_time": None,
            }

        state = self.target_states[target_name]
        notification_settings = self._get_notification_settings()

        prev_status = state["last_status"]
        # 更新基本信息
        state["last_check_time"] = result["timestamp"]
        state["last_response_time"] = result["response_time"]

        # 处理连续失败计数
        if result["success"]:
            state["consecutive_failures"] = 0
        else:
            state["consecutive_failures"] += 1

        # 判断状态变化
        new_status = result["success"]
        state["last_status"] = new_status

        logger.debug(
            f"[{target_name}] 状态更新: 之前={prev_status}, 现在={new_status}, "
            f"连续失败={state['consecutive_failures']}"
        )

        # 保存状态
        self._save_state()

        # 获取通知设置
        notify_on_status_change = notification_settings.get(
            "notify_on_status_change", True
        )
        notify_on_success = notification_settings.get("notify_on_success", False)
        notify_on_failure = notification_settings.get("notify_on_failure", False)
        consecutive_failures_threshold = notification_settings.get(
            "consecutive_failures", 2
        )

        # 判断状态是否发生变化（None→True/False 也算变化）
        status_changed = (prev_status is not None and new_status != prev_status) or (
            prev_status is None
        )  # 首次检测也算状态变化

        should_notify = False
        message = ""
        notify_reason = ""  # 记录通知原因，用于日志

        if new_status:
            # ===== 检测成功的情况 =====
            if status_changed and notify_on_status_change:
                # 状态从失败 变为 成功，且开启了状态变化通知
                # 恢复时立即通知，不受连续失败限制
                should_notify = True
                notify_reason = "状态变化（恢复）"
                if prev_status is False:
                    message = f"✅ [{target_name}] 网络已恢复正常！\n响应时间: {result['response_time']}ms\n（已从异常状态恢复）"
                else:
                    message = f"✅ [{target_name}] 网络检测正常\n响应时间: {result['response_time']}ms"
            elif notify_on_success:
                # 开启了每次成功都通知（不受限制）
                should_notify = True
                notify_reason = "每次成功通知"
                message = f"✅ [{target_name}] 网络检测正常\n响应时间: {result['response_time']}ms"
        else:
            # ===== 检测失败的情况 =====
            error_msg = result.get("error") or "连接超时或无法访问"

            # 优先检查"每次失败都通知"（独立于状态变化通知）
            if notify_on_failure:
                # 开启了每次失败都通知（不受限制，优先执行）
                should_notify = True
                notify_reason = "每次失败通知"
                message = f"❌ [{target_name}] 网络连接异常！\n错误: {error_msg}\n已连续失败 {state['consecutive_failures']} 次"
            elif notify_on_status_change:
                # 开启了状态变化通知
                # 需要满足：1) 达到阈值；2) 首次达到阈值（状态刚变化 或 连续失败次数刚好等于阈值）
                if state["consecutive_failures"] >= consecutive_failures_threshold:
                    # 检查是否是首次达到阈值：状态变化了，或者连续失败次数刚好等于阈值
                    is_first_time_reaching_threshold = status_changed or (
                        state["consecutive_failures"] == consecutive_failures_threshold
                    )

                    if is_first_time_reaching_threshold:
                        should_notify = True
                        notify_reason = "状态变化（异常，首次达到阈值）"
                        message = f"❌ [{target_name}] 网络连接异常！\n错误: {error_msg}\n已连续失败 {state['consecutive_failures']} 次"
                    else:
                        logger.debug(f"[{target_name}] 已达到阈值但非首次，跳过通知")
                else:
                    logger.debug(
                        f"[{target_name}] 连续失败 {state['consecutive_failures']} "
                        f"未达到阈值 {consecutive_failures_threshold}，暂不通知"
                    )

        # 发送通知
        if should_notify:
            if self._is_silent_hours():
                logger.info(
                    f"[{target_name}] 应发送通知（{notify_reason}），但当前处于免打扰时段，跳过"
                )
            else:
                logger.info(f"[{target_name}] 发送通知: {notify_reason}")
                await self._send_notification(message)
        else:
            logger.debug(
                f"[{target_name}] 不发送通知: 状态变化={status_changed}, "
                f"成功={new_status}, "
                f"状态变化通知={notify_on_status_change}, "
                f"成功通知={notify_on_success}, "
                f"失败通知={notify_on_failure}"
            )

    async def _send_notification(self, message: str):
        """发送通知到所有配置的通知目标"""
        notify_targets = self._get_notify_targets()

        if not notify_targets:
            logger.warning("未配置通知目标，无法发送通知")
            return

        # 构建消息链
        from astrbot.api.event import MessageChain

        chain = MessageChain([Comp.Plain(message)])

        logger.debug(f"准备发送通知到 {len(notify_targets)} 个目标: {message[:50]}...")

        for target in notify_targets:
            umo = target.get("umo")
            if not umo:
                logger.debug("跳过空的 UMO")
                continue
            try:
                await self.context.send_message(umo, chain)
                logger.info(f"已发送通知到 {umo}")
            except Exception as e:
                logger.error(f"发送通知到 {umo} 失败: {e}")

    @filter.command_group("net")
    def net(self, event: AstrMessageEvent):
        """网络监测指令组"""
        pass

    @net.command("addme")
    async def net_addme(self, event: AstrMessageEvent, description: str = ""):
        """将当前聊天添加到通知目标列表
        描述：可选的描述信息，用于标识此通知目标"""
        umo = event.unified_msg_origin
        group_id = event.get_group_id()
        sender_id = event.get_sender_id()

        logger.info(
            f"执行 /net addme, UMO: {umo}, 发送者: {sender_id}, 群组: {group_id}"
        )

        # 自动生成描述
        if not description:
            if group_id:
                description = f"群聊 {group_id}"
            else:
                description = f"用户 {sender_id}"

        added = self._add_umo_to_notify_targets(umo, description)

        if added:
            logger.info(f"成功添加通知目标: {umo}")
            yield event.plain_result(
                f"✅ 已添加此聊天到通知目标列表\nUMO: {umo}\n描述: {description}"
            )
        else:
            logger.info(f"通知目标已存在: {umo}")
            yield event.plain_result("ℹ️ 此聊天已在通知目标列表中")

    @net.command("check")
    async def net_check(self, event: AstrMessageEvent, target_name: str = ""):
        """手动执行一次网络检测
        目标：要检测的目标名称，留空则检测所有目标"""
        logger.info(
            f"执行 /net check, 发送者: {event.get_sender_id()}, 目标: {target_name or '全部'}"
        )

        targets = self._get_target_config()

        if not targets:
            logger.warning("未配置任何监测目标")
            yield event.plain_result("⚠️ 未配置任何监测目标，请在 WebUI 中配置")
            return

        # 筛选目标
        if target_name:
            targets_to_check = [t for t in targets if t.get("name") == target_name]
            if not targets_to_check:
                available = ", ".join([t.get("name") for t in targets])
                logger.warning(f"未找到目标: {target_name}")
                yield event.plain_result(
                    f"⚠️ 未找到目标 '{target_name}'\n可用目标: {available}"
                )
                return
        else:
            targets_to_check = targets

        logger.info(f"开始手动检测 {len(targets_to_check)} 个目标")
        yield event.plain_result(f"🔄 正在检测 {len(targets_to_check)} 个目标...")

        # 执行检测
        results = []
        for target in targets_to_check:
            result = await self._check_target(target)
            results.append(result)

        # 生成结果消息
        messages = []
        success_count = sum(1 for r in results if r["success"])
        for r in results:
            status = "✅ 正常" if r["success"] else "❌ 异常"
            resp_time = f" ({r['response_time']}ms)" if r["response_time"] else ""
            error = f"\n错误: {r['error']}" if r.get("error") else ""
            messages.append(f"[{r['target']}]{resp_time} - {status}{error}")

        logger.info(f"手动检测完成: {success_count}/{len(results)} 成功")
        yield event.plain_result("\n".join(messages))

    @net.command("status")
    async def net_status(self, event: AstrMessageEvent):
        """查看所有监测目标的当前状态"""
        logger.info(f"执行 /net status, 发送者: {event.get_sender_id()}")

        targets = self._get_target_config()

        if not targets:
            logger.warning("查看状态时未配置任何监测目标")
            yield event.plain_result("⚠️ 未配置任何监测目标")
            return

        logger.debug(f"获取 {len(targets)} 个目标的状态")
        messages = ["📊 网络监测状态\n" + "=" * 30]

        for target in targets:
            target_name = target.get("name", "unknown")
            url = target.get("url", "")
            state = self.target_states.get(target_name, {})

            # 状态图标
            last_status = state.get("last_status")
            if last_status is True:
                status_icon = "🟢"
                status_text = "正常"
            elif last_status is False:
                status_icon = "🔴"
                status_text = "异常"
            else:
                status_icon = "⚪"
                status_text = "未检测"

            # 响应时间
            resp_time = state.get("last_response_time")
            resp_text = f"{resp_time}ms" if resp_time else "-"

            # 最后检测时间
            last_check = state.get("last_check_time")
            if last_check:
                last_check_str = datetime.fromtimestamp(last_check).strftime(
                    "%m-%d %H:%M"
                )
            else:
                last_check_str = "从未"

            # 连续失败次数
            failures = state.get("consecutive_failures", 0)
            failure_text = f" (连续失败{failures}次)" if failures > 0 else ""

            messages.append(
                f"{status_icon} {target_name}\n"
                f"   URL: {url}\n"
                f"   状态: {status_text}{failure_text}\n"
                f"   响应: {resp_text}\n"
                f"   上次检测: {last_check_str}"
            )

        # 免打扰状态
        if self._is_silent_hours():
            messages.append("\n🔇 当前处于免打扰时段")

        # 通知目标数量
        notify_count = len(self._get_notify_targets())
        messages.append(f"\n📢 通知目标: {notify_count}个")

        yield event.plain_result("\n".join(messages))

    @net.command("history")
    async def net_history(
        self,
        event: AstrMessageEvent,
        target_name: str = "",
        arg1: str = "",
        arg2: str = "",
    ):
        """查看指定目标的检测历史
        目标：目标名称
        参数1：数量，或开始时间
        参数2：结束时间（仅时间范围查询时使用）"""
        logger.info(
            f"执行 /net history, 发送者: {event.get_sender_id()}, 目标: {target_name or '未指定'}, 参数1: {arg1 or '-'}, 参数2: {arg2 or '-'}"
        )

        count = 5
        start_ts = None
        end_ts = None

        if arg1:
            if arg2:
                start_ts = self._parse_history_datetime(arg1)
                end_ts = self._parse_history_datetime(arg2, is_end=True)
                if start_ts is None or end_ts is None:
                    yield event.plain_result(
                        "⚠️ 时间格式错误，支持 YYYY-MM-DD、YYYY-MM-DD HH:MM:SS 或 YYYY-MM-DDTHH:MM:SS"
                    )
                    return
                if start_ts > end_ts:
                    yield event.plain_result("⚠️ 开始时间不能晚于结束时间")
                    return
            else:
                try:
                    count = int(arg1)
                except ValueError:
                    yield event.plain_result(
                        "⚠️ 参数格式错误。用法:\n/net history <目标名> [数量]\n/net history <目标名> <开始时间> <结束时间>"
                    )
                    return

        if count < 1 or count > 20:
            count = 5

        # 如果没有指定目标，列出所有可用目标
        if not target_name:
            available_targets = list(self.detection_history.keys())
            logger.debug(f"可用历史目标: {available_targets}")
            if not available_targets:
                yield event.plain_result("暂无历史记录")
                return

            target_list = "\n".join([f"- {name}" for name in available_targets])
            yield event.plain_result(
                f"请指定目标名称:\n{target_list}\n\n使用:\n/net history <目标名> [数量]\n/net history <目标名> <开始时间> <结束时间>"
            )
            return

        history = self.detection_history.get(target_name, [])

        if not history:
            yield event.plain_result(f"⚠️ 目标 '{target_name}' 暂无历史记录")
            return

        if start_ts is not None and end_ts is not None:
            filtered_history = [
                record
                for record in history
                if start_ts <= record.get("timestamp", 0) <= end_ts
            ]
            if not filtered_history:
                start_str = datetime.fromtimestamp(start_ts).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                end_str = datetime.fromtimestamp(end_ts).strftime("%Y-%m-%d %H:%M:%S")
                yield event.plain_result(
                    f"⚠️ 目标 '{target_name}' 在 {start_str} 到 {end_str} 之间暂无历史记录"
                )
                return
            recent = filtered_history[-50:]
            range_text = (
                f"{datetime.fromtimestamp(start_ts).strftime('%Y-%m-%d %H:%M:%S')}"
                f" ~ {datetime.fromtimestamp(end_ts).strftime('%Y-%m-%d %H:%M:%S')}"
            )
            messages = [
                f"📈 {target_name} - 时间范围内共 {len(filtered_history)} 条记录，当前仅展示最近 {len(recent)} 条\n范围: {range_text}\n"
                + "=" * 30
            ]
        else:
            # 取最近几条
            recent = history[-count:]
            messages = [f"📈 {target_name} - 最近{len(recent)}次检测记录\n" + "=" * 30]

        for record in reversed(recent):  # 倒序显示，最新的在前面
            timestamp = datetime.fromtimestamp(record["timestamp"]).strftime(
                "%m-%d %H:%M:%S"
            )
            status = "✅ 正常" if record["success"] else "❌ 异常"
            resp_time = (
                f"{record['response_time']}ms" if record.get("response_time") else "-"
            )
            error = f" | {record['error'][:30]}..." if record.get("error") else ""

            messages.append(f"{timestamp} | {status} | {resp_time}{error}")

        yield event.plain_result("\n".join(messages))
