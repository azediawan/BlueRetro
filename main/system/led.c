/*
 * Copyright (c) 2019-2024, Jacques Gagnon
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdbool.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <soc/efuse_reg.h>
#include "zephyr/atomic.h"
#include "system/gpio.h"
#include "driver/ledc.h"
#include "adapter/config.h"
#include "led.h"

#ifdef CONFIG_BLUERETRO_SYSTEM_SEA_BOARD
#define ERR_LED_PIN 32
#else
#define ERR_LED_PIN 17
#endif
#define PICO_ERR_LED_PIN 20

/* LED flags */
enum {
    ERR_LED_SET = 0,
};

static atomic_t led_flags = 0;
static TaskHandle_t err_led_task_hdl;
static uint8_t err_led_pin = ERR_LED_PIN;
static volatile uint32_t led_pattern = LED_PAT_OFF;

static inline void led_on(void) {
    ledc_set_duty_and_update(LEDC_HIGH_SPEED_MODE, LEDC_CHANNEL_0, hw_config.led_pulse_on_duty_cycle, 0);
}

static inline void led_off(void) {
    ledc_set_duty_and_update(LEDC_HIGH_SPEED_MODE, LEDC_CHANNEL_0, hw_config.led_pulse_off_duty_cycle, 0);
}

/* Sleep in slices so a state change shows up within ~50 ms instead of waiting out
 * the three second gap of an idle beat. False means the pattern changed, drop the
 * rest of this cycle.
 */
static bool led_wait(uint32_t ms, uint32_t pattern) {
    while (ms) {
        uint32_t slice = (ms > 50) ? 50 : ms;

        vTaskDelay(slice / portTICK_PERIOD_MS);
        if (led_pattern != pattern) {
            return false;
        }
        ms -= slice;
    }
    return true;
}

/* One cycle per loop, pattern re-read at the top: a state change lands on a cycle
 * boundary and never leaves half a blink code on screen.
 */
static void err_led_task(void *param) {
    while (1) {
        uint32_t pat = led_pattern;

        if (atomic_test_bit(&led_flags, ERR_LED_SET)) {
            vTaskSuspend(err_led_task_hdl);
            continue;
        }

        switch (pat) {
            case LED_PAT_OFF:
                led_off();
                vTaskSuspend(err_led_task_hdl);
                break;
            case LED_PAT_BOOT:          /* 10 Hz, radio coming up */
                led_on();
                led_wait(50, pat);
                led_off();
                led_wait(50, pat);
                break;
            case LED_PAT_IDLE:          /* heartbeat: alive, nothing to do */
                led_on();
                led_wait(120, pat);
                led_off();
                led_wait(2880, pat);
                break;
            case LED_PAT_SEARCH:        /* the fade that already existed */
                ledc_set_fade_time_and_start(LEDC_HIGH_SPEED_MODE, LEDC_CHANNEL_0, hw_config.led_pulse_duty_max,
                    hw_config.led_pulse_fade_time_ms, LEDC_FADE_NO_WAIT);
                led_wait(hw_config.led_pulse_fade_cycle_delay_ms, pat);
                ledc_set_fade_time_and_start(LEDC_HIGH_SPEED_MODE, LEDC_CHANNEL_0, hw_config.led_pulse_duty_min,
                    hw_config.led_pulse_fade_time_ms, LEDC_FADE_NO_WAIT);
                led_wait(hw_config.led_pulse_fade_cycle_delay_ms, pat);
                break;
            case LED_PAT_CONNECTING:    /* 5 Hz, handshake in flight */
                led_on();
                led_wait(100, pat);
                led_off();
                led_wait(100, pat);
                break;
            case LED_PAT_MAINTENANCE:   /* inverted: mostly lit, a slow wink */
                led_on();
                led_wait(1500, pat);
                led_off();
                led_wait(200, pat);
                break;
            default: {                  /* blink codes: N blinks, long gap, repeat */
                uint32_t blinks = pat - LED_PAT_CODE_BASE;

                while (blinks--) {
                    led_on();
                    if (!led_wait(150, pat)) {
                        break;
                    }
                    led_off();
                    if (!led_wait(150, pat)) {
                        break;
                    }
                }
                led_off();
                /* The gap is what makes the count readable out of the corner of
                 * an eye. Loops until the state changes, so a problem that is
                 * still there keeps saying so, and one that passed stops on its own.
                 */
                led_wait(2000, pat);
                break;
            }
        }
    }
}

