
from collections.abc import Callable


class DataAcquisition:
    """Collect application data from a configured data source."""

    def __init__(self, source: Callable[[], bytes]) -> None:
        if not callable(source):
            raise TypeError("source must be callable.")

        self.source = source

    def collect(self) -> bytes:
        """Read one application-data sample."""

        payload = self.source()

        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("The acquired payload must be bytes-like.")

        return bytes(payload)
