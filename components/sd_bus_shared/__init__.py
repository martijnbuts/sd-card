"""Glue for an SD card next to esp32_hosted on the ESP32-P4.

Used together with the storage / sd_storage components from the p1ngb4ck ESPHome fork
(https://github.com/p1ngb4ck/esphome, branch testing_dev) on upstream ESPHome-dev.

1. Always: sd_storage from the fork reads esp32.CONF_ENABLE_EXFAT. That constant only exists in
   the fork's esp32 component (upstream ESPHome does not have the option), so it is provided here.

2. Always: the fork's sd_storage_spi.cpp uses SD_OCR_SDHC_CAP without including
   sd_protocol_defs.h (sd_storage.cpp does include it). On ESP-IDF 6.1 the header no longer
   arrives through another include, so the build fails. The same define as in sd_protocol_defs.h
   ((1<<30), HCS bit) is added, token-for-token identical so the real header may redefine it
   without a warning.

3. exfat: true -- read exFAT cards (SDXC, 64 GB and larger) without reformatting.
   In FatFs exFAT is a fixed #define in ffconf.h (no Kconfig option). Like enable_exfat in the
   fork's esp32 component: a copy of the IDF fatfs component in the build directory with
   FF_FS_EXFAT 1, FF_LBA64 1 and FF_USE_TRIM 0 (TRIM returned ESP_ERR_INVALID_RESPONSE over SPI).
   The copy is added to idf_component.yml as local component 'fatfs' and takes precedence over
   the built-in one. Long file names (required for exFAT) are already enabled by ESPHome via
   require_fatfs().

4. Only with wrap_sdmmc: true (card in SDMMC mode): share the SDMMC controller.
   The P4 has a single SDMMC controller. esp32_hosted uses it for the Wi-Fi co-processor (C6,
   SDIO on slot 1); the microSD slot is slot 0 (e.g. Waveshare ESP32-P4-WIFI6-Touch-LCD-7B).
   With the plain API this goes wrong:
     - IDF 6.0.1: sdmmc_host_init() returns ESP_ERR_NOT_FOUND when the controller is claimed;
     - IDF 6.0.1 and 6.1: sdmmc_host_deinit() removes both slots and deletes the controller,
       taking Wi-Fi down with it (sd_storage calls it after a failed mount and on unmount);
     - IDF 6.0.1: after sdmmc_host_deinit_slot(0) the ISR crashes on the next interrupt from the
       Wi-Fi chip (cur_slot_id still points at the removed slot 0).
   These calls are intercepted at link time (-Wl,--wrap), see sd_bus_shared.cpp.
   Note: even with this, the controller's clock divider is shared by both slots, which is why
   SPI mode (own SPI bus, never touches the SDMMC controller) is the recommended setup.
"""

from pathlib import Path
import re
import shutil

import esphome.codegen as cg
from esphome.components import esp32
from esphome.components.esp32.const import VARIANT_ESP32P4
import esphome.config_validation as cv
from esphome.core import CORE

DEPENDENCIES = ["esp32"]

CONF_WRAP_SDMMC = "wrap_sdmmc"
CONF_EXFAT = "exfat"

if not hasattr(esp32, "CONF_ENABLE_EXFAT"):
    esp32.CONF_ENABLE_EXFAT = "enable_exfat"

CONFIG_SCHEMA = cv.All(
    cv.Schema(
        {
            cv.Optional(CONF_WRAP_SDMMC, default=False): cv.boolean,
            cv.Optional(CONF_EXFAT, default=False): cv.boolean,
        }
    ),
    esp32.only_on_variant(supported=[VARIANT_ESP32P4]),
)

WRAPPED = (
    "sdmmc_host_init",
    "sdmmc_host_init_slot",
    "sdmmc_host_deinit_slot",
    "sdmmc_host_deinit",
)

_EXFAT_PATCHES = (
    ("FF_FS_EXFAT", "1"),
    ("FF_LBA64", "1"),
    ("FF_USE_TRIM", "0"),
)
_EXFAT_MARKER = ".sd_bus_shared_exfat"


def _exfat_fatfs_copy(idf_ver: str) -> str:
    """Patched copy of the IDF fatfs component (adapted from _sync_exfat_fatfs_override in the
    esp32 component of the p1ngb4ck fork). The directory must be called 'fatfs': IDF names a
    component after its directory. Stamped with the IDF version so another IDF re-copies."""
    from esphome.espidf.framework import _get_framework_path, check_esp_idf_install

    dest = Path(CORE.build_path) / "components-src" / "fatfs"
    marker = dest / _EXFAT_MARKER
    patches = ",".join(f"{k}={v}" for k, v in _EXFAT_PATCHES)
    stamp = f"v1:{idf_ver}:{patches}"
    if marker.is_file() and marker.read_text() == stamp:
        return str(dest)
    src = _get_framework_path(idf_ver) / "components" / "fatfs"
    if not src.is_dir():
        check_esp_idf_install(idf_ver, targets=["esp32p4"])
    if not src.is_dir():
        raise cv.Invalid(f"exfat: IDF component fatfs not found in {src}")
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest)
    ffconf = dest / "src" / "ffconf.h"
    text = ffconf.read_text()
    for key, value in _EXFAT_PATCHES:
        text, n = re.subn(rf"#define[ \t]+{key}[ \t]+\S+", f"#define {key} {value}", text)
        if n != 1:
            raise cv.Invalid(f"exfat: {key} not found in ffconf.h of IDF {idf_ver}")
    # Disabled Kconfig bools produce no #define, yet ff.c uses a few of them in plain C
    # expressions. Inside IDF that resolves; in a project copy it became 'undeclared identifier'.
    # So every CONFIG_ symbol from ffconf.h that is never probed with #ifdef/defined() defaults
    # to 0 when it does not exist (a defined symbol keeps its value).
    probed = set()
    skip_dirs = {"test_apps", "host_test", "fatfs_utils"}
    for f in dest.rglob("*"):
        if f.suffix not in (".c", ".h") or skip_dirs.intersection(p.name for p in f.parents):
            continue
        for line in f.read_text(errors="replace").splitlines():
            if re.search(r"#\s*if(n?def)?\b", line) or "defined" in line:
                probed.update(re.findall(r"\bCONFIG_[A-Z0-9_]+\b", line))
    symbols = sorted(set(re.findall(r"\bCONFIG_[A-Z0-9_]+\b", text)) - probed)
    guards = "".join(f"#ifndef {sym}\n#define {sym} 0\n#endif\n" for sym in symbols)
    include_line = '#include "sdkconfig.h"\n'
    if include_line not in text:
        raise cv.Invalid("exfat: ffconf.h has no sdkconfig.h include to anchor on")
    header = "\n/* sd_bus_shared exFAT: defaults, see __init__.py */\n"
    text = text.replace(include_line, f"{include_line}{header}{guards}", 1)
    ffconf.write_text(text)
    marker.write_text(stamp)
    return str(dest)


async def to_code(config):
    cg.add_build_flag("-DSD_OCR_SDHC_CAP=(1<<30)")
    if config[CONF_EXFAT]:
        path = _exfat_fatfs_copy(str(esp32.idf_version()))
        esp32.add_idf_component(name="fatfs", path=path)
    if not config[CONF_WRAP_SDMMC]:
        return
    cg.add_define("USE_SD_BUS_SHARED_WRAP")
    for symbol in WRAPPED:
        cg.add_build_flag(f"-Wl,--wrap={symbol}")
