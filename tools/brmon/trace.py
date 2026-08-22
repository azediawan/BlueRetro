#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Decode a br_debug_trace.bin captured by BlueRetro's debug mode.

Usable two ways: the window imports decode(), or run it directly with
    python -m tools.brmon.trace <arquivo.bin>

Standalone: no bluez, no socat, no Wireshark. Prints the SYS_NOTE log lines
and dissects HCI / L2CAP / HIDP inline. -w still emits a .btsnoop file for
Wireshark if you happen to have it.
"""
import struct
import sys
from argparse import ArgumentParser

CMD, EVT, ACL_TX, ACL_RX, SYS_NOTE = 2, 3, 4, 5, 12
NAMES = {CMD: 'CMD', EVT: 'EVT', ACL_TX: 'ACL_TX', ACL_RX: 'ACL_RX', SYS_NOTE: 'NOTE'}
FLAGS = {CMD: 0x02, EVT: 0x03, ACL_TX: 0x00, ACL_RX: 0x01}
EPOCH = 0x00E03AB44A676000

EVT_NAME = {
    0x01: 'INQUIRY_COMPLETE', 0x02: 'INQUIRY_RESULT', 0x03: 'CONN_COMPLETE',
    0x04: 'CONN_REQUEST', 0x05: 'DISCONN_COMPLETE', 0x06: 'AUTH_COMPLETE',
    0x07: 'REMOTE_NAME_REQ_COMPLETE', 0x08: 'ENCRYPT_CHANGE', 0x0b: 'REMOTE_FEATURES',
    0x0c: 'REMOTE_VERSION_INFO', 0x0e: 'CMD_COMPLETE', 0x0f: 'CMD_STATUS',
    0x12: 'ROLE_CHANGE', 0x13: 'NUM_COMPLETED_PACKETS', 0x14: 'MODE_CHANGE',
    0x16: 'PIN_CODE_REQ', 0x17: 'LINK_KEY_REQ', 0x18: 'LINK_KEY_NOTIFY',
    0x1a: 'DATA_BUF_OVERFLOW', 0x1b: 'MAX_SLOTS_CHANGE', 0x22: 'INQUIRY_RESULT_WITH_RSSI',
    0x23: 'REMOTE_EXT_FEATURES', 0x2f: 'EXTENDED_INQUIRY_RESULT',
    0x30: 'ENCRYPT_KEY_REFRESH_COMPLETE', 0x31: 'IO_CAPA_REQ', 0x32: 'IO_CAPA_RESP',
    0x33: 'USER_CONFIRM_REQ', 0x36: 'SSP_COMPLETE', 0x3e: 'LE_META',
}
# events whose first body byte is a status code
EVT_STATUS = {0x03, 0x05, 0x06, 0x07, 0x08, 0x0b, 0x0c, 0x23, 0x36}

SIG_NAME = {
    0x01: 'CMD_REJECT', 0x02: 'CONN_REQ', 0x03: 'CONN_RSP', 0x04: 'CONF_REQ',
    0x05: 'CONF_RSP', 0x06: 'DISCONN_REQ', 0x07: 'DISCONN_RSP',
    0x0a: 'INFO_REQ', 0x0b: 'INFO_RSP',
}
PSM_NAME = {0x01: 'SDP', 0x11: 'HID_CTRL', 0x13: 'HID_INTR'}
FIXED_CID = {0x0004: 'ATT', 0x0005: 'LESIG', 0x0006: 'SMP', 0x0007: 'BRSMP'}
CONN_RESULT = {0: 'SUCCESS', 1: 'PENDING', 2: 'PSM_NOT_SUPP', 3: 'SEC_BLOCK',
               4: 'NO_RESOURCES', 6: 'INVALID_SCID', 7: 'SCID_IN_USE'}
CONF_RESULT = {0: 'SUCCESS', 1: 'UNACCEPT_PARAMS', 2: 'REJECTED', 3: 'UNKNOWN_OPTION'}
HS_RESULT = {0x0: 'SUCCESSFUL', 0x1: 'NOT_READY', 0x2: 'ERR_INVALID_REPORT_ID',
             0x3: 'ERR_UNSUPPORTED_REQUEST', 0x4: 'ERR_INVALID_PARAMETER',
             0xe: 'ERR_UNKNOWN', 0xf: 'ERR_FATAL'}
HIDP_TYPE = {0x0: 'HANDSHAKE', 0x1: 'HID_CONTROL', 0x4: 'GET_REPORT', 0x5: 'SET_REPORT',
             0x6: 'GET_PROTOCOL', 0x7: 'SET_PROTOCOL', 0xa: 'DATA', 0xb: 'DATC'}


def txt(b):
    return ''.join(chr(c) if 32 <= c < 127 else '.' for c in b)


def bdaddr(b):
    return ':'.join(f'{x:02X}' for x in reversed(b[:6]))


def frames(data):
    """Yield (opcode, ts_us, payload). Resyncs over garbage."""
    i = 0
    while i + 11 <= len(data):
        dlen, opcode, flags, hlen, htype = struct.unpack_from('<HHBBB', data, i)
        ts, = struct.unpack_from('<I', data, i + 7)
        plen = dlen - 9
        if (flags == 0 and hlen == 5 and htype == 8 and opcode in NAMES
                and 0 <= plen <= 2048 and i + 11 + plen <= len(data)):
            yield opcode, ts * 100, data[i + 11:i + 11 + plen]
            i += 11 + plen
        else:
            i += 1


def dis_evt(p):
    code, plen = p[0], p[1]
    body = p[2:2 + plen]
    out = [EVT_NAME.get(code, f'0x{code:02X}')]
    if code in EVT_STATUS and body:
        out.append('OK' if body[0] == 0 else f'** status=0x{body[0]:02X} **')
    if code == 0x03 and len(body) >= 9:                      # CONN_COMPLETE
        out.append(f'handle=0x{struct.unpack_from("<H", body, 1)[0]:04X} {bdaddr(body[3:9])}')
    elif code == 0x05 and len(body) >= 4:                    # DISCONN_COMPLETE
        out.append(f'handle=0x{struct.unpack_from("<H", body, 1)[0]:04X} reason=0x{body[3]:02X}')
    elif code == 0x07 and len(body) >= 7:                    # REMOTE_NAME_REQ_COMPLETE
        out.append(f'{bdaddr(body[1:7])} name="{txt(body[7:]).rstrip(chr(46))}"')
    elif code == 0x08 and len(body) >= 4:                    # ENCRYPT_CHANGE
        out.append(f'enabled={body[3]}')
    elif code in (0x04, 0x16, 0x17, 0x18, 0x31, 0x33) and len(body) >= 6:
        out.append(bdaddr(body[0:6]))
    elif code == 0x0f and len(body) >= 4:                    # CMD_STATUS
        op = struct.unpack_from('<H', body, 2)[0]
        out.append(f'opcode=0x{op:04X} '
                   + ('OK' if body[0] == 0 else f'** status=0x{body[0]:02X} **'))
    elif code == 0x0e and len(body) >= 4:                    # CMD_COMPLETE
        op = struct.unpack_from('<H', body, 1)[0]
        out.append(f'opcode=0x{op:04X}'
                   + ('' if body[3] == 0 else f' ** status=0x{body[3]:02X} **'))
    elif code == 0x12 and len(body) >= 8:                    # ROLE_CHANGE
        role = 'slave' if body[7] else 'master'
        out.append(f'{bdaddr(body[1:7])} we_are={role}')
    return ' '.join(out)


def dis_sig(payload, psm_by_cid):
    out = []
    i = 0
    while i + 4 <= len(payload):
        code, ident, clen = struct.unpack_from('<BBH', payload, i)
        b = payload[i + 4:i + 4 + clen]
        i += 4 + clen
        name = SIG_NAME.get(code, f'sig 0x{code:02X}')
        if code == 0x02 and len(b) >= 4:                     # CONN_REQ
            psm, scid = struct.unpack('<HH', b[:4])
            psm_by_cid[scid] = PSM_NAME.get(psm, f'psm{psm:#x}')
            name += f' psm={PSM_NAME.get(psm, hex(psm))} scid=0x{scid:04X}'
        elif code == 0x03 and len(b) >= 8:                   # CONN_RSP
            dcid, scid, res, st = struct.unpack('<HHHH', b[:8])
            psm_by_cid[dcid] = psm_by_cid.get(scid, '?')
            name += (f' dcid=0x{dcid:04X} scid=0x{scid:04X} '
                     f'{CONN_RESULT.get(res, hex(res))}')
            if st:
                name += f' status=0x{st:04X}'
            if res or st:
                name = '** ' + name + ' **'
        elif code == 0x04 and len(b) >= 4:                   # CONF_REQ
            dcid = struct.unpack('<H', b[:2])[0]
            name += f' dcid=0x{dcid:04X}({psm_by_cid.get(dcid, "?")}) opts={b[4:].hex()}'
        elif code == 0x05 and len(b) >= 6:                   # CONF_RSP
            scid, _fl, res = struct.unpack('<HHH', b[:6])
            name += f' scid=0x{scid:04X}({psm_by_cid.get(scid, "?")}) {CONF_RESULT.get(res, hex(res))}'
            if res:
                name = '** ' + name + ' **'
        elif code in (0x06, 0x07) and len(b) >= 4:           # DISCONN_REQ / RSP
            dcid, scid = struct.unpack('<HH', b[:4])
            name += f' dcid=0x{dcid:04X}({psm_by_cid.get(dcid, "?")}) scid=0x{scid:04X}'
        elif code == 0x01:
            name = '** CMD_REJECT ' + b.hex() + ' **'
        out.append(name)
    return '; '.join(out)


def dis_hidp(payload, chan):
    if not payload:
        return f'{chan} <empty>'
    hdr = payload[0]
    ttype = HIDP_TYPE.get(hdr >> 4, f'type{hdr >> 4:#x}')
    if hdr >> 4 == 0x0:
        return f'** {chan} HANDSHAKE {HS_RESULT.get(hdr & 0xF, hex(hdr & 0xF))} **'
    # low nibble is the report type: 1=input (dev->host), 2=output, 3=feature
    dirn = {0x1: 'IN', 0x2: 'OUT', 0x3: 'FEATURE'}.get(hdr & 0xF, f'p{hdr & 0xF:#x}')
    if len(payload) < 2:
        return f'{chan} {ttype}/{dirn}'
    body = payload[2:]
    head = f'{chan} {ttype}/{dirn} report=0x{payload[1]:02X} len={len(payload)}'
    if not body:
        return head
    return head + '  ' + body[:24].hex() + ('...' if len(body) > 24 else '')


def decode(path):
    """Trace file to (linhas, resumo). What the window shows, no printing."""
    data = open(path, 'rb').read()
    psm_by_cid = {}
    for dev in range(4):
        psm_by_cid.update({dev | 0x60: 'SDP_RX', dev | 0x70: 'SDP_TX',
                           dev | 0x80: 'HID_CTRL', dev | 0x90: 'HID_INTR'})
    reasm, out, counts = {}, [], {}

    for opcode, ts, payload in frames(data):
        counts[opcode] = counts.get(opcode, 0) + 1
        t = f'[{ts / 1e6:9.3f}]'
        if opcode == SYS_NOTE:
            out.append((ts, 'NOTE', txt(payload).rstrip()))
        elif opcode == EVT and payload:
            if payload[0] == 0x13:
                continue
            out.append((ts, 'EVT', dis_evt(payload)))
        elif opcode == CMD and len(payload) >= 3:
            op, plen = struct.unpack_from('<HB', payload, 0)
            out.append((ts, 'CMD', f'opcode=0x{op:04X} ' + payload[3:3 + plen].hex()))
        elif opcode in (ACL_TX, ACL_RX) and len(payload) >= 4:
            hf = struct.unpack_from('<H', payload, 0)[0]
            handle, pb = hf & 0x0FFF, (hf >> 12) & 0x3
            arrow = '-->' if opcode == ACL_TX else '<--'
            key = (handle, opcode)
            buf = payload[4:] if pb != 1 else reasm.get(key, b'') + payload[4:]
            if len(buf) < 4:
                reasm[key] = buf
                continue
            l2len, cid = struct.unpack_from('<HH', buf, 0)
            if len(buf) - 4 < l2len:
                reasm[key] = buf
                continue
            reasm[key] = b''
            body = buf[4:4 + l2len]
            if cid == 0x0001:
                out.append((ts, f'L2CAP{arrow}', dis_sig(body, psm_by_cid)))
            elif cid in FIXED_CID:
                out.append((ts, f'{FIXED_CID[cid]}{arrow}', f'op=0x{body[0]:02X} len={len(body)}'))
            else:
                chan = psm_by_cid.get(cid, f'cid=0x{cid:04X}')
                if chan.startswith('SDP'):
                    out.append((ts, f'SDP{arrow}', f'pdu=0x{body[0]:02X} len={len(body)}'))
                else:
                    out.append((ts, f'HIDP{arrow}', dis_hidp(body, chan)))

    total = sum(counts.values())
    resumo = (f'{len(data)} bytes, {total} quadros: '
              + ', '.join(f'{NAMES[k]}={v}' for k, v in sorted(counts.items()))) if total else              'TRACE VAZIO: o modo debug nao estava ativo, ou o adaptador perdeu energia depois.'
    return out, resumo


def main():
    p = ArgumentParser()
    p.add_argument('file', nargs='?')
    p.add_argument('-w', '--write', help='also save a .btsnoop for Wireshark')
    p.add_argument('-a', '--all', action='store_true', help='keep NUM_COMPLETED_PACKETS noise')
    p.add_argument('--selftest', action='store_true')
    args = p.parse_args()

    if args.selftest:
        blob = b''.join(struct.pack('<HHBBBI', len(pl) + 9, op, 0, 5, 8, 1) + pl
                        for op, pl in ((EVT, b'\x06\x03\x00\x01\x00'), (SYS_NOTE, b'hi')))
        got = [(o, pl) for o, _, pl in frames(b'\xa5' * 7 + blob)]
        assert got == [(EVT, b'\x06\x03\x00\x01\x00'), (SYS_NOTE, b'hi')], got
        assert 'AUTH_COMPLETE OK' in dis_evt(b'\x06\x03\x00\x01\x00')
        assert 'ERR_UNSUPPORTED_REQUEST' in dis_hidp(b'\x03', 'ctrl')
        assert 'DATA/OUT report=0x11' in dis_hidp(b'\xa2\x11\xc0\x07', 'intr')
        cids = {}
        dis_sig(bytes.fromhex('0201040011008000'), cids)
        assert cids == {0x0080: 'HID_CTRL'}, cids
        print('selftest ok')
        return 0

    data = open(args.file, 'rb').read()
    psm_by_cid = {}
    for dev in range(4):                       # BlueRetro's own fixed source CIDs
        psm_by_cid.update({dev | 0x60: 'SDP_RX', dev | 0x70: 'SDP_TX',
                           dev | 0x80: 'HID_CTRL', dev | 0x90: 'HID_INTR'})
    reasm = {}
    hci, counts = [], {}

    for opcode, ts, payload in frames(data):
        counts[opcode] = counts.get(opcode, 0) + 1
        t = f'[{ts / 1e6:9.3f}]'
        if opcode == SYS_NOTE:
            print(f'{t} NOTE   {txt(payload).rstrip()}')
            continue
        hci.append((ts, opcode, payload))
        if opcode == EVT and payload:
            if payload[0] == 0x13 and not args.all:
                continue
            print(f'{t} EVT    {dis_evt(payload)}')
        elif opcode == CMD and len(payload) >= 3:
            op, plen = struct.unpack_from('<HB', payload, 0)
            print(f'{t} CMD    opcode=0x{op:04X} (ogf={op >> 10} ocf=0x{op & 0x3FF:03X}) '
                  + payload[3:3 + plen].hex())
        elif opcode in (ACL_TX, ACL_RX) and len(payload) >= 4:
            hf = struct.unpack_from('<H', payload, 0)[0]
            handle, pb = hf & 0x0FFF, (hf >> 12) & 0x3
            arrow = '-->' if opcode == ACL_TX else '<--'
            key = (handle, opcode)
            buf = payload[4:] if pb != 1 else reasm.get(key, b'') + payload[4:]
            if len(buf) < 4:
                reasm[key] = buf
                continue
            l2len, cid = struct.unpack_from('<HH', buf, 0)
            if len(buf) - 4 < l2len:
                reasm[key] = buf
                continue
            reasm[key] = b''
            body = buf[4:4 + l2len]
            if cid == 0x0001:
                print(f'{t} L2CAP{arrow} {dis_sig(body, psm_by_cid)}')
            elif cid in FIXED_CID:                 # BLE, not HID: don't dissect as HIDP
                print(f'{t} {FIXED_CID[cid]:<5}{arrow} op=0x{body[0]:02X} len={len(body)}  '
                      + body[1:20].hex())
            else:
                chan = psm_by_cid.get(cid, f'cid=0x{cid:04X}')
                if chan.startswith('SDP'):
                    print(f'{t} SDP  {arrow} pdu=0x{body[0]:02X} len={len(body)}')
                else:
                    print(f'{t} HIDP {arrow} {dis_hidp(body, chan)}')

    total = sum(counts.values())
    print(f'\n{len(data)} bytes, {total} frames: '
          + ', '.join(f'{NAMES[k]}={v}' for k, v in sorted(counts.items())), file=sys.stderr)
    if total == 0:
        print('EMPTY TRACE: debug mode was not active, or the adapter lost power '
              'afterwards (the buffer is RAM).', file=sys.stderr)
        return 1
    if args.write:
        with open(args.write, 'wb') as f:
            f.write(b'btsnoop\x00' + struct.pack('>II', 1, 1001))
            for ts, opcode, pl in hci:
                f.write(struct.pack('>IIIIq', len(pl), len(pl), FLAGS[opcode], 0, EPOCH + ts) + pl)
        print(f'wrote {args.write} ({len(hci)} HCI frames)', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
