import asyncio
import json
import time
from datetime import datetime
from typing import Dict, List, Optional, Any
import os

import aiohttp
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
import astrbot.api.message_components as Comp


@register("network_connectivity_check", "Fold", "网络连接监测插件", "1.0.0")
class NetworkConnectivityPlugin(Star):
    """网络连接监测插件 - 定时监测网络连接状态，支持多种检测方式"""
    
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        
        # 数据存储路径
        self.data_dir = os.path.join("data", "network_connectivity_check")
        self.state_file = os.path.join(self.data_dir, "state.json")
        self.history_file = os.path.join(self.data_dir, "history.json")
        
        # 确保数据目录存在
        os.makedirs(self.data_dir, exist_ok=True)
        
        # 加载状态和历史记录
        self.target_states = self._load_state()
        self.detection_history = self._load_history()
        
        # 后台任务
        self.monitor_tasks: Dict[str, asyncio.Task] = {}
        self.running = False
        
    def _load_state(self) -> Dict[str, Any]:
        """加载目标状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载状态文件失败: {e}")
        return {}
    
    def _save_state(self):
        """保存目标状态"""
        try:
            with open(self.state_file, 'w', encoding='utf-8') as f:
                json.dump(self.target_states, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存状态文件失败: {e}")
    
    def _load_history(self) -> Dict[str, List[Dict]]:
        """加载检测历史"""
        if os.path.exists(self.history_file):
            try:
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载历史文件失败: {e}")
        return {}
    
    def _save_history(self):
        """保存检测历史"""
        try:
            # 限制历史记录数量
            max_history = self.config.get("advanced_settings", {}).get("max_history", 100)
            for target_name in self.detection_history:
                if len(self.detection_history[target_name]) > max_history:
                    self.detection_history[target_name] = self.detection_history[target_name][-max_history:]
            
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.detection_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"保存历史文件失败: {e}")
    
    def _get_target_config(self) -> List[Dict]:
        """获取监测目标配置（返回处理后的目标列表）"""
        targets = self.config.get("targets", [])
        processed_targets = []
        
        # 获取全局检测设置
        detection_settings = self.config.get("detection_settings", {})
        global_interval = detection_settings.get("interval", 300)
        global_timeout = detection_settings.get("timeout", 10)
        global_retry = detection_settings.get("retry", 3)
        
        for target in targets:
            # 深拷贝避免修改原始配置
            processed = dict(target)
            
            # 如果没有启用自定义设置，使用全局设置
            if not processed.get("custom_settings", False):
                processed["interval"] = global_interval
                processed["timeout"] = global_timeout
                processed["retry"] = global_retry
            
            processed_targets.append(processed)
        
        return processed_targets
    
    def _get_notify_targets(self) -> List[Dict]:
        """获取通知目标列表"""
        return self.config.get("notify_targets", [])
    
    def _get_notification_settings(self) -> Dict:
        """获取通知全局设置"""
        return self.config.get("notification_settings", {})
    
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
        notify_targets.append({
            "__template_key": "notify_target",
            "umo": umo,
            "description": description or f"添加于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        })
        
        # 更新配置
        self.config["notify_targets"] = notify_targets
        self.config.save_config()
        logger.info(f"已添加通知目标: {umo} ({description})")
        return True
    
    async def initialize(self):
        """插件初始化 - 启动后台监测任务"""
        self.running = True
        targets = self._get_target_config()
        notify_targets = self._get_notify_targets()
        
        logger.info(f"网络监测插件初始化，配置目标数: {len(targets)}, 通知目标数: {len(notify_targets)}")
        
        if not targets:
            logger.warning("网络监测插件：未配置任何监测目标")
            return
        
        # 为每个目标启动监测任务
        for target in targets:
            target_name = target.get("name", "unknown")
            interval = target.get("interval", 300)
            logger.debug(f"配置目标: {target_name}, URL: {target.get('url')}, "
                        f"方法: {target.get('method')}, 间隔: {interval}s, "
                        f"超时: {target.get('timeout')}s, 重试: {target.get('retry')}次")
            
            task = asyncio.create_task(
                self._monitor_target(target),
                name=f"monitor_{target_name}"
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
        
        # 保存状态和历史
        self._save_state()
        self._save_history()
    
    async def _monitor_target(self, target: Dict):
        """监测单个目标的后台任务"""
        target_name = target.get("name", "unknown")
        interval = target.get("interval", 300)
        
        logger.info(f"监测任务 {target_name} 已启动，检测间隔: {interval} 秒")
        
        # 初始化状态
        if target_name not in self.target_states:
            self.target_states[target_name] = {
                "last_status": None,  # None: 未知, True: 正常, False: 异常
                "consecutive_failures": 0,
                "last_check_time": None,
                "last_response_time": None
            }
        
        while self.running:
            try:
                logger.debug(f"[定时任务] 开始检测目标: {target_name}")
                result = await self._check_target(target)
                logger.debug(f"[定时任务] 目标 {target_name} 检测完成: "
                            f"{'成功' if result['success'] else '失败'}, "
                            f"响应时间: {result.get('response_time', '-')}ms")
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                logger.info(f"监测任务 {target_name} 已取消")
                break
            except Exception as e:
                logger.error(f"监测目标 {target_name} 时出错: {e}", exc_info=True)
                await asyncio.sleep(60)  # 出错后等待60秒再继续
    
    async def _check_target(self, target: Dict) -> Dict:
        """检测单个目标"""
        target_name = target.get("name", "unknown")
        url = target.get("url", "")
        method = target.get("method", "http")
        timeout = target.get("timeout", 10)
        retry = target.get("retry", 3)
        
        logger.debug(f"开始检测目标: {target_name}, URL: {url}, 方法: {method}, "
                    f"超时: {timeout}s, 最大重试: {retry}次")
        
        state = self.target_states[target_name]
        result = {
            "target": target_name,
            "url": url,
            "timestamp": time.time(),
            "success": False,
            "response_time": None,
            "error": None
        }
        
        # 执行检测
        for attempt in range(retry + 1):
            try:
                start_time = time.time()
                
                if method == "http":
                    logger.debug(f"[{target_name}] HTTP 检测尝试 {attempt + 1}/{retry + 1}")
                    success = await self._check_http(url, timeout)
                elif method == "ping":
                    logger.debug(f"[{target_name}] Ping 检测尝试 {attempt + 1}/{retry + 1}")
                    success = await self._check_ping(url, timeout)
                elif method == "tcp":
                    logger.debug(f"[{target_name}] TCP 检测尝试 {attempt + 1}/{retry + 1}")
                    success = await self._check_tcp(url, timeout)
                else:
                    result["error"] = f"未知的检测方法: {method}"
                    logger.error(f"[{target_name}] {result['error']}")
                    break
                
                result["response_time"] = round((time.time() - start_time) * 1000, 2)  # 毫秒
                result["success"] = success
                
                if success:
                    logger.debug(f"[{target_name}] 检测成功，响应时间: {result['response_time']}ms")
                    break  # 成功则跳出重试
                else:
                    logger.debug(f"[{target_name}] 检测失败，准备重试...")
                    if attempt < retry:
                        await asyncio.sleep(1)  # 重试前等待1秒
                    
            except Exception as e:
                result["error"] = str(e)
                logger.debug(f"[{target_name}] 检测异常: {e}")
                if attempt < retry:
                    await asyncio.sleep(1)  # 重试前等待1秒
        
        logger.info(f"目标 {target_name} 检测完成: {'成功' if result['success'] else '失败'}, "
                   f"响应: {result.get('response_time', '-')}ms, "
                   f"重试: {attempt + 1}次")
        
        # 更新状态
        await self._update_target_state(target, result)
        
        # 保存历史记录
        if target_name not in self.detection_history:
            self.detection_history[target_name] = []
        self.detection_history[target_name].append(result)
        self._save_history()
        
        return result
    
    async def _check_http(self, url: str, timeout: int) -> bool:
        """HTTP 检测"""
        try:
            logger.debug(f"HTTP HEAD 请求: {url}, 超时: {timeout}s")
            async with aiohttp.ClientSession() as session:
                async with session.head(url, timeout=timeout, ssl=False) as resp:
                    logger.debug(f"HTTP HEAD 响应状态码: {resp.status}")
                    return 200 <= resp.status < 400
        except Exception as e:
            logger.debug(f"HTTP HEAD 失败: {e}，尝试 GET 请求")
            # HEAD 失败时尝试 GET
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=timeout, ssl=False) as resp:
                        logger.debug(f"HTTP GET 响应状态码: {resp.status}")
                        return 200 <= resp.status < 400
            except Exception as e2:
                logger.debug(f"HTTP GET 也失败: {e2}")
                return False
    
    async def _check_ping(self, host: str, timeout: int) -> bool:
        """Ping 检测"""
        try:
            # 移除协议头
            original_host = host
            if host.startswith(("http://", "https://")):
                host = host.split("://", 1)[1].split("/")[0]
            if ":" in host:
                host = host.split(":")[0]
            
            logger.debug(f"Ping 检测: {host} (原始: {original_host}), 超时: {timeout}s")
            
            proc = await asyncio.create_subprocess_exec(
                "ping", "-n", "1", "-w", str(timeout * 1000), host,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await asyncio.wait_for(proc.wait(), timeout=timeout + 2)
            success = proc.returncode == 0
            logger.debug(f"Ping 结果: {host} - {'成功' if success else '失败'} (returncode: {proc.returncode})")
            return success
        except Exception as e:
            logger.debug(f"Ping 异常: {e}")
            return False
    
    async def _check_tcp(self, url: str, timeout: int) -> bool:
        """TCP 连接检测"""
        try:
            # 解析主机和端口
            if url.startswith(("http://", "https://")):
                host = url.split("://", 1)[1].split("/")[0]
                port = 443 if url.startswith("https://") else 80
            else:
                if ":" in url:
                    host, port_str = url.rsplit(":", 1)
                    port = int(port_str)
                else:
                    host = url
                    port = 80
            
            logger.debug(f"TCP 连接检测: {host}:{port}, 超时: {timeout}s")
            
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port),
                timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            logger.debug(f"TCP 连接成功: {host}:{port}")
            return True
        except Exception as e:
            logger.debug(f"TCP 连接失败: {host}:{port} - {e}")
            return False
    
    async def _update_target_state(self, target: Dict, result: Dict):
        """更新目标状态并决定是否发送通知"""
        target_name = target.get("name", "unknown")
        state = self.target_states[target_name]
        notification_settings = self._get_notification_settings()
        
        prev_status = state["last_status"]
        prev_failures = state.get("consecutive_failures", 0)
        
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
        
        logger.debug(f"[{target_name}] 状态更新: 之前={prev_status}, 现在={new_status}, "
                    f"连续失败={state['consecutive_failures']}")
        
        # 保存状态
        self._save_state()
        
        # 判断是否发送通知
        should_notify = False
        message = ""
        notify_on_success = notification_settings.get("notify_on_success", False)
        notify_on_failure = notification_settings.get("notify_on_failure", True)
        consecutive_failures_threshold = notification_settings.get("consecutive_failures", 2)
        
        if new_status and prev_status is False:
            # 恢复通知
            if notify_on_success:
                should_notify = True
                message = f"✅ [{target_name}] 网络已恢复正常！\n响应时间: {result['response_time']}ms"
                logger.info(f"[{target_name}] 网络恢复，准备发送通知")
        elif not new_status:
            # 失败通知
            if state["consecutive_failures"] >= consecutive_failures_threshold:
                if prev_status is not False or notify_on_failure:
                    should_notify = True
                    error_msg = result.get("error", "未知错误")
                    message = f"❌ [{target_name}] 网络连接异常！\n错误: {error_msg}\n已连续失败 {state['consecutive_failures']} 次"
                    logger.info(f"[{target_name}] 网络异常且达到报警阈值，准备发送通知")
        
        if should_notify:
            if self._is_silent_hours():
                logger.info(f"[{target_name}] 当前处于免打扰时段，跳过通知发送")
            else:
                await self._send_notification(message)
    
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
    def net(self):
        """网络监测指令组"""
        pass
    
    @net.command("addme")
    async def net_addme(self, event: AstrMessageEvent, description: str = ""):
        """将当前聊天添加到通知目标列表
