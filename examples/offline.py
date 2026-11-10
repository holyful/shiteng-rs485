"""Encode/decode demonstration. No I/O or appliance writes."""
from shiteng_rs485 import Decoder, Fan, Mode, encode_control


def main():
    wire = encode_control(power=True, auto=False, fan=Fan.MID, mode=Mode.INNER_VENT)
    print('Offline example only:', wire.hex(' '))
    decoder = Decoder()
    assert not decoder.feed(wire[:5])
    frame = decoder.feed(wire[5:])[0]
    print(f'type=0x{frame.kind:02X} payload={frame.payload.hex(" ")}')


if __name__ == '__main__': main()
