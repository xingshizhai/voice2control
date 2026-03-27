# AI Voice Controller

按住说话，松开后自动识别并投递到当前焦点输入框（如 Cursor 聊天窗口）。

## 3 分钟上手

1) 安装依赖

```powershell
python -m pip install -r requirements.txt
```

2) 准备配置

```powershell
copy config.example.yaml config.yaml
```

3) 若使用 DashScope，设置 API Key（推荐环境变量）

```powershell
$env:DASHSCOPE_API_KEY="sk-xxxx"
```

4) 默认启动图形界面（PySide6）

```powershell
python -m voice_controller
```

5) 命令行模式（需要时）

```powershell
python -m voice_controller --cli -c config.yaml --validate-only
python -m voice_controller --cli -c config.yaml -v
```

## 功能

- 支持多 ASR provider：`local`（局域网 WebSocket）/ `dashscope`（阿里云百炼）
- 支持多 provider 配置并通过 `active_provider` 切换
- 支持自动输入与可选自动回车发送
- 支持窗口白名单（可选）

## 环境要求

- Python 3.8+
- Windows 10+（当前主验证平台）

## 配置

复制配置模板：

```powershell
copy config.example.yaml config.yaml
```

关键项（推荐只改这些）：

- `asr.active_provider`：当前启用的 provider 键
- `asr.providers.*`：各 provider 的具体配置
- `delivery.auto_send_enter`：是否自动回车发送（Cursor 常用）
- `gui.minimize_to_tray_on_close`：点击窗口关闭按钮时是否最小化到托盘
- `delivery.mode`：
  - `paste_and_send` 自动粘贴并发送
  - `paste_only` 仅粘贴不发送

### DashScope Key（推荐方式）

建议用环境变量，不要明文写入 `config.yaml`：

```powershell
$env:DASHSCOPE_API_KEY="sk-xxxx"
```

## 文档索引

- `config.example.yaml`：完整示例配置
- `docs/asr-service-api.md`：本地 ASR 服务协议说明
- `docs/technical-design.md`：实现设计说明

## 默认热键

- `F8`：按住录音，松开识别
- `Ctrl+Shift+R`：重录（预留）
- `Esc`：取消本轮录音
- `Ctrl+Q`：退出

> 可通过 `hotkey.recognition_enabled_on_start` 配置程序启动后默认是否允许识别。  
> 若默认关闭，可在图形界面点击“启用识别”后再使用 F8 录音。

## 常见问题

- **只粘贴不发送？**  
  检查 `delivery.mode` 是否为 `paste_only`。

- **设置了自动发送但不回车？**  
  确认：
  - `delivery.mode: paste_and_send`
  - `delivery.profile: cursor_win`
  - `delivery.auto_send_enter: true`

- **ASR 报连接拒绝（10061）？**  
  检查 provider 地址、端口与服务状态（网络/防火墙）。

- **DashScope 提示未读取到 API Key？**  
  `dashscope_api_key_env` 里应填“环境变量名”（如 `DASHSCOPE_API_KEY`），不是 key 值本身。

