---
name: use-picoxtools-debugger
description: Use the bundled PicoXtools MCP from Codex or Reasonix to safely identify, connect, and troubleshoot a user-declared PicoXtools 2 screenless debugger, especially UART/TTL with XIAO ESP32-S3. Trigger for PicoXtools/PicoXTools, 192.168.33.1, xShell, CDC-UART, GPIO4/GPIO5, UART loopback, DAP-Link, SWD, I2C, SPI, USB RNDIS/CDC-ECM, or device/Web-console detection problems.
---

# Use PicoXtools Debugger

Use the `picoxtools-debugger` MCP as the primary inspection and diagnostic layer. Adapt LCD-dependent official instructions to the user's screenless device using host-side USB, serial, volume, network, and Web evidence.

This skill is portable Agent Skills content. In Codex use the bundled plugin MCP. In Reasonix, load the repository `reasonix.toml` or the global configuration produced by `scripts/configure_reasonix.py`; MCP tools appear with the `mcp__picoxtools-debugger__` namespace. Do not weaken tool annotations or Reasonix `trusted_read_only_tools`: `capture_uart` and `uart_loopback_test` are intentionally not read-only.

## Non-negotiable firmware rule

> **PicoXtools 调试器自身的固件在任何条件下都不允许被修改。**

This is the user's clarification of the earlier “PQ” protection statement. It is absolute and cannot be overridden by a request to diagnose, repair, recover, upgrade, downgrade, or “try” firmware:

- Never upgrade, reflash, replace, patch, erase, downgrade, recover, or copy a UF2 onto the PicoXtools debugger.
- Never recommend a firmware modification as a workaround. Report that it is forbidden and continue with read-only or external diagnostics.
- Read-only firmware identity/version inspection is allowed.
- This rule protects PicoXtools firmware. It does not prohibit flashing a separately connected target such as XIAO ESP32-S3 after the user explicitly authorizes that target write.
- Do not confuse the SCSI enumeration field `PQ: 0` with a hardware component name.

The MCP intentionally exposes no PicoXtools firmware-write or updater tool.

## Required workflow

1. Call `safety_policy` at the start of a hardware session and preserve the immutable PicoXtools-firmware rule throughout the task.
2. Call `inspect_host` when detection, port selection, or USB state matters. Treat a port as PicoXtools only when associated with VID:PID `2e8a:000c`, `PicoXTools`/`PPVision`, or other Pico-specific evidence. A generic `usbmodem` name is insufficient.
3. Call `inspect_serial_owners` before opening CDC-UART. Report owners; do not kill processes automatically.
4. Call `quick_start` for the host OS. For a screenless unit, use USB/network/mass-storage evidence and `http://192.168.33.1`; never wait for LCD output.
5. Before connecting signals, call `wiring_guide` with the interface, target voltage, and target MCU when known. Stop if 3.3 V logic or common GND is not established.
6. For UART trouble, call `uart_diagnostic`, then use `capture_uart` for a bounded capture. Use `uart_loopback_test` only after the target is disconnected and the user confirms GPIO4 is wired directly to GPIO5.
7. If the Web console is unreliable, call `check_web_assets`. HTTP 200 for `/` is insufficient when referenced JS/CSS returns 404.
8. Call `troubleshoot` with the exact symptom and `search_docs` for source-linked background.
9. Use `list_device_files` and `read_device_file` only for relevant text evidence. Use `preview_init_config` only as a preview; it performs no write.

## UART facts and procedure

- PPVision's public source defines Pico GPIO4 as UART1 TX and GPIO5 as UART1 RX. That source is from the older public RP2040 implementation, so combine it with actual USB identity and loopback evidence for newer hardware.
- The official Web UART page says Web UART and USB CDC share UART1 and Web has priority. Use the Web UI's **Close serial** action before CDC; closing a browser tab alone is weaker evidence.
- The public CDC implementation suspends UART polling when DTR and RTS are both false. Set the intended 8N1 baud and assert DTR or RTS during CDC testing; treat this as source-backed behavior to verify on the attached firmware, not a universal RP2350 guarantee.
- CDC-UART is not xShell. Query `version` through Web xShell; do not send xShell commands to the CDC-UART port.

For XIAO ESP32-S3 at 115200:

```text
XIAO D6 / GPIO43 / TX  -> Pico P5 / GPIO5 / UART1 RX
Pico P4 / GPIO4 / TX   -> XIAO D7 / GPIO44 / RX  (add only after one-way RX passes)
GND                     -> GND
3V3                     -> not connected while both boards use USB power
```

Start with the first signal and GND only. Require observable bytes or heartbeats before adding the reverse direction. The verified local acceptance result on 2026-07-19 was continuous `PICO_HEARTBEAT` output through the Pico CDC-UART at 115200 8N1 with DTR/RTS asserted.

## Interface and target limits

- PicoXtools GPIO is 3.3 V logic only. Never connect 5 V logic directly.
- Do not claim PicoXtools powers the target. Avoid connecting 3V3 when both devices are independently USB powered.
- The official PicoXtools DAPLink path currently documents SWD. ESP32-S3 does not support SWD, so reject `swd` for ESP32-S3.
- GPIO4/GPIO5 provide UART logging/data, not ESP32-S3 hardware breakpoints.
- For ESP32-S3 breakpoint debugging, prefer the XIAO native USB Serial/JTAG path. External ESP32-S3 JTAG is a separate four-wire workflow and may involve irreversible eFuse choices; never burn eFuses implicitly.
- Do not infer that “screenless”, “Mini”, “second generation”, and “RP2350” are interchangeable. Official pages mix variants and eras. Report the user-declared variant separately from verified USB/version/board evidence.
- `linux-i2c=1` enables I2C-Tiny-USB and disables DAP-Link; it is Linux-only in this workflow. Windows 7 is not a supported recovery path in the official known-issues guidance.

## State-change boundaries

- `capture_uart` opens a verified CDC port and toggles DTR/RTS but transmits no payload.
- `uart_loopback_test` transmits only a generated token after two explicit physical confirmations; it must never run while a target remains connected.
- Target-MCU flashing overwrites target firmware and requires explicit user authorization for the exact target and port.
- Never auto-run xShell. Commands such as `rm`, `mv`, `format`, `flash`, `reboot`, `cc`, and `JS` may alter PicoXtools or a target.
- Never terminate a process, rewrite `.init`, format storage, burn an eFuse, or change an interface mode merely to continue diagnosis.

## Response style

Lead with the observed result and next safe action. Separate host evidence from inference, name exact ports/pins/baud, and report byte counts or matching frames. When blocked by the firmware rule, state that PicoXtools firmware modification is forbidden and offer only non-firmware alternatives.

Read [official-docs-audit.md](references/official-docs-audit.md) for capability limits and evidence age. Read [sources-and-scope.md](references/sources-and-scope.md) for the maintained primary-source index.
