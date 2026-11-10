"""Optional synchronous byte transports; never generate or replay commands.

Use a transparent TCP-to-serial gateway, not the Dacai A5 diagnostic service.
These instances are single-owner, not thread-safe. A failed write may already
have transmitted some bytes; the connection is closed and never auto-retried.
"""
from __future__ import annotations

import math
import socket
from typing import Protocol


class ByteTransport(Protocol):
    def read(self, max_bytes: int = 256) -> bytes: ...
    def write(self, data: bytes) -> None: ...
    def close(self) -> None: ...


def _timeout(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('timeout must be a positive finite number of seconds')
    return float(value)


def _read_size(value: int) -> int:
    if type(value) is not int or not 1 <= value <= 65536:
        raise ValueError('max_bytes must be an integer in 1..65536')
    return value


def _data(value: bytes) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise TypeError('data must be bytes, bytearray or memoryview')
    return bytes(value)


class _Transport:
    _connection = None

    @property
    def is_open(self) -> bool:
        return self._connection is not None

    def _require_open(self):
        if self._connection is None:
            raise RuntimeError('transport is closed; call open() explicitly')
        return self._connection

    def close(self) -> None:
        connection, self._connection = self._connection, None
        if connection is not None:
            connection.close()

    def _close_after_error(self) -> None:
        try:
            self.close()
        except Exception:
            pass  # Preserve the original I/O exception.

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc, traceback):
        if exc_type is None:
            self.close()
        else:
            self._close_after_error()
        return False


class TCPTransport(_Transport):
    """Raw TCP bytes with bounded socket waits and explicit connection lifetime."""

    def __init__(self, host: str, port: int, *, timeout: float = 1.0):
        if not isinstance(host, str) or not host.strip():
            raise ValueError('host must be a nonempty string')
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError('port must be an integer in 1..65535')
        self.host, self.port, self.timeout = host, port, _timeout(timeout)
        self._connection = None

    def open(self) -> TCPTransport:
        if self.is_open:
            raise RuntimeError('transport is already open')
        self._connection = socket.create_connection((self.host, self.port), timeout=self.timeout)
        return self

    def read(self, max_bytes: int = 256) -> bytes:
        connection = self._require_open()
        size = _read_size(max_bytes)
        try:
            result = connection.recv(size)
            if not result:
                raise EOFError('TCP peer closed; reset the protocol decoder before another session')
            return result
        except socket.timeout:
            return b''
        except Exception:
            self._close_after_error()
            raise

    def write(self, data: bytes) -> None:
        connection = self._require_open()
        wire = _data(data)
        if not wire:
            return
        try:
            connection.sendall(wire)
        except Exception:
            self._close_after_error()
            raise


class SerialTransport(_Transport):
    """Optional pySerial adapter; 9600/8N1 defaults match the observed panel.

    Requires an appropriate RS485 transceiver/USB adapter. Direction control is
    the adapter's responsibility. No RTS-based direction policy is inferred.
    Opening a port can still toggle modem lines in a platform-specific driver.
    """

    def __init__(self, port: str, *, baudrate: int = 9600, timeout: float = 1.0):
        if not isinstance(port, str) or not port.strip():
            raise ValueError('port must be a nonempty serial port name')
        if type(baudrate) is not int or baudrate <= 0:
            raise ValueError('baudrate must be a positive integer')
        self.port, self.baudrate, self.timeout = port, baudrate, _timeout(timeout)
        self._connection = None

    def open(self) -> SerialTransport:
        if self.is_open:
            raise RuntimeError('transport is already open')
        try:
            import serial
        except ImportError as exc:
            raise RuntimeError('Install the serial extra (shiteng-rs485[serial]); from a checkout: pip install ".[serial]"') from exc
        connection = serial.Serial(port=None)
        self._connection = connection
        try:
            connection.port = self.port
            connection.baudrate = self.baudrate
            connection.bytesize = 8
            connection.parity = 'N'
            connection.stopbits = 1
            connection.timeout = self.timeout
            connection.write_timeout = self.timeout
            connection.xonxoff = connection.rtscts = connection.dsrdtr = False
            connection.rts = connection.dtr = False
            connection.open()
        except Exception:
            self._close_after_error()
            raise
        return self

    def read(self, max_bytes: int = 256) -> bytes:
        connection = self._require_open()
        size = _read_size(max_bytes)
        try:
            return bytes(connection.read(size))
        except Exception:
            self._close_after_error()
            raise

    def write(self, data: bytes) -> None:
        connection = self._require_open()
        wire = _data(data)
        if not wire:
            return
        try:
            written = connection.write(wire)
            if written != len(wire):
                raise IOError('partial serial write; outcome unknown, command not retried')
        except Exception:
            self._close_after_error()
            raise