void err_led_pattern(uint32_t pattern) {
    /* A fatal error owns the LED until power cycle, nothing overrides it. */
    if (atomic_test_bit(&led_flags, ERR_LED_SET)) {
        return;
    }

    /* Idle is ambient and carries no information, so it never talks over
     * something that does. The manager sets it on every tick, and without this a
     * blink code would be wiped before anyone could count it. A code holds until
     * a real state change replaces it: a new attempt, or a controller connecting.
     */
    if (pattern == LED_PAT_IDLE
            && led_pattern != LED_PAT_OFF
            && led_pattern != LED_PAT_IDLE
            && led_pattern != LED_PAT_BOOT) {
        return;
    }

    /* Maintenance outranks the rest while the config app holds the link: settings
     * are moving and tracing is paused, and that matters more than whatever the
     * radio was reporting a moment ago. It is cleared when the session drops.
     */
    if (led_pattern == LED_PAT_MAINTENANCE && pattern != LED_PAT_MAINTENANCE
            && pattern != LED_PAT_OFF) {
        return;
    }

    led_pattern = pattern;
    if (pattern == LED_PAT_OFF) {
        vTaskSuspend(err_led_task_hdl);
        led_off();
    }
    else {
        vTaskResume(err_led_task_hdl);
    }
}

void err_led_init(uint32_t package) {
    ledc_timer_config_t ledc_timer = {
        .duty_resolution = LEDC_TIMER_13_BIT,
        .freq_hz = hw_config.led_pulse_hz,
        .speed_mode = LEDC_HIGH_SPEED_MODE,
        .timer_num = LEDC_TIMER_0,
        .clk_cfg = LEDC_AUTO_CLK,
    };
    ledc_channel_config_t ledc_channel = {
        .channel    = LEDC_CHANNEL_0,
        .duty       = hw_config.led_pulse_off_duty_cycle,
        .gpio_num   = ERR_LED_PIN,
        .speed_mode = LEDC_HIGH_SPEED_MODE,
        .hpoint     = 0,
        .timer_sel  = LEDC_TIMER_0,
    };

    if (package == EFUSE_RD_CHIP_VER_PKG_ESP32PICOV302) {
        ledc_channel.gpio_num = PICO_ERR_LED_PIN;
        err_led_pin = PICO_ERR_LED_PIN;
    }

    ledc_timer_config(&ledc_timer);
    ledc_channel_config(&ledc_channel);
    ledc_fade_func_install(0);
    ledc_set_duty_and_update(LEDC_HIGH_SPEED_MODE, LEDC_CHANNEL_0, hw_config.led_pulse_off_duty_cycle, 0);

    xTaskCreatePinnedToCore(&err_led_task, "err_led_task", 768, NULL, 5, &err_led_task_hdl, 0);
    /* Held until the manager's first tick decides the real state. If it never
     * gets replaced, boot hung somewhere after this point.
     */
    err_led_pattern(LED_PAT_BOOT);
}

void err_led_cfg_update(void) {
    ledc_set_freq(LEDC_HIGH_SPEED_MODE, LEDC_TIMER_0, hw_config.led_pulse_hz);
}

void err_led_set(void) {
    vTaskSuspend(err_led_task_hdl);
    ledc_set_duty_and_update(LEDC_HIGH_SPEED_MODE, LEDC_CHANNEL_0, hw_config.led_pulse_on_duty_cycle, 0);
    atomic_set_bit(&led_flags, ERR_LED_SET);
}

void err_led_clear(void) {
    /* Only cancels the search pulse. Inquiry stops right after a device is found,
     * so by then the LED is already showing the handshake, or a blink code from
     * one that failed. Neither is ours to wipe.
     */
    if (led_pattern == LED_PAT_SEARCH) {
        err_led_pattern(LED_PAT_OFF);
    }
}

void err_led_pulse(void) {
    err_led_pattern(LED_PAT_SEARCH);
}

uint32_t err_led_get_pin(void) {
    return err_led_pin;
}
