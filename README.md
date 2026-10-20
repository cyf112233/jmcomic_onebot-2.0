# JMComic OneBot 2.0

把禁漫天堂（JMComic）的漫画（默认**最多 10 章**）下载、合成 PDF、加密压缩后发送到 QQ 群的 OneBot v11 机器人。

> 当前版本：**2.1.0**
>
> 本次翻新的重点：**章节数上限（默认 10 章）**、依赖隔离（不再乱装系统 Python）、Linux 启动脚本修复、PDF 生成健壮性、完整测试与文档。

## ✨ 功能特性

- **章节数限制**：默认最多允许 10 章；超过上限直接拒绝，不浪费流量；下载后还有二次兜底检查。
- **整本发送**：1~10 章的漫画会按章节顺序合并为 PDF，再打包上传。
- **进度提示**：先发送“正在检查”，确认可下载后发送“正在下载”并自动撤回“正在检查”。
- **安全密码**：解压密码只通过群消息元数据发送，不写入压缩包文件名。
- **管理员控制**：`/开启jm`、`/关闭jm`、`/更新jm`，按 QQ 号授权。
- **分群开关与冷却**：每个群独立启用状态、独立冷却时间，状态持久化。
- **依赖隔离**：默认在项目内创建 `.venv`，不会污染系统 Python，也不会触发 Debian/Ubuntu 的 `externally-managed-environment`（PEP 668）错误。
- **兼容其他包管理器**：通过 `JM_BOT_PYTHON` 指向 uv / conda / poetry 环境时，只检查依赖，不擅自安装。
- **跨平台**：Linux/macOS 使用 LF 的 `start_jm_bot`；Windows 使用 CRLF 的 `start_jm_bot.bat`。
- **健壮 PDF**：支持 JPEG/PNG/WebP/BMP/TIFF，处理透明通道、EXIF 方向、损坏图片和自然排序。
- **可测试**：命令解析、配置校验、PDF、压缩包、章节超限拒绝流程均有单元测试。

## 🚫 章节数限制

JMComic 的“本子（album）”可能包含多个章节（photo）。现在默认**允许 1~10 章**，超过 10 章拒绝处理。

1. 收到 `/jm <ID>` 后，先用 jmcomic API 读取漫画元数据；
2. 如果 `episode_list` 中**不同章节 ID 数量 > `bot.max_chapters`**（默认 10），立即回复拒绝消息并结束，**不会创建下载任务**；
3. 下载完成后再次检查实际章节目录数量，若仍大于 `max_chapters`，则删除临时文件并拒绝发送；
4. 如果元数据读取失败，在默认配置下也**不会贸然下载**，避免误下超限本。

拒绝消息示例：

```text
❌ 已拒绝处理：章节数超过限制
📚 标题：示例漫画
🆔 漫画 ID：422866
📖 章节数：12
📌 上限：10 章

本机器人最多支持 10 章，超过上限的漫画不会下载。
请发送章节数不超过 10 章的漫画 ID。
章节示例：第1话 / 第2话 / 第3话 …
```

相关配置：

```yaml
bot:
  reject_multi_chapter: true   # 默认开启，强烈建议保持
  max_chapters: 10             # 允许 1~10 章；超过才拒绝
```

如果管理员需要完全取消限制（例如允许 20 章），可以将 `reject_multi_chapter` 设为 `false`。此时机器人会按元数据顺序合并全部章节，但可能产生非常大的 PDF，且更容易上传失败。

## 📜 使用命令

| 命令 | 功能 | 权限 |
|------|------|------|
| `/jm <ID>` | 下载并发送漫画（默认最多 10 章） | 所有群成员 |
| `/帮助` | 查看帮助 | 所有群成员 |
| `/开启jm` | 启用本群 | 机器人管理员 |
| `/关闭jm` | 禁用本群 | 机器人管理员 |
| `/更新jm` | 更新 jmcomic 并重启 | 机器人管理员 |

**严格语法**：命令必须完全匹配，不能多字，也不能少字。

