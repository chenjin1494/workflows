# sheng workflows

macOS-only build steps of the UniXNU `sheng` (SM8550) port. Linux cannot produce them:
`build-owner-os-policy.py` needs `hdiutil`, `diskutil`, `/dev/rdiskN` on a real read-only
UDIF image, and the probe is cross-built with the iOS SDK.

What runs here:

| workflow | produces |
|---|---|
| `probe-runner.yml` | runner identification (macOS version, Xcode, disk, IPSW reachability) |
| `verified-root.yml` | `build/verified-root-os/`, `build/verified-root-systemos/`, `build/verified-root-os-xnu-bundle/libVerifiedRootProbe.a`, host owner keys |

Inputs are public: `github.com/UniXNU/UniXNU`, `github.com/UniXNU/UniXNU-sheng`, and the
public iPad7,1 17.7.11 (21H461) IPSW. Image members are pinned by sha256:

* `044-76040-038.dmg` — `3f99b859c9728379110b66657c7b15285bcb1925ad8b5726542e2cdcf8b1028d` (OS, 1483785 blocks)
* `044-76034-038.dmg` — `b4908cde41d23e6bc05e6af51a2c9eeb00cf0f863a6a18b1600c96a3fd70414f` (SystemOS, 806400 blocks)

`scripts/owner-policy.py` mirrors `UniXNU/scripts/build-owner-os-policy.py` and only
parameterises the image, its digest and its volume UUID; all original checks stay in place
(UDIF trailer, compressed digest, read-only Disk Image bus protocol, raw character device,
RSA-3072 anchor, every 4 KiB block verified through the compiled C provider).

`scripts/build-verified-root-host.sh` compiles `kits/VerifiedRoot/tests/openssl_provider.c`
into `build/verified-root-host/verified-root.dylib` and generates the RSA-3072 owner key
pair. Both are uploaded — the manifest is only reproducible with those keys.
