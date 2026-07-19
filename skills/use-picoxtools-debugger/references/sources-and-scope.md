# Sources and scope

Verified 2026-07-19. The plugin stores concise, attributed facts rather than mirroring the sites.

## PicoXtools primary sources

- [Quick start, USB identity, pinout, 3.3 V warning](https://www.cnsee.net/guide/getting-started.html)
- [Boot flow, RNDIS/CDC-ECM, `.init`, I2C-Tiny-USB](https://www.cnsee.net/guide/bootup.html)
- [Known USB issues](https://www.cnsee.net/guide/known_issues.html)
- [UART assistant and Web-over-CDC priority](https://www.cnsee.net/guide/webconsole/uart_assistant.html)
- [xShell commands and connection boundary](https://www.cnsee.net/guide/xshell.html)
- [SPI assistant](https://www.cnsee.net/guide/webconsole/spi_assistant.html)
- [Release history and mixed hardware packages](https://www.cnsee.net/guide/changelog.html)
- [PicoXtools offline flashing/DAPLink scope](https://www.cnsee.net/guide/flash_mcu.html)
- [PPVision public UART pin definitions at fixed commit](https://github.com/ppvision/PicoXTools/blob/7d5e96437b8977d7aa6f04ec3d73c09997312ec0/apps/picprobe/include/board_pico_config.h#L42-L49)
- [PPVision public CDC line-state behavior at fixed commit](https://github.com/ppvision/PicoXTools/blob/7d5e96437b8977d7aa6f04ec3d73c09997312ec0/apps/picprobe/src/cdc_uart.c#L132-L169)

The public PPVision repository is RP2040-era. GPIO direction and DTR/RTS behavior are valid source evidence for that implementation and useful diagnostic hypotheses, but they are not represented as an unconditional specification for all later RP2350 firmware.

## Target-board primary sources

- [Seeed XIAO ESP32-S3 pin multiplexing](https://wiki.seeedstudio.com/xiao_esp32s3_pin_multiplexing/)
- [Espressif built-in ESP32-S3 USB Serial/JTAG](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/jtag-debugging/configure-builtin-jtag.html)
- [Espressif external ESP32-S3 JTAG and eFuse cautions](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/api-guides/jtag-debugging/configure-other-jtag.html)

## User firmware policy

The user's final clarification is authoritative for this plugin:

> **PicoXtools 调试器自身的固件在任何条件下都不允许被修改。**

Official firmware-upgrade documentation is retained only to identify prohibited operations and variant risk. The skill and MCP must never turn it into PicoXtools upgrade, reflash, replacement, patch, erase, downgrade, recovery, or UF2-copy instructions. Explicitly authorized target-MCU flashing remains separate.

## Screenless and variant scope

The user identifies the attached unit as PicoXtools 2 screenless. Official pages mix LCD, Mini, RP2040, RP2350, and release-specific material, so the plugin adapts interaction to a screenless device without inferring that screenless equals Mini or RP2350. Variant-sensitive claims require USB/version/board evidence.

## Local verified observations

On 2026-07-19 the attached unit enumerated as PPVision PicoXTools VID:PID `2e8a:000c`; CDC-UART mapped to `/dev/cu.usbmodem00004`. GPIO4-to-GPIO5 loopback passed. With only XIAO D6/GPIO43/TX connected to Pico GPIO5/RX plus common GND, capture at 115200 8N1 with DTR/RTS asserted received `PICO_UART_READY` and continuous `PICO_HEARTBEAT` messages. These observations validate the attached setup, not every PicoXtools variant.

The device root HTML referenced JS/CSS assets that returned 404 during one check. This is why the MCP verifies referenced assets separately from HTTP reachability. It does not authorize firmware repair.

## MCP boundaries

The MCP performs host discovery, read-only volume inspection, Web asset reads, documentation lookup, bounded UART capture, and an explicitly confirmed physical loopback. It does not write PicoXtools files, invoke xShell, kill processes, burn eFuses, flash a target, or modify PicoXtools firmware.
