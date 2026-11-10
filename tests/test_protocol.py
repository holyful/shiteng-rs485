"""Pure protocol tests using de-identified recorded frames; no hardware access."""

import dataclasses
import json
from pathlib import Path
import random
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
try:
    import shiteng_rs485 as protocol
except ImportError:
    protocol = None

FIXTURES = json.loads((Path(__file__).parent / 'fixtures/golden_frames.json').read_text(encoding='utf-8'))
GOLDENS = {item['id']: item for item in FIXTURES['frames']}


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(protocol is not None and hasattr(protocol, 'Decoder'), 'pure protocol core is missing')

    def golden(self, name):
        return bytes.fromhex(GOLDENS[name]['raw'])

    def test_crc_known_check_vector_and_recorded_high_byte_first_order(self):
        self.assertEqual(protocol.crc16(b'123456789'), 0x4b37)
        self.assertEqual(protocol.crc16(b''), 0xffff)
        raw = self.golden('on_manual_inner_mid')
        self.assertEqual(protocol.crc16(raw[:-4]), 0x42e9)
        self.assertEqual(raw[-4:-2], b'\x42\xe9')

    def test_all_recorded_frames_roundtrip_without_assigned_semantics(self):
        for item in FIXTURES['frames']:
            with self.subTest(name=item['id']):
                raw = bytes.fromhex(item['raw'])
                payload = bytes.fromhex(item['payload'])
                kind = int(item['kind'], 16)
                self.assertEqual(protocol.encode_frame(kind, payload), raw)
                decoded, = protocol.Decoder().feed(raw)
                self.assertEqual((decoded.kind, decoded.payload, decoded.raw), (kind, payload, raw))
                self.assertEqual(int.from_bytes(raw[2:4], 'big'), len(raw) - 2)

    def test_every_split_point_and_bytewise_delivery(self):
        raw = b''.join(bytes.fromhex(item['raw']) for item in FIXTURES['frames'])
        for cut in range(len(raw) + 1):
            decoder = protocol.Decoder()
            output = decoder.feed(raw[:cut]) + decoder.feed(raw[cut:])
            self.assertEqual(b''.join(frame.raw for frame in output), raw)
            self.assertEqual(decoder.buffered_bytes, 0)
        decoder = protocol.Decoder()
        output = []
        for byte in raw:
            output.extend(decoder.feed(bytes([byte])))
        self.assertEqual(len(output), len(FIXTURES['frames']))

    def test_arbitrary_chunks_embedded_delimiters_and_unknown_kind_are_preserved(self):
        payload = b'\x7e\x0d\x0a\x7e\xa5\xd0' + bytes(range(256))
        raw = protocol.encode_frame(0xf1, payload)
        rng = random.Random(42)
        decoder, frames, pos = protocol.Decoder(), [], 0
        while pos < len(raw):
            width = rng.randrange(1, 18)
            frames.extend(decoder.feed(raw[pos:pos + width]))
            pos += width
        self.assertEqual([(x.kind, x.payload, x.raw) for x in frames], [(0xf1, payload, raw)])

    def test_bad_crc_tail_and_swapped_crc_are_rejected_then_resynchronized(self):
        valid = self.golden('periodic_poll')
        raw = self.golden('on_manual_inner_mid')
        corruptions = [raw[:-4] + bytes([raw[-4] ^ 1]) + raw[-3:],
                       raw[:-2] + b'\n\r', raw[:-4] + raw[-4:-2][::-1] + raw[-2:]]
        for damaged in corruptions:
            decoder = protocol.Decoder()
            frames = decoder.feed(damaged + valid)
            self.assertEqual([frame.raw for frame in frames], [valid])

    def test_invalid_lengths_noise_and_maximal_frame_keep_buffer_bounded(self):
        valid = self.golden('periodic_poll')
        for bad in (b'\x7e\x01\x00\x05', b'\x7e\x01\x02\x00', b'\x7e\x01\xff\xff'):
            decoder = protocol.Decoder()
            self.assertEqual([frame.raw for frame in decoder.feed(b'z' * 20000 + bad + valid)], [valid])
        maximum = protocol.encode_frame(0xee, bytes(504))
        self.assertEqual(len(maximum), 512)
        decoder = protocol.Decoder()
        self.assertEqual(decoder.feed(maximum[:-1]), [])
        self.assertEqual(decoder.buffered_bytes, 511)
        self.assertEqual(decoder.feed(maximum[-1:])[0].raw, maximum)
        with self.assertRaises(ValueError):
            protocol.encode_frame(0xee, bytes(505))

    def test_partial_candidates_wait_for_explicit_reset_not_embedded_marker(self):
        decoder = protocol.Decoder()
        prefix = b'\x7e\x99\x01\xfe'
        nested = self.golden('periodic_poll')
        self.assertEqual(decoder.feed(prefix + nested), [])
        self.assertEqual(decoder.buffered_bytes, len(prefix + nested))
        decoder.reset()
        self.assertEqual(decoder.buffered_bytes, 0)
        self.assertEqual(decoder.feed(nested)[0].raw, nested)

    def test_empty_payload_and_empty_feed_are_supported(self):
        decoder = protocol.Decoder()
        self.assertEqual(decoder.feed(b''), [])
        raw = protocol.encode_frame(0, b'')
        self.assertEqual(len(raw), 8)
        self.assertEqual(decoder.feed(raw)[0].payload, b'')

    def test_byteslike_inputs_are_copied_to_immutable_frames(self):
        raw = bytearray(self.golden('periodic_poll'))
        decoded, = protocol.Decoder().feed(memoryview(raw))
        raw[:] = bytes(len(raw))
        self.assertEqual(decoded.raw, self.golden('periodic_poll'))
        self.assertIsInstance(decoded.payload, bytes)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decoded.kind = 0

    def test_invalid_inputs_fail_before_mutating_decoder_carry(self):
        decoder = protocol.Decoder()
        raw = self.golden('periodic_poll')
        decoder.feed(raw[:3])
        for value in (None, '7e', 4, True, [126, 32]):
            with self.subTest(value=value), self.assertRaises(TypeError):
                decoder.feed(value)
            self.assertEqual(decoder.buffered_bytes, 3)
            with self.assertRaises(TypeError):
                protocol.crc16(value)
            with self.assertRaises(TypeError):
                protocol.encode_frame(1, value)
        self.assertEqual(decoder.feed(raw[3:])[0].raw, raw)
        for value in (True, False, None, '1', 1.5):
            with self.assertRaises(TypeError):
                protocol.encode_frame(value, b'')
        for value in (-1, 256):
            with self.assertRaises(ValueError):
                protocol.encode_frame(value, b'')

    def test_make_control_and_encode_control_match_observed_settled_cases(self):
        cases = (
            ('off_auto_inner_low', False, True, 1, 1),
            ('on_auto_inner_low', True, True, 1, 1),
            ('on_manual_inner_low', True, False, 1, 1),
            ('on_manual_inner_mid', True, False, 2, 1),
            ('on_manual_inner_high', True, False, 3, 1),
            ('inner_dry_settled', True, False, 1, 2),
            ('fresh_dry_settled', True, False, 2, 3),
            ('fresh_settled', True, False, 2, 0),
        )
        for name, power, auto, fan, mode in cases:
            kwargs = dict(power=power, auto=auto, fan=fan, mode=mode)
            self.assertEqual(protocol.make_control(**kwargs), bytes.fromhex(GOLDENS[name]['payload']))
            self.assertEqual(protocol.encode_control(**kwargs), self.golden(name))

    def test_enums_have_only_observed_fan_and_mode_values(self):
        self.assertEqual([(x.name, x.value) for x in protocol.Fan], [('LOW', 1), ('MID', 2), ('HIGH', 3)])
        self.assertEqual([(x.name, x.value) for x in protocol.Mode], [
            ('FRESH', 0), ('INNER_VENT', 1), ('INNER_DEHUMIDIFY', 2), ('FRESH_DEHUMIDIFY', 3)])
        self.assertEqual(protocol.make_control(power=True, auto=False, fan=protocol.Fan.MID,
                                              mode=protocol.Mode.INNER_VENT), bytes.fromhex('81401400'))

    def test_patch_preserves_opaque_bits_and_tail_and_input(self):
        original = bytes.fromhex('3c9bdead')
        changed = protocol.patch_control(original, power=True, auto=True, fan=3, mode=3)
        self.assertEqual(changed, bytes.fromhex('ffffdead'))
        self.assertEqual(changed[0] & 0x3c, original[0] & 0x3c)
        self.assertEqual(changed[1] & 0x9b, original[1] & 0x9b)
        self.assertEqual(protocol.patch_control(changed, mode=1), bytes.fromhex('fdfbdead'))
        self.assertEqual(protocol.patch_control(original), original)
        self.assertEqual(original, bytes.fromhex('3c9bdead'))

    def test_patch_each_field_changes_only_its_observed_mask(self):
        base = bytes.fromhex('41201400')
        for change, expected in (({'power': True}, 'c1201400'), ({'auto': False}, '01201400'),
                                 ({'fan': 3}, '41601400'), ({'mode': 0}, '40241400')):
            self.assertEqual(protocol.patch_control(base, **change), bytes.fromhex(expected))

    def test_commands_reject_bool_as_numeric_and_unverified_fan_stop(self):
        good = dict(power=True, auto=False, fan=2, mode=1)
        for field, invalids in (('power', (0, 1, None, 'on')), ('auto', (0, 1, None)),
                                ('fan', (True, False, 0, 4, 1.5, '2', None)),
                                ('mode', (True, False, -1, 4, 1.5, '1', None))):
            for invalid in invalids:
                kwargs = {**good, field: invalid}
                with self.subTest(field=field, value=invalid), self.assertRaises((TypeError, ValueError)):
                    protocol.make_control(**kwargs)
                if invalid is not None:
                    with self.assertRaises((TypeError, ValueError)):
                        protocol.patch_control(b'\x00\x00\x14\x00', **{field: invalid})
        for payload in (b'', bytes(3), bytes(5)):
            with self.assertRaises(ValueError):
                protocol.patch_control(payload, power=True)
        with self.assertRaises(TypeError):
            protocol.make_control(power=True)
        with self.assertRaises(TypeError):
            protocol.patch_control(bytes(4), unsupported=1)

    def test_humidity_goldens_policy_bounds_and_heartbeat(self):
        self.assertEqual(protocol.encode_humidity(55), self.golden('humidity_55'))
        self.assertEqual(protocol.encode_humidity(60), self.golden('humidity_60'))
        for value in (30, 90):
            result, = protocol.Decoder().feed(protocol.encode_humidity(value))
            self.assertEqual((result.kind, result.payload), (0xc4, bytes([0, value])))
        for value in (29, 91, -1):
            with self.assertRaises(ValueError):
                protocol.encode_humidity(value)
        for value in (True, False, 55.0, '55', None):
            with self.assertRaises(TypeError):
                protocol.encode_humidity(value)
        self.assertEqual(protocol.heartbeat(), self.golden('periodic_poll'))

    def test_experimental_decode_is_explicit_and_provisional_including_zero(self):
        for name, expected in (('provisional_a2', ('indoor_temperature', 22, '°C')),
                               ('provisional_a8', ('indoor_humidity', 35, '%')),
                               ('provisional_ac_zero', ('pm25', 0, 'µg/m³'))):
            decoded, = protocol.Decoder().feed(self.golden(name))
            self.assertFalse(hasattr(decoded, 'temperature'))
            result = protocol.decode_experimental(decoded)
            self.assertEqual((result['name'], result['value'], result['unit']), expected)
            self.assertIs(result['provisional'], True)
            self.assertEqual(result['source_type'], decoded.kind)
            json.dumps(result, allow_nan=False)

    def test_experimental_signed_temperature_and_range_boundaries(self):
        for kind, values in ((0xa2, (-40, -1, 0, 85)), (0xa8, (0, 100)), (0xac, (0, 999))):
            for value in values:
                raw = protocol.encode_frame(kind, (value % 65536).to_bytes(2, 'big'))
                decoded, = protocol.Decoder().feed(raw)
                self.assertEqual(protocol.decode_experimental(decoded)['value'], value)

    def test_experimental_invalid_and_unmapped_fields_remain_unknown(self):
        for kind, payload in ((0xa2, b'\xff\xd7'), (0xa2, b'\x00\x56'),
                              (0xa8, b'\x00\x65'), (0xac, b'\x03\xe8'),
                              (0xa2, b''), (0xa8, b'\x23'), (0xac, b'\x00\x00\x00')):
            decoded, = protocol.Decoder().feed(protocol.encode_frame(kind, payload))
            self.assertIsNone(protocol.decode_experimental(decoded))

    def test_experimental_never_trusts_manually_constructed_unvalidated_frame(self):
        raw = self.golden('provisional_a2')
        for value in (protocol.Frame(0xa2, b'\x00\x16', raw[:-4] + b'\x00\x00\r\n'),
                      protocol.Frame(0xa2, b'\x00\x17', raw),
                      protocol.Frame(0xa8, b'\x00\x16', raw)):
            self.assertIsNone(protocol.decode_experimental(value))
        with self.assertRaises(TypeError):
            protocol.decode_experimental(raw)
        for name in ('unmapped_a0', 'unmapped_aa', 'unmapped_ab', 'unmapped_b6',
                     'reply_manual_inner_mid', 'on_manual_inner_mid'):
            decoded, = protocol.Decoder().feed(self.golden(name))
            self.assertIsNone(protocol.decode_experimental(decoded))


if __name__ == '__main__':
    unittest.main()
