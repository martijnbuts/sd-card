# microSD card next to esp32_hosted Wi-Fi on the ESP32-P4 (ESPHome)

Working microSD support for the **Waveshare ESP32-P4-WIFI6-Touch-LCD-7B** (and other ESP32-P4
boards that use an ESP32-C6 over SDIO for Wi-Fi) in ESPHome-dev, with Wi-Fi staying up.

Tested on ESPHome 2026.11.0-dev, ESP-IDF 6.1.0, esp32_hosted (ESP32-C6, SDIO) and a 64 GB exFAT card.

## The problem

The ESP32-P4 has **one** SDMMC controller with two slots. `esp32_hosted` uses slot 1 for the
Wi-Fi co-processor; the board's TF slot is wired to slot 0. Running the card in SDMMC mode
breaks Wi-Fi, even on ESP-IDF 6.1:

- the controller's clock divider is shared by both slots, so the card's 400 kHz probing changes
  the Wi-Fi chip's bus clock;
- `sdmmc_host_deinit()` (called by `sd_storage` after a failed mount and on unmount) removes
  **both** slots and deletes the controller, so the Wi-Fi goes with it;
- on ESP-IDF 6.0.1, `sdmmc_host_init()` fails when the controller is already claimed, and after
  `sdmmc_host_deinit_slot(0)` the ISR crashes on the next Wi-Fi interrupt.

## The solution

Run the card in **SPI mode** on its own SPI bus (SPI3) on the same pins. It never touches the
SDMMC controller, so Wi-Fi is unaffected. Throughput is about 2 MB/s, which is plenty for files,
logs and images.

| Card signal | SDMMC pin | SPI role |
|---|---|---|
| CLK | GPIO43 | SCLK |
| CMD | GPIO44 | MOSI |
| D0  | GPIO39 | MISO |
| D3  | GPIO42 | CS |
| D1 / D2 | GPIO40 / GPIO41 | unused, pull-up |

The slot is powered from on-chip LDO channel 4 (`esp_ldo`, `voltage: passthrough`).

## What is in this repo

- `components/sd_bus_shared`: a small ESPHome external component that makes the `storage` /
  `sd_storage` components from the [p1ngb4ck ESPHome fork](https://github.com/p1ngb4ck/esphome)
  (branch `testing_dev`) work on **upstream** ESPHome-dev with ESP-IDF 6.1:
  - provides `esp32.CONF_ENABLE_EXFAT`, which `sd_storage` expects from the fork's esp32 component;
  - defines `SD_OCR_SDHC_CAP`, missing in the fork's `sd_storage_spi.cpp` on IDF 6.1 (compile error);
  - `exfat: true`: builds a patched copy of the IDF `fatfs` component with exFAT, 64-bit LBA
    and TRIM off (same approach as `enable_exfat` in the fork), so SDXC cards work without
    reformatting;
  - `wrap_sdmmc: true` (optional, only for SDMMC mode): link-time wrappers that keep the shared
    SDMMC controller alive. Not needed and off in SPI mode.
- `example/p4_sd.yaml`: a package with the SPI bus, the card, mount/unmount buttons and
  mounted/free/size entities for Home Assistant.

## Usage

Your device config needs ESP-IDF 6.1:

```yaml
esp32:
  framework:
    type: esp-idf
    version: 6.1.0

packages:
  sd: !include p4_sd.yaml   # copy example/p4_sd.yaml next to your config
```

Component options:

```yaml
sd_bus_shared:
  exfat: true        # default false
  wrap_sdmmc: false  # default false; only for sd_storage type: sd_mmc
```

## Credits

- `storage` / `sd_storage` and the exFAT approach: [p1ngb4ck/esphome](https://github.com/p1ngb4ck/esphome)
  (branch `testing_dev`).
- Pinout and LDO power: Waveshare [esp32-p4-platform](https://github.com/waveshareteam/esp32-p4-platform),
  example `09_sdmmc`.