描述：可选的描述信息，用于标识此通知目标"""
        umo = event.unified_msg_origin
        group_id = event.get_group_id()
        sender_id = event.get_sender_id()
        
        logger.info(f"执行 /net addme, UMO: {umo}, 发送者: {sender_id}, 群组: {group_id}")
        
        # 自动生成描述
        if not description:
            if group_id:
                description = f"群聊 {group_id}"
            else:
                description = f"用户 {sender_id}"
        
        added = self._add_umo_to_notify_targets(umo, description)
        
        if added:
            logger.info(f"成功添加通知目标: {umo}")
            yield event.plain_result(f"✅ 已添加此聊天到通知目标列表\nUMO: {umo}\n描述: {description}")
        else:
            logger.info(f"通知目标已存在: {umo}")
            yield event.plain_result("ℹ️ 此聊天已在通知目标列表中")
    
    @net.command("check")
    async def net_check(self, event: AstrMessageEvent, target_name: str = ""):
        """手动执行一次网络检测
目标：要检测的目标名称，留空则检测所有目标"""
        logger.info(f"执行 /net check, 发送者: {event.get_sender_id()}, 目标: {target_name or '全部'}")
        
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
                yield event.plain_result(f"⚠️ 未找到目标 '{target_name}'\n可用目标: {available}")
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
                last_check_str = datetime.fromtimestamp(last_check).strftime("%m-%d %H:%M")
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
    async def net_history(self, event: AstrMessageEvent, target_name: str = "", count: int = 5):
        """查看指定目标的检测历史
目标：目标名称
数量：显示最近几条记录（默认5条）"""
        logger.info(f"执行 /net history, 发送者: {event.get_sender_id()}, 目标: {target_name or '未指定'}, 数量: {count}")
        
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
            yield event.plain_result(f"请指定目标名称:\n{target_list}\n\n使用: /net history <目标名> [数量]")
            return
        
        history = self.detection_history.get(target_name, [])
        
        if not history:
            yield event.plain_result(f"⚠️ 目标 '{target_name}' 暂无历史记录")
            return
        
        # 取最近几条
        recent = history[-count:]
        
        messages = [f"📈 {target_name} - 最近{len(recent)}次检测记录\n" + "=" * 30]
        
        for record in reversed(recent):  # 倒序显示，最新的在前面
            timestamp = datetime.fromtimestamp(record["timestamp"]).strftime("%m-%d %H:%M:%S")
            status = "✅ 正常" if record["success"] else "❌ 异常"
            resp_time = f"{record['response_time']}ms" if record.get("response_time") else "-"
            error = f" | {record['error'][:30]}..." if record.get("error") else ""
            
            messages.append(f"{timestamp} | {status} | {resp_time}{error}")
        
        yield event.plain_result("\n".join(messages))
