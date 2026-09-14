# Galvo scanner firmware (UC2 galvo add-on, XIAO ESP32-S3)

Firmware for the board that sweeps the light sheet. It is a **vendored copy**,
not our own code:

| | |
|---|---|
| Upstream | <https://github.com/openUC2/openUC2-LaserScanner> |
| Taken at | `9582634c57554a9e7e91d40912040ed5960be178` |
| Licence | MIT — see [LICENSE](LICENSE), © 2021 atomic14 |

It lives here rather than in a separate clone so the rig is reproducible from
one repository: the LSFT board's firmware is in [`../`](../README.md), the
galvo board's is here, and the client that drives it is
[`napari_lsft/_galvo.py`](../../src/napari_lsft/_galvo.py).

Only what is needed to build the XIAO target was copied. The upstream `KICAD/`
folder (35 MB of schematics) and `data/` (laser-show `.ild` files) were left
out; fetch the upstream repo if you need them.

## It does not build upstream — four changes were needed

Upstream `main` is broken. These are carried here; each is commented at the
site so it is not silently reverted.

**1. `src/SPIRenderer.cpp` — restored from `1ae37d2`.**
Merge `cc0dd46` dropped a `*/`, so a block comment ran from line 623 to 817 and
swallowed `SPIRenderer::start()` and `SPIRenderer::setSinglePosition()` — the
two functions `main.cpp` calls. The compiler reports it obliquely as
`"/*" within comment` plus a pile of `qualified-id` errors far below.

**2. `src/main.cpp` — `app_main()` still held pre-refactor code.**
Locals shadowing the global scan parameters, the 7-argument `SPIRenderer`
constructor and `setParameters()` (both now take 15), and a stray unclosed
`while (1) {`. Worse than the compile errors: a **local** `SPIRenderer
*renderer` shadowed the global one that `processSerial()` pushes every
`/galvo_act` through, so parameter updates would have gone nowhere.

**3. `src/SPIRenderer.cpp` — SPI clock 20 MHz → 2 MHz.**
20 MHz is the MCP4822's absolute maximum, with no margin for trace capacitance
or a 3.3 V drive. Transfers are 16 bits, so the cost is nil.

**4. `src/main.cpp` + `SPIRenderer.cpp` — the sweep rate was set by the loop.**
`vTaskDelay(pdMS_TO_TICKS(10))` per frame plus a per-frame `ESP_LOGI` over
USB-CDC capped the sweep near 30 Hz — too slow for a light sheet. Now
`vTaskDelay(1)` and `ESP_LOGD`, so the scan itself sets the rate (~900 Hz
reachable with few points).

All four are worth reporting upstream; 1 and 2 block anyone from building
`main` at all.

## Build and flash

```bash
cd firmware/galvo
pio run -e UC2_3_Xiao -j 2
```

Two traps, both already hit:

**Use `-j 2`.** Default parallelism spawns enough `cc1plus` to exhaust RAM
(`cc1plus.exe: out of memory allocating 8392703 bytes`).

**PlatformIO cannot upload to this board.** Its `upload_speed` was lowered to
115200 here (the S3's USB-Serial/JTAG baud rate is virtual and renegotiating it
drops the link), but the esptool *stub* still fails with "Unable to verify
flash chip connection". Flash with `--no-stub` instead:

```bash
cd .pio/build/UC2_3_Xiao
python <platformio>/packages/tool-esptoolpy/esptool.py \
    --chip esp32s3 --port COM7 --no-stub \
    --before default_reset --after hard_reset write_flash -z \
    0x0 bootloader.bin 0x8000 partitions.bin \
    0xd000 ota_data_initial.bin 0x10000 firmware.bin
```

`platformio.ini` also hardcodes `upload_port = /dev/cu.usbmodem1101`, a macOS
path — irrelevant when flashing with esptool directly.

## Talking to it

Protocol and quirks are in [`SERIAL_INTERFACE.md`](SERIAL_INTERFACE.md) and, for
what actually matters on this rig, in
[`acquisition/galvo_scanner.md`](../../acquisition/galvo_scanner.md): the board
reboots when the port is opened, serial is serviced once per frame so long
commands are silently dropped, nothing persists across a reboot, **Y is the
fast axis**, and the ±10 V output must be clamped to the ±5 V the scanner takes.

`python tools/galvo_selftest.py --port COM7` in [`../tools/`](../tools/) walks
the chain from the outside in.
