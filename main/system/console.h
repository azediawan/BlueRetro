/*
 * SPDX-License-Identifier: Apache-2.0
 */

#ifndef _BR_CONSOLE_H_
#define _BR_CONSOLE_H_

/* Command reader on the console UART.
 *
 * The firmware only ever wrote to UART0, so configuring an adapter meant going
 * through the BLE config app. This reads the RX side of the same cable, which
 * is already plugged in for the log, so one USB cable now does monitoring,
 * configuration and trace download.
 *
 * Deliberately polls the RX FIFO register instead of installing the UART
 * driver: installing it would reroute every printf in the firmware, and there
 * are over two hundred of them on the Bluetooth hot path. Commands arrive at
 * human typing speed and the FIFO holds 128 bytes, so a poll is plenty and the
 * output path stays exactly as it was.
 *
 * Replies are prefixed with '+' so a tool can pick them out of the '#' debug
 * stream. Type "help" on the port for the command list.
 */
void console_init(void);

#endif /* _BR_CONSOLE_H_ */
