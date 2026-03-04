from __future__ import annotations

from typing import Any

from .core import CentralController
from .types import ZoneEvents


class SerialIngester:
    """
    The mechanism to ingest data via serial connections to the receivers

    Attributes
    ----------
    controller : CentralController
        The controller to which the serial ingester should pass information to.
    port : str
        The serial port in which to ingest data from.
    baudrate : int
        The baud rate of the port.
    _serial : Serial
        The Serial class instance with which to perform ingestion with.
    """
    def __init__(
        self,
        controller: CentralController,
        port: str,
        baudrate: int = 115200,
    ) -> None:
        """
        Creates a SerialIngester instance.
        """
        self.controller = controller
        self.port = port
        self.baudrate = baudrate
        self._serial: Any = None

    def open(self) -> None:
        """
        Opens the serial connection specified by the instance attributes
        """
        import serial
        self._serial = serial.Serial(self.port, self.baudrate, timeout=1)

    def close(self) -> None:
        """
        Closes the serial connection specified by the instance attributes
        """
        if self._serial is not None:
            self._serial.close()
            self._serial = None

    def read_once(self) -> ZoneEvents | None:
        """
        Reads one line from the Serial connection.
        """
        if self._serial is None:
            raise RuntimeError("Serial port not open — call open() first")
        line = (
            self._serial.readline().decode("utf-8", errors="replace").strip()
        )
        if not line:
            return []
        return self.controller.ingest(line)

    def run_forever(self) -> None:
        """
        Helper method to ensure the instance continuously reads from the connection
        """
        self.open()
        try:
            while True:
                self.read_once()
        except KeyboardInterrupt:
            pass
        finally:
            self.close()
