# AGENTS.md - 网络连接监测插件

## 项目概述

这是一个用于 **AstrBot** 的插件，提供定时网络连接监测功能，支持多种检测方式和灵活的通知策略。

### 基本信息
- **名称**: `astrbot_plugin_network_connectivity_check`
- **显示名称**: 网络连接监测插件
- **版本**: v1.0.0
- **作者**: Fold
- **仓库**: https://github.com/FFFold/astrbot_plugin_Network_Connectivity_Check
- **许可证**: MIT

### 核心功能
1. **多种检测方式**: HTTP 请求、ICMP Ping、TCP 连接
2. **定时监测**: 后台自动运行，可配置检测间隔
3. **智能通知**: 
   - 状态变化时通知（失败→成功 或 成功→失败）
   - 连续失败阈值（防误报）
   - 夜间免打扰时段
4. **多目标管理**: 同时监测多个目标，独立配置
5. **历史记录**: 保存检测历史，便于分析网络状况

---

## 文件结构

```
D:\Projects\AstrBot\data\plugins\astrbot_plugin_Network_Connectivity_Check\
├── main.py                 # 主插件代码 (754行)
├── _conf_schema.json       # 配置模式定义 (WebUI可视化配置)
├── metadata.yaml           # 插件元数据
├── requirements.txt        # 依赖: aiohttp>=3.8.0
├── README.md               # 详细使用文档
├── LICENSE                 # MIT许可证
├── AGENTS.md              # 本文件
├── .gitignore             # Git忽略配置
└── __pycache__/           # Python缓存
```

---

## 技术栈

- **框架**: AstrBot 插件系统 (基于 Python asyncio)
- **核心依赖**: 
  - `aiohttp>=3.8.0` - 异步 HTTP 客户端
  - AstrBot API (`astrbot.api.*`)
- **检测方式**:
  - HTTP: `aiohttp` 发送 HEAD/GET 请求
  - Ping: Windows `ping` 命令 (`asyncio.create_subprocess_exec`)
  - TCP: `asyncio.open_connection` 直接连接端口

---

## 核心类和方法

### NetworkConnectivityPlugin (Star)

主插件类，继承自 AstrBot 的 `Star` 基类。

#### 关键属性
- `config`: 插件配置 (从 `_conf_schema.json` 解析)
- `target_states`: 目标状态字典 `{target_name: {...}}`
- `detection_history`: 检测历史 `{target_name: [results]}`
- `monitor_tasks`: 后台监测任务 `{target_name: asyncio.Task}`

#### 主要方法

**初始化与生命周期**
- `__init__(context, config)` - 初始化插件，加载状态和历史
- `initialize()` - 启动所有监测任务
- `terminate()` - 停止所有任务，保存数据

**配置获取**
- `_get_target_config()` - 获取处理后的目标配置（合并全局和自定义设置）
- `_get_notify_targets()` - 获取通知目标列表
- `_get_notification_settings()` - 获取通知全局设置

**检测方法**
- `_check_target(target)` - 检测单个目标，返回结果字典
- `_check_http(url, timeout)` → `(bool, str)` - HTTP 检测，返回(成功, 错误信息)
- `_check_ping(host, timeout)` → `(bool, str)` - ICMP Ping 检测
- `_check_tcp(url, timeout)` → `(bool, str)` - TCP 连接检测

**通知逻辑**
- `_update_target_state(target, result)` - 更新状态并触发通知
- `_send_notification(message)` - 发送通知到所有配置目标
- `_is_silent_hours()` - 检查是否在免打扰时段

**后台任务**
- `_monitor_target(target)` - 单个目标的监测循环

---

## 指令列表

所有指令都在 `/net` 指令组下：

| 指令 | 用法 | 功能 |
|------|------|------|
| `check` | `/net check [目标名]` | 手动执行一次检测 |
| `status` | `/net status` | 查看所有目标当前状态 |
| `history` | `/net history <目标> [数量]` | 查看检测历史 |
| `addme` | `/net addme [描述]` | 添加当前聊天到通知列表 |

---

## 配置说明

配置通过 `_conf_schema.json` 定义，在 AstrBot WebUI 中可视化配置。

### 配置项结构

```json
{
  "targets": [...],              // template_list - 监测目标
  "detection_settings": {...},   // object - 全局检测参数
  "notification_settings": {...},// object - 通知策略
  "notify_targets": [...],       // template_list - 通知目标
  "advanced_settings": {...}     // object - 高级设置
}
```

### 关键配置详解

**notification_settings 通知策略**

| 配置项 | 类型 | 默认 | 说明 |
|--------|------|------|------|
| `notify_on_status_change` | bool | true | 状态变化时通知（推荐开启） |
| `notify_on_success` | bool | false | 每次成功都通知（会刷屏） |
| `notify_on_failure` | bool | false | 每次失败都通知（会刷屏） |
| `consecutive_failures` | int | 2 | 状态变化通知的连续失败阈值 |

**通知逻辑详解**:
- **成功→失败**: 需连续失败达到阈值才通知（防误报）
- **失败→成功**: 立即通知（恢复通知）
- **每次成功/失败通知**: 独立开关，不受阈值限制

---

## 数据存储

数据保存在 AstrBot 的 `data/` 目录下：

```
data/
├── network_connectivity_check/
│   ├── state.json      # 目标状态 (last_status, consecutive_failures 等)
│   └── history.json    # 检测历史记录
└── config/
    └── astrbot_plugin_network_connectivity_check_config.json  # 配置文件实体
```

### 状态文件结构
```json
{
  "目标名称": {
    "last_status": true/false/null,
    "consecutive_failures": 0,
    "last_check_time": 1234567890,
    "last_response_time": 123.45
  }
}
```

---

## 开发规范

### 代码风格
- 使用 Python 类型注解 (`typing.Dict`, `typing.List` 等)
- 异步编程：`async/await` 模式
- 日志：使用 `astrbot.api.logger` (支持 debug/info/warning/error 级别)

### 错误处理
- 检测方法返回 `(bool, str)` 元组，包含成功状态和详细错误信息
- 使用 `try/except` 捕获异常，记录详细日志
- 网络请求设置合理的超时时间

### 通知消息格式
```python
# 成功恢复
"✅ [目标名] 网络已恢复正常！\n响应时间: XXms\n（已从异常状态恢复）"

# 异常（达到阈值）
"❌ [目标名] 网络连接异常！\n错误: 具体错误信息\n已连续失败 X 次"
```

---

## 使用场景

1. **服务器监控**: 监测服务器外网连通性
2. **网站可用性**: 监测网站服务状态
3. **网络质量分析**: 通过历史记录分析响应时间趋势

---

## 依赖安装

```bash
pip install aiohttp>=3.8.0
```

或在 AstrBot 中自动安装（通过 `requirements.txt`）

---

## 开发建议

### 添加新的检测方式
1. 在 `_check_target` 中添加新方法分支
2. 实现对应的 `_check_<method>` 方法，返回 `(bool, str)`
3. 在 `_conf_schema.json` 的 `method` options 中添加新选项

### 修改通知逻辑
- 主要逻辑在 `_update_target_state` 方法中
- 注意三个通知开关的独立性和优先级

### 调试技巧
- 开启 DEBUG 级别日志查看详细检测过程
- 使用 `notify_on_success` 和 `notify_on_failure` 查看每次检测结果
- 查看 `data/network_connectivity_check/` 下的状态和历史文件

---

## 相关资源

- [AstrBot 主仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)
- [aiohttp 文档](https://docs.aiohttp.org/)
