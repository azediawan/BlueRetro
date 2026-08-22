/*
 * Copyright (c) 2019-2024, Jacques Gagnon
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef _LED_H_
#define _LED_H_

#include <stdint.h>

/* Red LED vocabulary. Blue says one thing only, "a controller lives on this
 * port"; everything else talks through the red one, so a lit blue LED can never
 * mean anything but connected.
 *
 * Told apart by rhythm before count: you recognise a rhythm across the room,
 * a count you have to stop and tally.
 */
enum {
    LED_PAT_OFF = 0,        /* connected, or nothing to report */
    LED_PAT_BOOT,           /* 10 Hz, coming up */
    LED_PAT_IDLE,           /* one short beat every 3 s, alive and unpaired */
    LED_PAT_SEARCH,         /* slow fade, looking for a controller */
    LED_PAT_CONNECTING,     /* 5 Hz, pairing and handshake in flight */

    /* Config app is connected: settings can change under us, trace capture is
     * paused, an OTA may be running. Deliberately the only mostly-lit pattern,
     * everything else is mostly dark, so it cannot be mistaken for anything.
     * Do not unplug while this one is showing.
     */
    LED_PAT_MAINTENANCE,

    /* Blink codes, motherboard beep style: count the blinks, read the table.
     * Value is the blink count, so LED_PAT_CODE_BASE + 2 blinks twice.
     */
    LED_PAT_CODE_BASE,
    LED_PAT_CODE_HID_REFUSED = LED_PAT_CODE_BASE + 2,   /* L2CAP CONN_RSP != success */
    LED_PAT_CODE_PAIR_FAILED = LED_PAT_CODE_BASE + 3,   /* AUTH_COMPLETE status != 0 */
    LED_PAT_CODE_LINK_LOST   = LED_PAT_CODE_BASE + 4,   /* DISCONN while connected */
};

void err_led_init(uint32_t package);
void err_led_pattern(uint32_t pattern);
void err_led_cfg_update(void);
void err_led_set(void);
void err_led_clear(void);
void err_led_pulse(void);
uint32_t err_led_get_pin(void);

#endif /* _LED_H_ */
