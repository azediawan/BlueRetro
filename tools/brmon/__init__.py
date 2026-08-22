# SPDX-License-Identifier: Apache-2.0
"""BlueRetro over one USB cable: watch, configure, capture.

    python -m tools.brmon                 a janela
    python -m tools.brmon.test_protocol   verificacoes do nucleo, sem hardware
    python -m tools.brmon.trace <bin>     decodifica um trace na linha de comando

Layers, each unaware of the one above:

    protocol.py   console line -> event -> adapter state. Pure, fully tested
    command.py    sends commands and reads the '+' replies
    trace.py      the 128 KB capture buffer -> readable HCI/L2CAP/HIDP
    reader.py     one thread per port, reconnects on unplug
    ui.py         the window

Needs pyserial. Tkinter ships with Python.
"""
