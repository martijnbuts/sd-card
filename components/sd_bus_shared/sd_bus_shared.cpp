// Shared SDMMC controller on the ESP32-P4: microSD on slot 0, esp32_hosted (Wi-Fi chip) on slot 1.
// See __init__.py. Only active with sd_bus_shared: wrap_sdmmc: true (card in SDMMC mode);
// then linked in via -Wl,--wrap=... for the four functions below.
#include "esphome/core/defines.h"

#ifdef USE_SD_BUS_SHARED_WRAP

#include "esphome/core/log.h"

#include <driver/sdmmc_host.h>

static const char *const TAG = "sd_bus_shared";

// Slot 0 is added once and stays registered for good (see deinit_slot).
static bool s_slot0_ready = false;

extern "C" {

esp_err_t __real_sdmmc_host_init(void);
esp_err_t __real_sdmmc_host_init_slot(int slot, const sdmmc_slot_config_t *slot_config);
esp_err_t __real_sdmmc_host_deinit_slot(int slot);

// Controller already running (esp32_hosted or the SD card came first): just return OK.
// IDF 6.0.1 would otherwise return ESP_ERR_NOT_FOUND: there is only one SDMMC controller.
esp_err_t __wrap_sdmmc_host_init(void) {
  sdmmc_host_state_t state[1] = {};
  if (sdmmc_host_get_state(state) == ESP_OK && state[0].host_initialized) {
    ESP_LOGD(TAG, "SDMMC controller already running (shared with another slot): init skipped");
    return ESP_OK;
  }
  return __real_sdmmc_host_init();
}

// Add slot 0 only once; a later mount reuses the existing slot.
esp_err_t __wrap_sdmmc_host_init_slot(int slot, const sdmmc_slot_config_t *slot_config) {
  if (slot == 0 && s_slot0_ready) {
    ESP_LOGD(TAG, "SD slot 0 already exists: reused");
    return ESP_OK;
  }
  esp_err_t err = __real_sdmmc_host_init_slot(slot, slot_config);
  if (slot == 0 && err == ESP_OK)
    s_slot0_ready = true;
  return err;
}

// NEVER remove slot 0. Bug in ESP-IDF 6.0.1 (sd_host_sdmmc.c): the ISR reads
// ctlr->slot[ctlr->cur_slot_id]. After slot 0 is removed cur_slot_id still points at 0, and the
// next SDIO interrupt from the Wi-Fi chip (slot 1) reads a NULL slot: crash in sd_host_isr.
// Other slots are passed through.
esp_err_t __wrap_sdmmc_host_deinit_slot(int slot) {
  if (slot == 0) {
    ESP_LOGD(TAG, "SD slot 0 stays registered (shared controller)");
    return ESP_OK;
  }
  return __real_sdmmc_host_deinit_slot(slot);
}

// Deleting the whole controller would also take down the Wi-Fi chip (slot 1): never do it.
esp_err_t __wrap_sdmmc_host_deinit(void) {
  ESP_LOGD(TAG, "sdmmc_host_deinit ignored: controller is shared with the Wi-Fi chip");
  return ESP_OK;
}

}  // extern "C"

#endif  // USE_SD_BUS_SHARED_WRAP
