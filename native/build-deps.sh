#!/bin/sh
# Cross-builds the static libraries the native player links: libogg, libopus,
# and opusfile, 32-bit Windows, from the xiph release tarballs. Everything
# lands under native/deps/ (gitignored): sources in deps/src, the install in
# deps/prefix. Run by `make deps`; `make native` needs the result.
#
#   HOST=i686-w64-mingw32 ./native/build-deps.sh
set -eu

cd "$(dirname "$0")"
HOST=${HOST:-i686-w64-mingw32}
SRC=$PWD/deps/src
PREFIX=$PWD/deps/prefix
MIRROR=https://downloads.xiph.org/releases
mkdir -p "$SRC" "$PREFIX"

# name  archive  sha256 (from downloads.xiph.org/releases/*/SHA256SUMS.txt;
# libogg has no sums file there, its hash was checked against Homebrew's formula)
DEPS='
libogg-1.3.5   ogg/libogg-1.3.5.tar.xz   c4d91be36fc8e54deae7575241e03f4211eb102afb3fc0775fbbc1b740016705
opus-1.5.2     opus/opus-1.5.2.tar.gz    65c1d2f78b9f2fb20082c38cbe47c951ad5839345876e46941612ee87f9a7ce1
opusfile-0.12  opus/opusfile-0.12.tar.gz 118d8601c12dd6a44f52423e68ca9083cc9f2bfe72da7a8c1acb22a80ae3550b
'

fetch() { # name path sha256
    archive=$SRC/$(basename "$2")
    if [ ! -f "$archive" ]; then
        echo "fetching $2"
        curl -fsSL -o "$archive.tmp" "$MIRROR/$2" && mv -f "$archive.tmp" "$archive"
    fi
    echo "$3  $archive" | sha256sum -c --quiet
    if [ ! -d "$SRC/$1" ]; then
        tar -C "$SRC" -xf "$archive"
    fi
}

build() { # name [configure options]
    name=$1; shift
    echo "building $name"
    (
        cd "$SRC/$name"
        CC=$HOST-gcc CFLAGS="-O2 -msse2 -mfpmath=sse" \
        PKG_CONFIG_LIBDIR="$PREFIX/lib/pkgconfig" \
        ./configure --quiet --host="$HOST" --prefix="$PREFIX" --disable-shared --enable-static "$@" \
            > configure.log 2>&1 || { tail -20 configure.log >&2; exit 1; }
        make -j"$(nproc)" > make.log 2>&1 || { tail -20 make.log >&2; exit 1; }
        make install > install.log 2>&1
    )
}

echo "$DEPS" | while read -r name path sum; do
    if [ -n "$name" ]; then fetch "$name" "$path" "$sum"; fi
done

build libogg-1.3.5
build opus-1.5.2 --disable-extra-programs --disable-doc
build opusfile-0.12 --disable-http --disable-doc --disable-examples

echo "installed under $PREFIX:"
ls "$PREFIX/lib/"*.a
