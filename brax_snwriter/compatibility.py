"""Fail-closed model/release policy. No operator override or chip-only approval."""
from dataclasses import dataclass


class CompatibilityError(ValueError):
    pass


@dataclass(frozen=True)
class DeviceProfile:
    model: str
    board_revision: str
    chip: str
    storage: str
    storage_bytes: int
    layout_id: str
    bootloader_version: int
    rollback_index: int


@dataclass(frozen=True)
class ReleaseProfile:
    model: str
    board_revisions: tuple
    chip: str
    storage: str
    storage_bytes: tuple
    source_layouts: tuple
    minimum_bootloader: int
    rollback_index: int
    allowed_modes: tuple


def compare(device, release, mode):
    """Compare already-verified facts; this function does not authenticate them."""
    if not isinstance(device, DeviceProfile) or not isinstance(release, ReleaseProfile):
        raise CompatibilityError('Verified phone and release profiles are required')
    if not all((device.model, device.board_revision, device.chip, device.storage, device.layout_id)):
        raise CompatibilityError('Phone identity is incomplete')
    if device.model != release.model:
        raise CompatibilityError('Firmware is for a different phone model')
    if device.board_revision not in release.board_revisions or device.chip != release.chip:
        raise CompatibilityError('Hardware revision is not approved for this release')
    if device.storage != release.storage or device.storage_bytes not in release.storage_bytes:
        raise CompatibilityError('Storage type or capacity is incompatible')
    if device.layout_id not in release.source_layouts:
        raise CompatibilityError('This partition layout has no approved migration path')
    for value in (device.bootloader_version, device.rollback_index, release.minimum_bootloader, release.rollback_index):
        if type(value) is not int or value < 0:
            raise CompatibilityError('Bootloader or rollback version could not be verified')
    if device.bootloader_version < release.minimum_bootloader:
        raise CompatibilityError('Bootloader is too old for this release')
    if release.rollback_index < device.rollback_index:
        raise CompatibilityError('Firmware downgrade is blocked by the rollback policy')
    if mode not in release.allowed_modes:
        raise CompatibilityError('This operation is not approved for this phone and release')
    return {'compatible': True, 'model': device.model, 'board_revision': device.board_revision}


def require_verified_compatibility(connection, manifest, mode):
    """Production entry point: never accept caller-supplied identity/trust flags.

    The current boot probe supplies a chip code and a COM location only. A trusted
    live device reader and a signed, hash-bound release catalog are not implemented.
    Keep every operation blocked until those providers can compare authenticated
    profiles and bind the check to the same session used for writing. Passing an
    ADB model string, filename, chip code or `verified=True` is not sufficient.
    """
    raise CompatibilityError(
        'Flashing blocked: exact model/revision, storage layout and approved release '
        'cannot yet be verified for the connected phone. The current check identifies '
        'only the chip. No flash or format command was started.')
