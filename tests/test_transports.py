"""Transport checks use mocks and loopback only; never a physical appliance."""
import socket
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

from shiteng_rs485.transports import SerialTransport, TCPTransport


class TransportTests(unittest.TestCase):
    def test_tcp_open_is_passive_and_close_is_idempotent(self):
        sock = MagicMock()
        with patch('socket.create_connection', return_value=sock) as create:
            transport = TCPTransport('localhost', 12345)
            create.assert_not_called()
            with transport:
                create.assert_called_once_with(('localhost', 12345), timeout=1.0)
                sock.sendall.assert_not_called()
                transport.write(b'abc')
                sock.sendall.assert_called_once_with(b'abc')
            transport.close()
            sock.close.assert_called_once()

    def test_tcp_timeout_is_empty_but_eof_closes_connection(self):
        sock = MagicMock()
        sock.recv.side_effect = [socket.timeout(), b'']
        with patch('socket.create_connection', return_value=sock):
            transport = TCPTransport('localhost', 12345).open()
            self.assertEqual(transport.read(), b'')
            self.assertTrue(transport.is_open)
            with self.assertRaises(EOFError):
                transport.read()
            self.assertFalse(transport.is_open)

    def test_partial_send_failure_does_not_retry_or_reconnect(self):
        sock = MagicMock()
        sock.sendall.side_effect = TimeoutError('partial write')
        sock.close.side_effect = OSError('close failed')
        with patch('socket.create_connection', return_value=sock) as create:
            transport = TCPTransport('localhost', 12345).open()
            with self.assertRaisesRegex(TimeoutError, 'partial write'):
                transport.write(b'abc')
            with self.assertRaises(RuntimeError):
                transport.write(b'abc')
            create.assert_called_once()
            sock.sendall.assert_called_once()

    def test_serial_optional_dependency_and_explicit_open(self):
        module = MagicMock()
        port = module.Serial.return_value
        port.write.return_value = 2
        with patch.dict(sys.modules, {'serial': module}):
            transport = SerialTransport('TEST_PORT')
            module.Serial.assert_not_called()
            with transport:
                port.open.assert_called_once()
                port.write.assert_not_called()
                self.assertEqual(port.port, 'TEST_PORT')
                self.assertEqual(port.baudrate, 9600)
                self.assertEqual(port.timeout, 1.0)
                self.assertEqual(port.bytesize, 8)
                self.assertEqual(port.parity, 'N')
                self.assertEqual(port.stopbits, 1)
                self.assertFalse(port.rts)
                self.assertFalse(port.dtr)
                transport.write(b'ab')
            port.close.assert_called_once()

    def test_serial_short_write_is_error_and_closes_port(self):
        module = MagicMock()
        port = module.Serial.return_value
        port.write.return_value = 1
        with patch.dict(sys.modules, {'serial': module}):
            transport = SerialTransport('TEST_PORT').open()
            with self.assertRaisesRegex(IOError, 'partial'):
                transport.write(b'abc')
            self.assertFalse(transport.is_open)
            port.write.assert_called_once_with(b'abc')
            port.close.assert_called_once()

    def test_serial_missing_dependency_has_install_hint(self):
        with patch.dict(sys.modules, {'serial': None}):
            with self.assertRaisesRegex(RuntimeError, r'shiteng-rs485\[serial\]'):
                SerialTransport('TEST_PORT').open()

    def test_closed_transport_and_invalid_parameters(self):
        for transport in (TCPTransport('localhost', 1), SerialTransport('TEST_PORT')):
            with self.assertRaises(RuntimeError): transport.read()
            with self.assertRaises(RuntimeError): transport.write(b'a')
        for timeout in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError): TCPTransport('localhost', 1, timeout=timeout)
        for port in (0, 65536, True, '123'):
            with self.assertRaises(ValueError): TCPTransport('localhost', port)
        with self.assertRaises(ValueError): SerialTransport('')

    def test_read_size_and_write_type_are_checked_before_io(self):
        sock = MagicMock()
        with patch('socket.create_connection', return_value=sock):
            with TCPTransport('localhost', 1) as transport:
                for size in (0, -1, True, 1.5, 65537):
                    with self.assertRaises(ValueError): transport.read(size)
                for data in (4, True, 'abc', [1, 2]):
                    with self.assertRaises(TypeError): transport.write(data)
                sock.recv.assert_not_called()
                sock.sendall.assert_not_called()

    def test_real_loopback_preserves_arbitrary_bytes_without_extra_commands(self):
        listener = socket.socket()
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        listener.settimeout(3)
        received, errors = [], []
        wire = bytes.fromhex('7e20000700fa050d0a')
        def server():
            try:
                with listener.accept()[0] as peer:
                    peer.settimeout(3)
                    data = b''
                    while len(data) < len(wire):
                        chunk = peer.recv(256)
                        if not chunk: break
                        data += chunk
                    received.append(data)
                    peer.sendall(data)
            except Exception as exc: errors.append(exc)
            finally: listener.close()
        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        with TCPTransport('127.0.0.1', listener.getsockname()[1]) as transport:
            transport.write(wire)
            response = b''
            while len(response) < len(wire): response += transport.read()
        worker.join(timeout=4)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(received, [wire])
        self.assertEqual(response, wire)


if __name__ == '__main__': unittest.main()
