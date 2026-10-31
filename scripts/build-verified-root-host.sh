#!/usr/bin/env bash
# Build the verified-root host provider (real RSA-3072 + per-block provider) and its
# owner key pair.  The provider is the same C code the kit ships for host tests; the
# key pair is a fresh test anchor, so the manifest it signs is reproducible only with
# the exported keys (both are uploaded as artifacts).
set -euo pipefail
TREE=${1:?usage: build-verified-root-host.sh TREE}
OPENSSL_PREFIX=$(brew --prefix openssl@3 2>/dev/null || true)
if [ -z "$OPENSSL_PREFIX" ] || [ ! -d "$OPENSSL_PREFIX/include/openssl" ]; then
  echo "openssl@3 is required: brew install openssl@3" >&2
  exit 1
fi
OUT="$TREE/build/verified-root-host"
mkdir -p "$OUT/keys"
if [ ! -f "$OUT/keys/owner.pem" ]; then
  openssl genrsa -out "$OUT/keys/owner.pem" 3072
  openssl rsa -in "$OUT/keys/owner.pem" -pubout -out "$OUT/keys/owner-public.pem"
fi
openssl rsa -pubin -in "$OUT/keys/owner-public.pem" -noout -text | head -2
clang -dynamiclib -O2 -std=c11 -Wall -Wextra -Werror \
  -I "$TREE/kits/VerifiedRoot/include" \
  -I "$OPENSSL_PREFIX/include" \
  -L "$OPENSSL_PREFIX/lib" -Wl,-rpath,"$OPENSSL_PREFIX/lib" \
  -lcrypto \
  -o "$OUT/verified-root.dylib" \
  "$TREE/kits/VerifiedRoot/tests/openssl_provider.c"
ls -l "$OUT/verified-root.dylib"
python3 - "$OUT/verified-root.dylib" <<'PY'
import ctypes, sys
lib = ctypes.CDLL(sys.argv[1])
for symbol in ('vr_host_open', 'vr_host_block'):
    getattr(lib, symbol)
print('PASS: host provider exports vr_host_open/vr_host_block')
PY
