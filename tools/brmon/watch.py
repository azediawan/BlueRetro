# SPDX-License-Identifier: Apache-2.0
"""Narrate one adapter's console: python -u -m tools.brmon.watch COM4

Prints one line per meaningful event, so it can drive a live commentary without
drowning in the ESP-IDF chatter and BLE advertising reports.
"""
import sys
import time

import serial

from .protocol import AdapterState, Kind, parse_line

LOUD = {
    Kind.BOOT, Kind.INQUIRY_START, Kind.DEVICE_FOUND, Kind.DEVICE_SKIPPED,
    Kind.CONNECTED, Kind.IDENTIFIED, Kind.PAIRED, Kind.ENCRYPTED,
    Kind.HID_READY, Kind.CHANNEL_REFUSED, Kind.ERROR, Kind.DISCONNECTED,
}


def main(port: str) -> None:
    st = AdapterState(port=port)
    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 921600, 0.2
    s.dtr = s.rts = False          # attach without rebooting the adapter
    s.open()
    print(f"escutando {port}, estado atual: {st.state.value}")

    buf = b""
    last_state = st.state
    while True:
        try:
            chunk = s.read(8192)
        except (serial.SerialException, OSError) as e:
            print(f"porta caiu: {e}")
            return
        if not chunk:
            continue
        buf += chunk
        *lines, buf = buf.split(b"\n")
        buf = bytes(buf)
        for raw in lines:
            text = raw.decode("utf-8", "replace").strip()
            ev = parse_line(text)
            if not ev or not st.apply(ev):
                continue
            if ev.kind in LOUD:
                extra = f" | {ev.detail}" if ev.detail else ""
                print(f"[{time.strftime('%H:%M:%S')}] {ev.kind.value}{extra}  ->  "
                      f"{st.state.value}: {st.note}")
                last_state = st.state


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "COM4")
