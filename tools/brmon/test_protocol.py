# SPDX-License-Identifier: Apache-2.0
"""Runnable check for the parser and state machine. No hardware, no framework.

    python -m tools.brmon.test_protocol

The input is real console output: the boot lines came off COM3/COM4, the
connection sequence is the one captured while chasing a DS4 clone that pairs
and then refuses the HID channel.
"""
from .protocol import AdapterState, Kind, State, parse_line

# The failure we spent a whole session finding, as the console prints it.
SEC_BLOCK_SESSION = """
# internal_flag_init: External adapter
local_bdaddr: B0:CB:D8:01:0D:8A
I (808) gpio: GPIO[2]| InputEn: 0| OutputEn: 1| OpenDrain: 0
# bt_hci_cmd_periodic_inquiry
# BT_HCI_EVT_LE_ADVERTISING_REPORT
# BT_HCI_EVT_INQUIRY_RESULT
Inquiry dev: 0 type: 0 bdaddr: 64:0C:FB:05:06:07
# BT_HCI_EVT_CONN_COMPLETE
dev: 0 acl_handle: 0x0081
# BT_HCI_EVT_REMOTE_NAME_REQ_COMPLETE:
dev: 0 type: 3:0 Wireless Controller
# BT_HCI_EVT_AUTH_COMPLETE
# dev: 0 Pairing done
# dev: 0 conn refused scid: 0x0080 result: 0x0003 status: 0x0000
# BT_HCI_EVT_ENCRYPT_CHANGE
# DISCONN from dev: 0
"""


def check(name, got, want):
    assert got == want, f"{name}: esperado {want!r}, veio {got!r}"


def test_parses_the_lines_that_matter():
    kinds = [ev.kind for ev in map(parse_line, SEC_BLOCK_SESSION.splitlines()) if ev]
    for expected in (Kind.BOOT, Kind.ADAPTER_ADDR, Kind.DEVICE_FOUND, Kind.CONNECTED,
                     Kind.IDENTIFIED, Kind.PAIRED, Kind.CHANNEL_REFUSED,
                     Kind.ENCRYPTED, Kind.DISCONNECTED):
        assert expected in kinds, f"não reconheceu {expected}"


def test_noise_stays_noise():
    for line in ("I (808) gpio: GPIO[2]| InputEn: 0",
                 "# BT_HCI_EVT_LE_ADVERTISING_REPORT",
                 "# bt_hci_cmd_read_bd_addr"):
        check(line, parse_line(line).kind, Kind.NOISE)


def test_pulls_fields_apart():
    ev = parse_line("dev: 0 type: 3:0 Wireless Controller")
    check("nome", ev.name, "Wireless Controller")
    check("tipo", ev.detail, "PlayStation")

    ev = parse_line("# dev: 0 conn refused scid: 0x0080 result: 0x0003 status: 0x0000")
    check("código", ev.code, 0x0003)
    check("motivo", ev.detail, "bloqueio de segurança")

    ev = parse_line("Inquiry dev: 0 type: 0 bdaddr: 64:0C:FB:05:06:07")
    check("bdaddr", ev.bdaddr, "64:0C:FB:05:06:07")


def test_state_follows_the_session():
    st = AdapterState(port="COM4")
    seen = []
    for line in SEC_BLOCK_SESSION.splitlines():
        ev = parse_line(line)
        if ev and st.apply(ev):
            seen.append(st.state)

    check("endereço do adaptador", st.adapter_addr, "B0:CB:D8:01:0D:8A")
    assert State.PAIRED in seen, "não passou por pareado"
    assert State.FAILED in seen, "não registrou a falha"
    check("estado final", st.state, State.IDLE)          # desconectou no fim
    assert "bloqueio de segurança" in " ".join(t for _, t in st.timeline)


def test_allowlist_rejection_is_counted():
    st = AdapterState(port="COM3")
    st.apply(parse_line("# Inquiry: skipping unknown bdaddr, not in pairing mode"))
    check("ignorados", st.skipped, 1)


def test_ready_is_reached():
    st = AdapterState(port="COM4")
    for line in ("dev: 0 acl_handle: 0x0081", "# dev: 0 Pairing done", "# PS init done"):
        st.apply(parse_line(line))
    check("estado", st.state, State.READY)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"\n{len(tests)} verificações passaram")
