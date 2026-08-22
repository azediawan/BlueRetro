/*
 * SPDX-License-Identifier: Apache-2.0
 */

#include <stdio.h>
#include <stdbool.h>
#include <string.h>
#include <stdlib.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include "esp_app_desc.h"
#include "soc/soc.h"
#include "soc/uart_reg.h"
#include "adapter/adapter.h"
#include "adapter/config.h"
#include "adapter/memory_card.h"
#include "system/manager.h"
#include "console.h"

#define CONSOLE_UART 0
#define LINE_MAX 96
#define POLL_MS 50

/* Trace dump chunk. Small enough to yield often, big enough that the per-line
 * overhead stays irrelevant against 128 KB.
 */
#define DUMP_CHUNK 32

static char line[LINE_MAX];
static uint32_t line_len;

static inline uint32_t rx_count(void) {
    return (READ_PERI_REG(UART_STATUS_REG(CONSOLE_UART)) >> UART_RXFIFO_CNT_S) & UART_RXFIFO_CNT_V;
}

static inline uint8_t rx_byte(void) {
    /* AHB window: the direct FIFO address is not safe to read on the ESP32. */
    return (uint8_t)(READ_PERI_REG(UART_FIFO_AHB_REG(CONSOLE_UART)) & 0xFF);
}

static const char *inquiry_name(uint8_t v) {
    return (v == INQ_AUTO) ? "auto" : "manual";
}

static void cmd_version(void) {
    const esp_app_desc_t *d = esp_app_get_description();

    printf("+version %s\n", d->version);
    printf("+name %s\n", d->project_name);
}

static void cmd_cfg_show(void) {
    printf("+cfg system=%d multitap=%d inquiry=%d(%s) bank=0x%02X lock_inquiry=%d\n",
        config.global_cfg.system_cfg, config.global_cfg.multitap_cfg,
        config.global_cfg.inquiry_mode, inquiry_name(config.global_cfg.inquiry_mode),
        config.global_cfg.banksel,
#ifdef CONFIG_BLUERETRO_LOCK_INQUIRY_MANUAL
        1);
#else
        0);
#endif
    for (uint32_t i = 0; i < 2; i++) {
        printf("+out %lu dev_mode=%d acc_mode=%d\n", i,
            config.out_cfg[i].dev_mode, config.out_cfg[i].acc_mode);
    }
}

/* Returns true when the value was understood. Nothing is written to flash until
 * "save", so a typo costs nothing and a power cut mid-fiddle changes nothing.
 */
static bool cmd_cfg_set(const char *field, const char *value) {
    if (strcmp(field, "inquiry") == 0) {
        if (strcmp(value, "auto") == 0) {
            config.global_cfg.inquiry_mode = INQ_AUTO;
        }
        else if (strcmp(value, "manual") == 0) {
            config.global_cfg.inquiry_mode = INQ_MANUAL;
        }
        else {
            return false;
        }
#ifdef CONFIG_BLUERETRO_LOCK_INQUIRY_MANUAL
        printf("+note this build ignores the stored value and stays manual\n");
#endif
        return true;
    }
    if (strcmp(field, "bank") == 0) {
        config.global_cfg.banksel = (strcmp(value, "debug") == 0)
            ? CONFIG_BANKSEL_DBG : (uint8_t)strtoul(value, NULL, 0);
        return true;
    }
    if (strcmp(field, "system") == 0) {
        config.global_cfg.system_cfg = (uint8_t)strtoul(value, NULL, 0);
        return true;
    }
    if (strcmp(field, "multitap") == 0) {
        config.global_cfg.multitap_cfg = (uint8_t)strtoul(value, NULL, 0);
        return true;
    }
    return false;
}

static void cmd_trace(void) {
    static const char hex[] = "0123456789abcdef";
    uint8_t buf[DUMP_CHUNK];
    char out[DUMP_CHUNK * 2 + 2];

    printf("+trace begin %d\n", MC_BUFFER_SIZE);
    for (uint32_t off = 0; off < MC_BUFFER_SIZE; off += DUMP_CHUNK) {
        mc_read(off, buf, DUMP_CHUNK);
        for (uint32_t i = 0; i < DUMP_CHUNK; i++) {
            out[i * 2] = hex[buf[i] >> 4];
            out[i * 2 + 1] = hex[buf[i] & 0xF];
        }
        out[DUMP_CHUNK * 2] = 0;
        printf("%s\n", out);

        /* Yield regularly: this holds the CPU for a few hundred milliseconds
         * otherwise, and the Bluetooth stack does not tolerate that.
         */
        if ((off & 0x3FF) == 0) {
            vTaskDelay(1);
        }
    }
    printf("+trace end\n");
}

static void cmd_help(void) {
    printf("+help version              firmware version\n");
    printf("+help cfg                  show config\n");
    printf("+help cfg <field> <value>  inquiry auto|manual, bank 0-3|debug, system N, multitap N\n");
    printf("+help save                 persist config\n");
    printf("+help trace                dump the debug capture as hex\n");
    printf("+help reboot               restart the adapter\n");
}

static void dispatch(char *cmd) {
    char *argv[3] = {NULL, NULL, NULL};
    uint32_t argc = 0;

    for (char *tok = strtok(cmd, " \t"); tok && argc < 3; tok = strtok(NULL, " \t")) {
        argv[argc++] = tok;
    }
    if (argc == 0) {
        return;
    }

    if (strcmp(argv[0], "help") == 0) {
        cmd_help();
    }
    else if (strcmp(argv[0], "version") == 0) {
        cmd_version();
    }
    else if (strcmp(argv[0], "cfg") == 0) {
        if (argc == 1) {
            cmd_cfg_show();
        }
        else if (argc == 3 && cmd_cfg_set(argv[1], argv[2])) {
            cmd_cfg_show();
        }
        else {
            printf("+err bad cfg, try: cfg inquiry manual\n");
        }
    }
    else if (strcmp(argv[0], "save") == 0) {
        config_update(config_get_src());
        printf("+save ok\n");
    }
    else if (strcmp(argv[0], "trace") == 0) {
        cmd_trace();
    }
    else if (strcmp(argv[0], "reboot") == 0) {
        printf("+reboot now\n");
        sys_mgr_cmd(SYS_MGR_CMD_ADAPTER_RST);
    }
    else {
        printf("+err unknown '%s', try help\n", argv[0]);
    }
}

static void console_task(void *param) {
    while (1) {
        uint32_t pending = rx_count();

        while (pending--) {
            char c = (char)rx_byte();

            if (c == '\r' || c == '\n') {
                if (line_len) {
                    line[line_len] = 0;
                    line_len = 0;
                    dispatch(line);
                }
            }
            else if (line_len < LINE_MAX - 1) {
                line[line_len++] = c;
            }
            else {
                /* Overlong line: drop it rather than act on half a command. */
                line_len = 0;
            }
        }
        vTaskDelay(POLL_MS / portTICK_PERIOD_MS);
    }
}

void console_init(void) {
    printf("+console ready, type help\n");
    xTaskCreatePinnedToCore(&console_task, "console_task", 3072, NULL, 2, NULL, 0);
}
