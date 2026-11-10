"""Golden wire vectors are shared between Python and the portable Lua codec."""
import json
from pathlib import Path
import unittest

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

from shiteng_rs485 import Fan, Mode, encode_control, patch_control

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(LuaRuntime, 'install the test extra to run Lua compatibility checks')
class LuaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.codec = self.lua.execute((ROOT / 'lua/shiteng.lua').read_text(encoding='utf-8'))

    def table(self, data):
        return self.lua.table_from(data)

    def wire(self, table):
        return bytes(table[index] for index in range(1, len(table) + 1))

    def test_all_shared_golden_frames_encode_and_decode(self):
        fixtures = json.loads((ROOT / 'tests/fixtures/golden_frames.json').read_text(encoding='utf-8'))
        for vector in fixtures['frames']:
            with self.subTest(vector=vector['id']):
                payload = bytes.fromhex(vector['payload'])
                raw = bytes.fromhex(vector['raw'])
                kind = int(vector['kind'], 16)
                self.assertEqual(self.wire(self.codec.encode(kind, self.table(payload))), raw)
                for split in range(len(raw) + 1):
                    decoder = self.codec.decoder_new()
                    records = list(decoder.feed(decoder, self.table(raw[:split])).values())
                    records += list(decoder.feed(decoder, self.table(raw[split:])).values())
                    self.assertEqual(len(records), 1)
                    self.assertEqual(self.wire(records[0]['raw']), raw)

    def test_control_api_preserves_unknown_bits_like_python(self):
        payload = bytes.fromhex('bd9b1357')
        changes = {'power':False, 'auto':True, 'fan':3, 'mode':0}
        actual = self.wire(self.codec.patch_control(self.table(payload), self.table(changes)))
        self.assertEqual(actual, patch_control(payload, **changes))
        state = {'power':True, 'auto':False, 'fan':2, 'mode':1}
        lua_payload = self.codec.make_control(self.table(state))
        self.assertEqual(self.wire(self.codec.encode(0x81, lua_payload)),
                         encode_control(power=True, auto=False, fan=Fan.MID, mode=Mode.INNER_VENT))


if __name__ == '__main__': unittest.main()
