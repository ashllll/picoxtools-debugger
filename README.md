# PicoXtools Debugger

[![CI](https://github.com/ashllll/picoxtools-debugger/actions/workflows/ci.yml/badge.svg)](https://github.com/ashllll/picoxtools-debugger/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

一个同时支持 **Codex** 与 **Reasonix** 的 Agent Skill + 本地 stdio MCP 服务，用于安全识别 PicoXtools、检查 USB/Web 状态、诊断串口占用，并验证 PicoXtools 与 XIAO ESP32-S3 的 UART 链路。

> [!IMPORTANT]
> **PicoXtools 调试器自身的固件在任何条件下都不允许被修改。**
>
> 本项目不提供 PicoXtools 固件升级、UF2 复制、重刷、替换、修补、擦除、降级或恢复工具。只读查询固件身份和版本是允许的。经用户明确授权后烧录独立目标 MCU（例如 XIAO ESP32-S3）属于另一项操作，不受此规则禁止。

## 功能

| MCP 工具 | 用途 | 状态影响 |
|---|---|---|
| `safety_policy` | 返回不可绕过的 Pico 固件与 3.3 V 安全规则 | 只读 |
| `inspect_host` | 按 VID:PID、产品、卷和网络证据识别 PicoXtools | 只读 |
| `inspect_serial_owners` | 检查 CDC 串口与 Web TCP 连接占用 | 只读 |
| `check_web_assets` | 检查首页引用的同源 JS/CSS 是否可用 | 只读网络请求 |
| `list_device_files` | 枚举 `PICOXTOOLS` 卷文件 | 只读 |
| `read_device_file` | 有边界地读取 UTF-8 文本配置 | 只读 |
| `preview_init_config` | 生成 `.init` 配置预览，不写入设备 | 只读 |
| `quick_start` | 根据 macOS、Windows 或 Linux 给出无屏启动流程 | 只读 |
| `wiring_guide` | 给出 UART/I2C/SPI/SWD/JTAG 安全边界 | 只读 |
| `uart_diagnostic` | 给出 Web/CDC 仲裁、DTR/RTS 与自环排查顺序 | 只读 |
| `capture_uart` | 从经过 VID:PID 验证的 CDC-UART 限时采集 | 打开端口并设置 DTR/RTS，不发送载荷 |
| `uart_loopback_test` | 在双重确认后执行 GPIO4→GPIO5 自环 | 仅发送随机测试令牌 |
| `troubleshoot` | 根据症状生成有序诊断步骤 | 只读 |
| `search_docs` | 搜索附带来源与适用范围的知识库 | 只读 |

设备识别不会把任意 `/dev/cu.usbmodem*` 当作 PicoXtools。优先要求 VID:PID `2e8a:000c`、`PicoXTools`/`PPVision` 产品身份或 `PICOXTOOLS` 卷等专属证据。

## XIAO ESP32-S3 UART 接线

两块设备都由 USB 供电时：

```text
XIAO D6 / GPIO43 / TX  -> Pico P5 / GPIO5 / UART1 RX
Pico P4 / GPIO4 / TX   -> XIAO D7 / GPIO44 / RX  （单向接收稳定后再增加）
GND                     -> GND
3V3                     -> 不连接
```

建议先只连接 `XIAO TX -> Pico RX` 和 GND，在 115200 8N1 下确认持续收到日志后，再增加反向线路。Web UART 与 USB CDC 共用 UART1，使用 CDC 前应在 Web 页面中明确关闭 Web 串口。

ESP32-S3 不支持 Arm SWD。GPIO4/GPIO5 这里只用于 UART 日志或数据，不提供 ESP32-S3 硬件断点；断点调试应优先使用 XIAO 自带 USB Serial/JTAG。

## 环境要求

- Codex Desktop/CLI，或 Reasonix 1.x CLI/Desktop
- Python 3.10+
- [PySerial](https://pyserial.readthedocs.io/) 3.5（仅 UART 采集和自环需要）
- macOS、Linux 或 Windows；部分主机诊断能力按系统有所不同

安装 Python 依赖：

```bash
python3 -m pip install -r mcp/requirements.txt
```

没有 PySerial 时，文档查询、接线指导、USB/Web 检查仍可使用；UART 采集工具会返回明确的依赖错误。

## 安装到 Codex

克隆到个人插件目录：

```bash
git clone https://github.com/ashllll/picoxtools-debugger.git ~/plugins/picoxtools-debugger
```

确保个人市场文件 `~/.agents/plugins/marketplace.json` 的 `plugins` 数组包含以下条目：

```json
{
  "name": "picoxtools-debugger",
  "source": {
    "source": "local",
    "path": "./plugins/picoxtools-debugger"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Developer Tools"
}
```

然后安装：

```bash
codex plugin add picoxtools-debugger@personal
```

安装或更新后请新建 Codex 任务，使技能与 MCP 重新加载。调用示例：

```text
Use $use-picoxtools-debugger to inspect my PicoXtools and capture the XIAO ESP32-S3 UART log.
```

## 安装到 Reasonix

[Reasonix 1.x](https://reasonix.cn/guide/) 原生支持 Claude/Agent Skills 格式的 `SKILL.md`、项目根 `.mcp.json`，以及 `reasonix.toml` 中的 `[skills].paths` 和 `[[plugins]]`。本仓库同时提供项目级配置和安全的全局安装脚本。

先克隆仓库并安装 PySerial：

```bash
git clone https://github.com/ashllll/picoxtools-debugger.git
cd picoxtools-debugger
python3 -m pip install -r mcp/requirements.txt
```

直接在仓库目录启动 Reasonix 时，根目录 [`reasonix.toml`](reasonix.toml) 会注册技能与 MCP。若希望在其他项目中全局使用，先预览配置变更：

```bash
python3 scripts/configure_reasonix.py
```

确认路径后应用；脚本会先备份现有配置、保留其他技能/MCP，并进行 TOML 解析验证：

```bash
python3 scripts/configure_reasonix.py --apply
```

如果电脑同时存在新旧两个 Reasonix 配置文件，脚本会拒绝猜测，需明确指定实际使用的文件，例如：

```bash
python3 scripts/configure_reasonix.py \
  --config ~/.reasonix/config.toml \
  --apply
```

如需固定 MCP 使用的 Python（该环境必须装有 PySerial），可增加 `--python /absolute/path/to/python3`。

验证配置：

```bash
python3 scripts/configure_reasonix.py \
  --config ~/.reasonix/config.toml \
  --check
```

重启 Reasonix 后：

1. 用 `/skills` 确认 `use-picoxtools-debugger` 已发现；
2. 用 `/mcp` 确认 `picoxtools-debugger` 已连接；
3. 请求使用 `use-picoxtools-debugger` 检查 PicoXtools 或采集 UART。

Reasonix 的 `trusted_read_only_tools` 只包含 MCP 明确标为只读的 12 个工具。`capture_uart` 和 `uart_loopback_test` 不会被错误加入只读信任列表。

## 项目结构

```text
.codex-plugin/plugin.json                   插件清单
.mcp.json                                   stdio MCP 配置
reasonix.toml                               Reasonix 项目级技能/MCP 配置
scripts/configure_reasonix.py               Reasonix 全局配置安装器
mcp/server.py                               MCP 服务
mcp/requirements.txt                        UART 依赖
knowledge/picoxtools.json                   带来源和适用范围的知识库
skills/use-picoxtools-debugger/SKILL.md     技能工作流与安全规则
skills/.../references/                      官方资料审计与来源索引
tests/test_server.py                        单元、协议与安全回归测试
tests/test_reasonix.py                      Reasonix 配置与信任边界测试
```

## 验证

运行仓库内测试：

```bash
python3 -m unittest discover -s tests -v
python3 -m json.tool knowledge/picoxtools.json >/dev/null
python3 -m json.tool .codex-plugin/plugin.json >/dev/null
python3 -m json.tool .mcp.json >/dev/null
python3 -c 'import tomllib; tomllib.load(open("reasonix.toml", "rb"))'
python3 scripts/configure_reasonix.py --check --config /path/to/reasonix/config.toml
```

如果本机存在 Codex 系统技能，也可以运行完整插件校验：

```bash
python3 ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py \
  skills/use-picoxtools-debugger
python3 ~/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py .
```

本地硬件验收曾完成以下检查：

- 识别 PPVision PicoXTools VID:PID `2e8a:000c`；
- GPIO4/TX → GPIO5/RX 自环通过；
- 在 115200 8N1、DTR/RTS 有效时，通过 MCP 持续收到 XIAO `PICO_HEARTBEAT`。

## 资料与限制

- PicoXtools 官方页面混有 RP2040/RP2350、LCD/无屏、Mini 与不同固件时期的内容。本项目不会在缺乏设备证据时把这些变体视为等同。
- PPVision 公开 UART 源码停留在较早的 RP2040 实现，因此 GPIO 方向和 DTR/RTS 行为会结合现场证据使用，不会冒充所有 RP2350 固件的无条件规范。
- MCP 不运行 xShell、不终止进程、不写 PicoXtools 文件、不烧 eFuse、不烧录目标，也绝不修改 PicoXtools 固件。
- Reasonix 会把声明了 MCP `readOnlyHint: true` 的工具作为只读工具；项目配置又以 `trusted_read_only_tools` 明确收敛 Plan/只读研究阶段可用的工具。串口采集和自环仍属于需要正常权限判断的本地 I/O。
- 完整官方资料审计见 [`official-docs-audit.md`](skills/use-picoxtools-debugger/references/official-docs-audit.md)。

## 贡献

欢迎提交 Issue 或 Pull Request。涉及新硬件变体、引脚和高风险操作的改动必须附上官方一手来源，并为安全边界添加回归测试。任何 PicoXtools 固件修改能力都不会被接受。

## 开源协议

本项目使用宽松的 [MIT License](LICENSE)。

---

English summary: this repository provides a portable Agent Skill and local stdio MCP server for Codex and Reasonix. PicoXtools firmware is immutable by project policy. Reasonix support includes a project `reasonix.toml`, a backup-first global config installer, and exact read-only tool trust tests.
