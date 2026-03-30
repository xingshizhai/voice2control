# AI Voice Controller

AI Voice Controller 是一个面向 Windows 的按住说话（Push-to-Talk）语音输入工具。  
按住热键录音，松开后自动识别，并将文本自动粘贴或发送到当前焦点窗口（例如 Cursor 聊天框）。

[English](README.md) | 简体中文

## 项目概览

这个项目帮助开发者和高频输入用户用语音替代重复打字，同时保留可控性：

- **什么时候录音**（全局热键控制）
- **使用哪个识别后端**（`local` WebSocket 或 `dashscope`）
- **如何投递文本**（`paste_and_send`、`paste_only`、`review`）
- **如何纠正常见术语**（本地 SQLite 词库）

## 主要功能

- 全局按住说话、取消、退出
- GUI 与 CLI 双模式
- 多 ASR Provider 配置切换
- 文本投递模式：`paste_and_send`、`paste_only`、`review`
- 窗口白名单与剪贴板恢复
- 识别后本地词库纠正（Lexicon Correction）
- 本地词库管理（GUI + CLI）
- 词库 CSV 导入/导出与模板生成

## 典型使用场景

- 在 Cursor、IDE 聊天框、笔记工具中快速输入
- 对产品名、API 名、专业术语进行稳定纠正
- 本地 ASR 优先（隐私）+ 云 ASR 备选（可用性）

## 3 分钟上手

### 1) 安装依赖

```powershell
python -m pip install -r requirements.txt
```

### 2) 准备配置

```powershell
copy config.example.yaml config.yaml
```

### 3) （可选）DashScope API Key 使用环境变量

```powershell
$env:DASHSCOPE_API_KEY="sk-xxxx"
```

### 4) 启动 GUI（默认）

```powershell
python -m vc
```

### 5) 命令行校验配置

```powershell
python -m vc --cli -c config.yaml --validate-only
```

## 工作流程

1. 按下热键 -> 开始录音  
2. 释放热键 -> 调用 ASR 识别  
3. 拿到识别文本 -> 词库纠正（可选）  
4. 输出文本 -> 自动粘贴/发送到目标窗口  
5. 更新状态和历史记录

## GUI 能力

- 启动/停止监听
- 运行时启用/禁用识别
- 配置 Provider、投递模式、白名单
- 词库术语增删改查
- 词库搜索和排序（按权重/术语名）
- 词库 CSV 导入/导出与模板保存

## CLI 用法

### 运行模式

```powershell
python -m vc
python -m vc --cli -c config.yaml -v
python -m vc --cli -c config.yaml --validate-only
```

## 词库管理（CLI）

```powershell
# 新增/更新术语
python -m vc --lexicon-add "LangChain" --lexicon-aliases "lang chain,郎圈"

# 查看当前领域词库
python -m vc --lexicon-list

# 从 CSV 导入（列：term,aliases,domain,weight）
python -m vc --lexicon-import-csv ".\terms.csv"

# 导出当前领域到 CSV
python -m vc --lexicon-export-csv ".\lexicon_export.csv"
```

## 词库 CSV 格式

表头：

```text
term,aliases,domain,weight
```

示例行：

```text
LangChain,"lang chain,郎圈",default,100
```

导入统计说明：

- `total`：读取行数
- `imported`：成功导入行数
- `skipped`：被跳过行数（如空 term、非法 weight）
- `failed`：异常失败行数

## 配置重点

基于 `config.example.yaml`，通常先关注：

- `asr.active_provider`
- `asr.providers.*`
- `delivery.mode`
- `delivery.auto_send_enter`
- `gui.minimize_to_tray_on_close`
- `gui.auto_start_listening`
- `lexicon.enabled`
- `lexicon.db_path`
- `lexicon.domain`

## 项目结构

```text
vc/
  app_module/        # 应用入口编排
  __main__.py        # 包入口
  ui_module/         # GUI 领域模块
  core_module/       # 核心运行流程（pipeline/history）
  input_module/      # 输入模块（hotkey/audio）
  output_module/     # 输出模块（delivery）
  platform_module/   # 平台相关能力
  asr_module/        # ASR 领域模块
  lexicon_module/    # 词库领域模块
  config.py          # 配置解析与校验
tests/
  test_*.py          # 单元测试
```

## 默认热键

