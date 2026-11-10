"""Bounded passive serial/TCP listener; never transmits polling or controls."""
import argparse
import math
import time
from shiteng_rs485 import Decoder
from shiteng_rs485.transports import SerialTransport, TCPTransport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--serial', metavar='PORT', help='e.g. COM3 or /dev/ttyUSB0')
    group.add_argument('--tcp', nargs=2, metavar=('HOST', 'PORT'), help='transparent serial gateway only')
    parser.add_argument('--seconds', type=float, default=30, help='listen duration, 1..3600 (default 30)')
    args = parser.parse_args()
    if not math.isfinite(args.seconds) or not 1 <= args.seconds <= 3600:
        parser.error('--seconds must be finite and between 1 and 3600')
    try:
        transport = SerialTransport(args.serial, timeout=0.25) if args.serial else TCPTransport(args.tcp[0], int(args.tcp[1]), timeout=0.25)
    except ValueError as exc:
        parser.error(str(exc))
    decoder = Decoder()
    last_data = None
    with transport:
        end = time.monotonic() + args.seconds
        while time.monotonic() < end:
            chunk = transport.read()
            now = time.monotonic()
            # Explicit listener policy, not a claimed manufacturer timing limit.
            if last_data is not None and now - last_data > 2:
                decoder.reset()
                last_data = None
            if chunk:
                last_data = now
                for frame in decoder.feed(chunk):
                    print(f'type=0x{frame.kind:02X} payload={frame.payload.hex(" ")} raw={frame.raw.hex(" ")}')


if __name__ == '__main__': main()