- `/jm` 与 ID 之间必须**恰好一个半角空格**，例如 `/jm 123456`；
- 不接受 `?`、`？`、`/help`、`/enable`、`/disable`、`/update`；
- 不接受 `jm 123456`、`.jm 123456`、`!jm 123456`、`#jm 123456`；
- 不接受 `/jm123456`、`/jm  123456`、`/jm #123456`、`/jm id:123456`；
- 不接受 `/jm 123456 多余文字`；
- 全角字符会先做 NFKC 归一化（如 `／ｊｍ　１２３４５６`），消息开头的 `@机器人` CQ 码会被忽略；
- 消息首尾空白会被忽略，但命令内部不能有多余空格。

## 🚀 快速开始

### 1. 环境要求

- Python **3.9+**
- 一个支持 OneBot v11 正向 WebSocket 的服务端，例如 [NapCat](https://github.com/NapNeko/NapCatQQ)、go-cqhttp 等
- 机器人 QQ 号需要具备上传群文件的权限

Linux（Debian/Ubuntu）建议先安装 Python 和 venv 支持：

```bash
sudo apt update
sudo apt install python3 python3-venv
```

> 不需要 `sudo pip install` 任何东西。项目会在自己的 `.venv` 里安装依赖。

Windows 请从 [python.org](https://www.python.org/downloads/) 安装 Python 3.9+，安装时勾选 **Add Python to PATH**。

### 2. 修改配置

编辑 [`jm_bot/config.yml`](jm_bot/config.yml)：

```yaml
onebot:
  ws_url: "ws://127.0.0.1:6700/"
  access_token: ""

bot:
  admins: [12345678]        # 你的管理员 QQ
  reject_multi_chapter: true
```

- `ws_url` 指向 OneBot 端的正向 WebSocket 服务；
- 如果 OneBot 端设置了 token，这里填入相同的 `access_token`；
- `admins` 必须填写，否则没人能执行 `/开启jm`。

### 3. 启动

Linux / macOS：

```bash
chmod +x start_jm_bot
./start_jm_bot
```

Windows：

```bat
start_jm_bot.bat
```

首次启动会自动：

1. 在项目根目录创建 `.venv`；
2. 安装 [`requirements.txt`](requirements.txt)；
3. 启动 `jm_bot.main`。

之后启动会跳过安装。启动前可以随时自检：

```bash
./start_jm_bot --check
# 或
.venv/bin/python -m jm_bot.main --check
```

### 4. 手动安装（可选）

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m jm_bot.main --check
.venv/bin/python -m jm_bot.main
```

Windows 对应 `.venv\Scripts\python.exe`。

国内网络较慢时可以指定 PyPI 镜像：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple ./scripts/setup.sh
# 或
./scripts/setup.sh --index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

如果 PowerShell 阻止执行脚本，可以使用：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

#### 使用 uv / conda / poetry

如果你用其他工具管理环境，**不要让本项目自动安装依赖**，设置 `JM_BOT_PYTHON` 即可：

```bash
# 以 uv 为例
uv venv .venv
uv pip install -r requirements.txt
JM_BOT_PYTHON=.venv/bin/python ./start_jm_bot
```

此时 `scripts/bootstrap.py` 只做依赖检查；缺少依赖会提示你用对应工具安装，不会调用 pip 修改环境。

### 5. OneBot 端设置

以 NapCat 为例，在 WebSocket 服务端配置中：

- 启用正向 WebSocket；
- 监听端口与 `onebot.ws_url` 一致（默认 `6700`）；
- 如启用了 token，同步填写到 `onebot.access_token`；
- 确认机器人账号在目标群有**上传群文件**权限。

## ⚙️ 配置说明

完整注释见 [`jm_bot/config.yml`](jm_bot/config.yml)，常用项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `onebot.ws_url` | `ws://127.0.0.1:6700/` | OneBot 正向 WS 地址 |
| `onebot.access_token` | 空 | OneBot 鉴权 token |
| `onebot.reconnect_interval` | `3` | 重连基础间隔（秒），指数退避 |
| `bot.admins` | `[]` | 管理员 QQ 列表 |
| `bot.reject_multi_chapter` | `true` | **章节数超限拒绝开关** |
| `bot.max_chapters` | `10` | 允许的最大章节数（1-1000） |
| `bot.per_group_cooldown_enabled` | `true` | 是否启用分群冷却 |
| `bot.per_group_cooldown_seconds` | `60` | 每次 `/jm` 后的冷却秒数 |
| `bot.group_enabled_by_default` | `false` | 新群默认是否开启 |
| `bot.work_dir` | `data/work` | 临时工作目录（只清理任务子目录） |
| `bot.state_file` | `data/group_state.json` | 群开关持久化文件 |
| `bot.jmcomic_config` | `jm_bot/jm_pdf/config.yml` | jmcomic 下载配置 |
| `bot.download_workers` | `1` | 下载/PDF/压缩进程数 |
| `bot.metadata_timeout` | `60` | 元数据请求超时（秒） |
| `bot.pdf_resolution` | `150` | PDF 分辨率 |
| `bot.pdf_max_width` | `0` | PDF 单页最大宽度（0 不缩放） |
| `bot.zip_encrypt` | `true` | 是否创建 AES 加密 ZIP |
| `bot.zip_password_length` | `6` | 数字密码长度 |
| `bot.upload_timeout` | `300` | 群文件上传超时（秒） |
| `bot.keep_files` | `false` | 调试用：保留任务目录 |
| `bot.log_level` | `INFO` | 日志级别 |
| `bot.log_file` | 空 | 可选滚动日志文件 |

`work_dir` / `state_file` / `jmcomic_config` 的相对路径都相对于**项目根目录**，不受启动时当前目录影响。

## 🧠 处理流程

```text
/jm <ID>
  │
  ├─ 群开关 / 冷却 / 群内串行检查
  │
  ├─ 发送“正在检查”，读取漫画元数据（jmcomic API）
  │     └─ 章节数 > max_chapters ──► 拒绝，结束（不下载）
  │
  ├─ 发送“正在下载”，并撤回“正在检查”
  ├─ 下载到 data/work/<ID>-<时间>-<随机>/
  │     └─ 目录结构：<任务>/<漫画ID>/<章节ID>/图片
  │
  ├─ 下载后再次校验章节数（兜底拒绝 / 不完整检查）
  │
  ├─ 按章节顺序合成 PDF
  ├─ 创建 AES 加密 ZIP（密码只通过群消息发送，不写入文件名）
  ├─ upload_group_file 上传（URI 失败时自动回退普通路径）
  └─ 发送标题、页数、文件名、解压密码；清理临时目录
```

## 🔄 更新 jmcomic

在群内发送 `/更新jm`（仅管理员）：

- 仅当机器人运行在**项目自带的 `.venv`** 中时才允许；
- 在虚拟环境内执行 `python -m pip install --upgrade jmcomic`；
- 成功后退出码为 `3`，启动脚本会自动重启机器人；
- 如果使用 uv / conda / poetry 或系统包管理器，命令会拒绝执行，请用对应工具更新。

如果使用 uv / conda / poetry，或提示没有 pip，请用对应工具更新：

```bash
uv pip install -U jmcomic
# 或
.venv/bin/python -m pip install -U jmcomic
```

更新后重新启动即可。

## 🛠️ 常见问题

### 1. `bash: ./start_jm_bot: /bin/bash^M: bad interpreter`

旧版本仓库中的 Linux 脚本使用了 Windows 换行符（CRLF）。2.1.0 已改为 LF，并添加了 `.gitattributes`。如果你拉取的是旧代码，可以手动修复：

```bash
sed -i 's/\r$//' start_jm_bot
chmod +x start_jm_bot
```

### 2. `error: externally-managed-environment`

不要用 `sudo pip install` 安装依赖。使用：

```bash
./scripts/setup.sh
# 或
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

项目默认的 `.venv` 不会触发 PEP 668。

### 3. `No module named venv` / 创建虚拟环境失败

Debian/Ubuntu 需要：

```bash
sudo apt install python3-venv
```

### 4. 启动提示缺少依赖

```bash
./scripts/setup.sh          # Linux/macOS
scripts\setup.ps1           # Windows PowerShell
```

或按提示在虚拟环境中安装 `requirements.txt`。

### 5. 上传群文件失败

- 确认 OneBot 实现支持 `upload_group_file`；
- 确认机器人在群内有上传文件权限；
- 文件过大时 QQ 会拒绝，建议关闭 `reject_multi_chapter` 前先确认存储/上传限制；
- 机器人会先尝试 `file://` URI，失败后自动回退到普通绝对路径。

### 6. 漫画被拒绝，但我觉得章节数没超

机器人以 jmcomic 返回的 `episode_list` 为准。如果该 ID 实际章节数大于 `bot.max_chapters`，就会被拒绝。可以：

- 换成章节数不超过 `max_chapters` 的漫画 ID；
- 或让管理员调大 `bot.max_chapters`；
- 或设为 `bot.reject_multi_chapter: false` 完全取消限制（不推荐）。

### 7. 元数据读取失败

默认配置下不会继续下载，以免误下多章节本。请检查网络、代理、jmcomic 域名可用性，或稍后重试。管理员会收到包含堆栈的私聊通知（`notify_admins_on_error: true`）。

### 8. 依赖装好了但仍提示缺少模块

确认启动的是同一个解释器：

```bash
.venv/bin/python -m jm_bot.main --check
```

`--check` 会打印实际使用的 Python 路径和各依赖版本。

## 🧪 开发与测试

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/ruff check jm_bot tests scripts
```

> 仓库只包含**纯 Python 源码**。Cython/Nuitka 生成的 `.so`、`.pyd`、`.c`、`build/` 等产物已在 `.gitignore` 中忽略，不会提交到 GitHub；如需 native 构建，请在自己的部署环境本地生成。

测试覆盖：

- 命令解析（中英文、全角、CQ 码、非法输入）
- 配置校验（默认值、类型转换、非法路径、越界值）
- 章节数元数据识别与超限拒绝文案
- 章节超限拒绝流程（确保不进入下载、不上传）
- 10 章漫画的完整下载/合并 PDF 流程
- PDF 合成（多格式、损坏图片、自然排序、空目录）
- AES/普通 ZIP 压缩与密码
- OneBot 客户端工具函数（重连退避、响应判断、token 脱敏）

## 📦 目录结构

```text
.
├── jm_bot/
│   ├── config.yml           # 机器人配置
│   ├── config.py            # 配置加载 / 校验
│   ├── commands.py          # 命令解析
│   ├── texts.py             # 群消息文案（含章节超限拒绝）
│   ├── jmcomic_api.py       # 元数据 / 下载 / 任务配置
│   ├── bot.py               # 业务流水线
│   ├── main.py              # 入口 / 自检 / 信号处理
│   ├── onebot_ws.py         # OneBot v11 WS 客户端
│   ├── archive.py           # 加密 ZIP
│   ├── paths.py             # 路径 / 文件名 / 排序
│   ├── logging_utils.py     # 日志
│   ├── message.py           # 消息段构造
│   └── jm_pdf/
│       ├── config.yml       # jmcomic 下载配置
│       ├── main.py          # 图片 -> PDF
│       └── __init__.py
├── scripts/
│   ├── bootstrap.py         # venv 创建 / 依赖检查（PEP 668 安全）
│   ├── setup.sh             # Linux/macOS 安装
│   └── setup.ps1            # Windows 安装
├── tests/                   # pytest 测试
├── start_jm_bot             # Linux/macOS 启动器（LF）
├── start_jm_bot.bat         # Windows 启动器（CRLF）
├── requirements.txt
├── requirements-dev.txt
├── pyproject.toml
└── README.md
```

## 🙏 致谢

- [JMComic-Crawler-Python](https://github.com/hect0x7/JMComic-Crawler-Python)：漫画下载
- [image2pdf](https://github.com/salikx/image2pdf)：PDF 思路来源
- [OneBot v11](https://github.com/botuniverse/onebot-11)：协议规范

## 📄 开源协议

本项目采用 **GNU General Public License v3.0**，详见 [LICENSE](LICENSE)。

> 💡 请遵守相关法律法规，合理使用本工具。开发者不对滥用行为负责。
