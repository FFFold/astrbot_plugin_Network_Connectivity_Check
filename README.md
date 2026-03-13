# 网络连接监测插件

AstrBot 网络连接监测插件 - 定时监测网络连接状态，支持 HTTP/Ping/TCP 多种检测方式

## 功能特性

- **多种检测方式**：支持 HTTP 请求、ICMP Ping、TCP 连接三种检测方式
- **定时监测**：可配置检测间隔，自动后台运行
- **智能通知**：
  - 只在状态变化时通知（避免刷屏）
  - 连续失败 N 次才报警（防误报）
  - 支持夜间免打扰时段
- **灵活配置**：支持多个目标同时监测，每个目标可独立配置通知策略
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

### 配置说明

在 AstrBot WebUI 的插件配置页面中进行配置：

#### 监测目标

配置需要监测的网络目标：

- **名称**：用于标识该目标（如 Google、百度）
- **URL/地址**：要检测的网址或 IP
- **检测方式**：
  - `http`：发送 HTTP 请求检测网站可用性
  - `ping`：使用 ICMP 协议检测网络连通性
  - `tcp`：检测 TCP 端口连接
- **检测间隔**：两次检测之间的间隔（秒），建议不少于 60 秒
- **超时时间**：单次检测的最大等待时间
- **失败重试次数**：检测失败时的重试次数
- **连通时通知**：是否在网络恢复时发送通知
- **失败时通知**：是否在网络异常时发送通知
- **连续失败几次才报警**：避免偶发性波动导致误报

#### 通知目标

配置检测结果通知给哪些用户或群组。UMO 格式示例：
- 群聊：`aiocqhttp:GroupMessage:123456`
- 私聊：`aiocqhttp:FriendMessage:123456`

**提示**：在群里发送 `/net check` 会自动将该群添加到通知列表

#### 全局设置

- **免打扰时段**：设置夜间不发送通知的时间段
- **历史记录保留数**：每个目标保留的最近检测记录数量

## 示例配置

```json
{
  "targets": [
    {
      "name": "Google",
      "url": "https://www.google.com",
      "method": "http",
      "interval": 300,
      "timeout": 10,
      "retry": 3,
      "notify_on_success": false,
      "notify_on_failure": true,
      "consecutive_failures": 2
    },
    {
      "name": "百度",
      "url": "https://www.baidu.com",
      "method": "http",
      "interval": 300,
      "timeout": 10
    }
  ]
}
```

## 使用场景

1. **服务器监控**：监测服务器是否能访问外网
2. **网站可用性**：监测网站是否正常提供服务
3. **网络质量**：通过响应时间和历史记录分析网络状况

## 支持与文档

- [AstrBot 主仓库](https://github.com/AstrBotDevs/AstrBot)
- [AstrBot 插件开发文档](https://docs.astrbot.app/dev/star/plugin-new.html)

## 许可证

MIT License