- `F8`：按住录音，松开识别
- `Esc`：取消当前录音
- `Ctrl+Shift+R`：重录（预留）
- `Ctrl+Q`：退出程序

## 环境要求

- Python 3.8+
- Windows 10+（当前主验证平台）
- Linux（X11 和 Wayland）— 见下方平台说明

### Linux 平台说明

**安装系统音频库（所有 Linux）：**

```bash
sudo apt install libportaudio2
```

**X11 会话** — 无需额外步骤，pynput 负责全局热键和键盘注入。可选安装 `xdotool` 用于读取前台窗口标题（`delivery.window_whitelist` 功能依赖此工具）：

```bash
sudo apt install xdotool
```

**Wayland 会话** — 按键注入通过 `/dev/uinput`（内核层虚拟键盘）实现，支持 GNOME、KDE、sway 等所有合成器。需要以下两步：

1. 创建 udev 规则，授予 `input` 组对 `/dev/uinput` 的写权限（一次性，立即生效）：

```bash
echo 'KERNEL=="uinput", GROUP="input", MODE="0660"' | sudo tee /etc/udev/rules.d/99-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger
```

2. 将当前用户加入 `input` 组（热键检测和按键注入都需要），然后重新登录：

```bash
sudo usermod -aG input $USER
# 注销并重新登录后生效
```

> **说明：** Wayland 会话下，热键捕获通过 evdev 读取 `/dev/input` 实现，按键注入通过 `evdev.UInput` 在内核层创建虚拟键盘实现。这种方式绕过了 Wayland 合成器的协议限制，不依赖合成器是否支持特定协议（GNOME 不支持 wtype 所需协议，但此方案完全兼容）。未配置 udev 规则或未加入 `input` 组时，热键和投递均无法使用。

**Wayland 剪贴板** — 依赖 `wl-clipboard`，大多数 Wayland 桌面已预装。如未安装：

```bash
sudo apt install wl-clipboard
```

## 开发与测试

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## 编译与打包（Windows）

先安装打包依赖：

```powershell
python -m pip install -r requirements-dev.txt
```

PowerShell 脚本：

```powershell
# 打包 GUI + CLI（onedir）
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1

# 仅打包 GUI
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Target gui

# 仅打包 CLI（单文件）
powershell -ExecutionPolicy Bypass -File .\scripts\build.ps1 -Target cli -OneFile
```

Batch 脚本：

```powershell
scripts\build.bat all
scripts\build.bat gui --onefile
```

输出目录：

- `dist/voice2control`（GUI 包）
- `dist/voice2control-cli`（CLI 包）

## 已知限制

- 当前主要在 Windows 平台验证，Linux 支持已可用但测试较少
- `Ctrl+Shift+R` 为预留热键，后续会扩展更多重录能力
- GUI 自动投递依赖目标应用的焦点和键盘处理行为
- Wayland 下按键注入通过 `/dev/uinput`（内核层）实现，兼容所有合成器及原生 Wayland、XWayland 应用

## 相关关键词（SEO）

语音转文字, 按住说话, 全局热键录音, ASR, 桌面语音输入, Cursor 语音输入, 本地词库, SQLite 术语词典, Windows 语音输入

## 文档索引

- `config.example.yaml`
- `docs/asr-service-api.md`
- `docs/technical-design.md`
- `docs/requirement.md`

## 常见问题

- **ASR 连接被拒绝（10061）？**
  检查 ASR 服务是否启动、地址端口是否正确、防火墙是否拦截。

- **DashScope Key 未读取到？**
  确保 `dashscope_api_key_env` 填的是环境变量名（如 `DASHSCOPE_API_KEY`）。

- **只粘贴不发送？**
  检查 `delivery.mode` 与 `delivery.auto_send_enter`。

- **Linux：F8 热键无响应？**
  Wayland 下需执行 `sudo usermod -aG input $USER` 并重新登录，使 evdev 能读取键盘设备。

- **Linux：识别成功但文字未输入到窗口？**
  Wayland 下需确保已创建 `/dev/uinput` 的 udev 规则且用户在 `input` 组中（见上方 Linux 平台说明）。

- **Linux：剪贴板报错？**
  安装 `wl-clipboard`（`sudo apt install wl-clipboard`）以支持 Wayland 剪贴板。

## 贡献

欢迎提交 Issue 和 Pull Request。  
提交缺陷建议附带复现步骤和测试结果。

## 许可证

MIT License，详见 `LICENSE`。
