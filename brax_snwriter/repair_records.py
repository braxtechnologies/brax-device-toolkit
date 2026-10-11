"""Read and verify the supported phone's factory serial record."""
import hashlib
from .serial_record import BASEOS_FINGERPRINT, PRODUCT_LID, validate_serial
from .session import MetaSession, open_serial


def probe_identity(port, expected):
    validate_serial(expected)
    with open_serial(port) as stream:
        session = MetaSession(stream)
        fingerprint = session.property('ro.build.fingerprint')
        serial = session.property('ro.vendor.serialno')
        if fingerprint != BASEOS_FINGERPRINT or serial != expected:
            raise ValueError('META identity/firmware does not match the selected device')
        data = session.read_record(PRODUCT_LID)
        if len(data) != 1024:
            raise ValueError('unexpected PRODUCT_INFO size')
        return {'serial': serial, 'fingerprint': fingerprint,
                'serial_field': data[116:136].split(b'\0', 1)[0].decode('ascii'),
                'product_info_sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
