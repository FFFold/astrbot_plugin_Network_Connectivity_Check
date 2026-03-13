# 网络连接监测插件

AstrBot 网络连接监测插件 - 定时监测网络连接状态，支持 HTTP/Ping/TCP 多种检测方式

## 功能特性

- **多种检测方式**：支持 HTTP 请求、ICMP Ping、TCP 连接三种检测方式
- **定时监测**：可配置检测间隔，自动后台运行
- **智能通知**：
  - 只在状态变化时通知（避免刷屏）
  - 连续失败 N 次才报警（防误报）
  - 支持夜间免打扰时段
- **灵活配置**：支持多个目标同时监测，可在 WebUI 中以可视化方式配置
- **历史记录**：保存检测历史，便于查看网络状况趋势

## 安装方法

1. 在 AstrBot WebUI 中打开「插件」页面
2. 点击「安装插件」
3. 输入仓库地址：`https://github.com/FFFold/astrbot_plugin_Network_Connectivity_Check`
4. 安装完成后重启 AstrBot

## 使用方法

### 指令列表

| 指令 | 用法 | 说明 |
|------|------|------|
| `/net check` | `/net check [目标名]` | 手动执行一次检测，不指定目标则检测所有目标 |
| `/net status` | `/net status` | 查看所有监测目标的当前状态 |
| `/net history` | `/net history <目标名> [数量]` | 查看指定目标的检测历史 |
| `/net addme` | `/net addme [描述]` | 将当前聊天添加到通知目标列表 |

### 配置说明

在 AstrBot WebUI 的插件配置页面中进行配置：

#### 监测目标 (targets)

配置需要监测的网络目标列表。使用 `template_list` 类型，在 WebUI 中以卡片形式展示，方便添加和管理：

- **名称**：用于标识该目标（如 Google、百度）
- **URL/地址**：要检测的网址或 IP
- **检测方式**：`http` / `ping` / `tcp`
- **自定义设置**：是否为该目标单独设置检测参数（覆盖全局设置）
  - 启用后可单独设置：检测间隔、超时时间、失败重试次数

#### 检测全局设置 (detection_settings)

所有监测目标的默认检测参数：

- **检测间隔**：两次检测之间的间隔（秒），默认 300 秒
- **超时时间**：单次检测的最大等待时间（秒），默认 10 秒
- **失败重试次数**：检测失败时的重试次数，默认 3 次

#### 通知全局设置 (notification_settings)

检测结果的通知策略：

- **连通时是否通知**：网络恢复时是否发送通知（默认关闭，建议关闭避免刷屏）
- **失败时是否通知**：网络异常时是否发送通知（默认开启）
- **连续失败几次才报警**：避免偶发性网络波动导致误报（默认 2 次）
- **免打扰开始/结束时间**：设置夜间不发送通知的时间段，设为 -1 关闭

#### 通知目标 (notify_targets)

配置检测结果通知给哪些用户或群组。使用 `template_list` 类型，在 WebUI 中以卡片形式展示：

- **UMO**：统一消息来源标识
- **描述**：用于标识此通知目标

**快速添加方法**：在群里或私聊中发送 `/net addme`，即可自动将当前聊天添加到通知列表。

UMO 格式示例：
- 群聊：`aiocqhttp:GroupMessage:123456`
- 私聊：`aiocqhttp:FriendMessage:123456`

#### 高级设置 (advanced_settings)

- **历史记录保留数**：每个目标保留的最近检测记录数量（默认 100）

## 示例配置

### 监测目标

添加两个监测目标：

```json
[
  {
    "name": "Google",
    "url": "https://www.google.com",
    "method": "http",
    "custom_settings": false
  },
  {
    "name": "百度",
    "url": "https://www.baidu.com",
    "method": "http",
    "custom_settings": false
  }
]
```

### 检测全局设置

```json
{
  "interval": 300,
  "timeout": 10,
  "retry": 3
}
```

### 通知目标

```json
[
  {
    "umo": "aiocqhttp:GroupMessage:123456789",
    "description": "我的QQ群"
  },
  {
    "umo": "aiocqhttp:FriendMessage:987654321",
    "description": "管理员私聊"
  }
]
```

## 使用场景

1. **服务器监控**：监测服务器是否能访问外网
2. **网站可用性**：监测网站是否正常提供服务
3. **网络质量**：通过响应时间和历史记录分析网络状况

## 工作原理

1. 插件启动后，会为每个监测目标创建一个后台任务
2. 按照设定的间隔自动执行检测
3. 检测成功/失败时更新目标状态
4. 根据通知策略决定是否发送通知（避免重复通知、支持免打扰）
5. 所有检测记录保存到历史，便于后续分析

## 支持与文档

- [AstrBot 主仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)

## 许可证

MIT License
