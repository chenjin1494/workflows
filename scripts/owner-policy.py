#!/usr/bin/env python3
"""Sign and fully verify one original IPSW image member without expanding the UDIF.

This mirrors UniXNU/scripts/build-owner-os-policy.py, with the image, its compressed
sha256 and its volume UUID taken as arguments so the same code signs the OS member
(044-76040-038.dmg) and the SystemOS member (044-76034-038.dmg) of the iPad7,1 IPSW.
Nothing here weakens the original checks: the UDIF trailer, the compressed digest,
the read-only Disk Image bus protocol, the raw character device, the RSA-3072 anchor
and every 4 KiB block are still verified against the compiled C provider.
"""
import argparse
import ctypes
import hashlib
import importlib.util
import json
import plistlib
import re
import stat
import subprocess
import uuid
from pathlib import Path

class View(ctypes.Structure):
    _fields_ = [('hashes', ctypes.c_void_p), ('image_bytes', ctypes.c_uint64), ('block_count', ctypes.c_uint64),
                ('generation', ctypes.c_uint64), ('volume_uuid', ctypes.c_uint8 * 16), ('image_sha256', ctypes.c_uint8 * 32)]

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--tree', type=Path, required=True)
parser.add_argument('--dmg', type=Path, required=True)
parser.add_argument('--mount', type=Path, required=True)
parser.add_argument('--out', type=Path, required=True)
parser.add_argument('--expect-sha256', required=True)
parser.add_argument('--expect-blocks', type=int, required=True)
parser.add_argument('--volume-uuid', type=uuid.UUID)
parser.add_argument('--require-udif', action='store_true',
                    help='the member is a UDIF image, so its koly trailer must close the file')
args = parser.parse_args()

source, volume, out = args.dmg.resolve(), args.mount.resolve(), args.out
if not source.is_file():
    raise SystemExit('Require the existing exact original input: ' + str(source))
digest = hashlib.sha256()
with source.open('rb') as stream:
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    if args.require_udif:
        stream.seek(-512, 2)
        if stream.read(4) != b'koly':
            raise SystemExit('Expected the actual original UDIF trailer')
if digest.hexdigest() != args.expect_sha256:
    raise SystemExit('The compressed image is not the exact supplied member: ' + digest.hexdigest())

images = plistlib.loads(subprocess.check_output(['hdiutil', 'info', '-plist']))['images']
found = [i for i in images if Path(i['image-path']).resolve() == source and i.get('writeable') is False]
if len(found) != 1:
    raise SystemExit('Require one existing exact read-only image attachment of ' + str(source))
entities = found[0]['system-entities']
whole = [e['dev-entry'] for e in entities if e.get('content-hint') == 'GUID_partition_scheme']
if not whole:
    # A raw image (no GPT) exposes one partition entity; the whole virtual media is
    # its parent disk, i.e. the entity node without the trailing 'sN'.
    whole = [re.sub(r's\d+$', '', entities[0]['dev-entry'])] if entities else []
if len(whole) != 1:
    raise SystemExit('Require one actual whole virtual media mapping')
info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', whole[0]]))
volume_info = plistlib.loads(subprocess.check_output(['diskutil', 'info', '-plist', str(volume)]))
if (info.get('BusProtocol') != 'Disk Image' or info.get('Writable') is not False or
        info.get('DeviceNode') != whole[0] or
        volume_info.get('Writable') is not False or
        volume_info.get('DeviceNode') not in [e['dev-entry'] for e in entities]):
    raise SystemExit('Only the actual matching read-only Disk Image volume is accepted')
mounted_uuid = uuid.UUID(volume_info['VolumeUUID'])
expected_uuid = args.volume_uuid or mounted_uuid
if mounted_uuid != expected_uuid:
    raise SystemExit('The mounted volume is not the signed identity: ' + volume_info['VolumeUUID'])

disk = Path('/dev/r' + Path(whole[0]).name)
if not stat.S_ISCHR(disk.stat().st_mode):
    raise SystemExit('Require the verified virtual-media raw character device')

spec = importlib.util.spec_from_file_location('verified_manifest', args.tree / 'kits/VerifiedRoot/tools/manifest.py')
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)
keys = args.tree / 'build/verified-root-host/keys'
with disk.open('rb', buffering=1024 * 1024) as stream:
    record = manifest.make(stream, info['TotalSize'], keys / 'owner.pem', keys / 'owner-public.pem',
                           expected_uuid, 1, out)

native = ctypes.CDLL(str(args.tree / 'build/verified-root-host/verified-root.dylib'))
native.vr_host_open.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_uint64, ctypes.POINTER(View)]
native.vr_host_block.argtypes = [ctypes.POINTER(View), ctypes.c_uint64, ctypes.c_void_p, ctypes.c_size_t]
encoded = ctypes.create_string_buffer((out / 'manifest.bin').read_bytes())
anchor = ctypes.create_string_buffer((out / 'trusted-key.bin').read_bytes())
view = View()
if native.vr_host_open(encoded, len(encoded) - 1, anchor, 1, ctypes.byref(view)) or bytes(view.volume_uuid) != expected_uuid.bytes:
    raise SystemExit('Real C signature/volume authentication failed')
if view.block_count != args.expect_blocks:
    raise SystemExit('Block count ' + str(view.block_count) + ' is not the expected ' + str(args.expect_blocks))

complete = hashlib.sha256()
indices = [0, 1, view.block_count // 2, view.block_count - 1]
samples, lengths = [], []
with disk.open('rb', buffering=1024 * 1024) as stream:
    for index in range(view.block_count):
        block = stream.read(min(4096, view.image_bytes - index * 4096))
        if native.vr_host_block(ctypes.byref(view), index, block, len(block)) != 0:
            raise SystemExit('Actual C block verification failed at ' + str(index))
        complete.update(block)
        if index in indices:
            samples.append(block.ljust(4096, b'\0'))
            lengths.append(len(block))
    if stream.read(1):
        raise SystemExit('Virtual media exceeded its authenticated exact bounds')
if complete.digest() != bytes(view.image_sha256) or complete.hexdigest() != record['image_sha256']:
    raise SystemExit('Actual complete original image digest differs')

(out / 'original-samples.bin').write_bytes(b''.join(samples))
report = dict(image_bytes=view.image_bytes, blocks_verified=view.block_count, image_sha256=complete.hexdigest(),
              volume_uuid=str(expected_uuid).upper(), generation=view.generation,
              source_compressed_sha256=digest.hexdigest(), source_format='UDIF',
              host_bus_protocol='Disk Image', host_media_writable=False,
              trusted_key_sha256=record['trusted_key_sha256'], actual_sample_indices=indices,
              actual_sample_lengths=lengths, real_c_signature_and_block_verification=True,
              original_image_modified=False, host_only=True, boot_root_authenticated=False,
              qemu_tested=False, hardware_tested=False)
(out / 'verification.json').write_text(json.dumps(report, indent=2) + '\n')
print('PASS: original image signature and every', view.block_count, 'block verified by real C/RSA; no expanded image')
print('image_sha256', complete.hexdigest())
