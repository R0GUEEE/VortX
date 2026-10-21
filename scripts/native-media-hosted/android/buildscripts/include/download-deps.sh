#!/bin/bash -e

. ./include/depinfo.sh

[ -z "$WGET" ] && WGET=wget

mkdir -p deps && cd deps

# mbedtls
[ ! -d mbedtls ] && git clone --depth 1 --branch v$v_mbedtls --recurse-submodules https://github.com/Mbed-TLS/mbedtls.git mbedtls
[ "$(git -C mbedtls rev-parse HEAD)" = "$v_mbedtls_sha" ] || {
    echo "mbedTLS source does not match the reviewed maintenance pin; refusing reuse" >&2
    exit 1
}

# dav1d
[ ! -d dav1d ] && git clone --depth 1 --branch $v_dav1d https://code.videolan.org/videolan/dav1d.git dav1d

# ffmpeg
[ ! -d ffmpeg ] && git clone --depth 1 --branch n$v_ffmpeg https://github.com/FFmpeg/FFmpeg.git ffmpeg
[ "$(git -C ffmpeg rev-parse HEAD)" = "$v_ffmpeg_sha" ] || {
    echo "FFmpeg source does not match the reviewed maintenance pin; refusing reuse" >&2
    exit 1
}

# freetype2
[ ! -d freetype ] && git clone --depth 1 --branch VER-$v_freetype https://gitlab.freedesktop.org/freetype/freetype.git freetype

# fribidi
[ ! -d fribidi ] && git clone --depth 1 --branch v$v_fribidi https://github.com/fribidi/fribidi.git fribidi

# harfbuzz
[ ! -d harfbuzz ] && git clone --depth 1 --branch $v_harfbuzz https://github.com/harfbuzz/harfbuzz.git harfbuzz

# libunibreak
if [ ! -d libunibreak ]; then
	mkdir libunibreak
	$WGET https://github.com/adah1972/libunibreak/releases/download/libunibreak_${v_libunibreak}/libunibreak-${v_libunibreak//_/.}.tar.gz -O - | \
		tar -xz -C libunibreak --strip-components=1
fi

# libxml2
[ ! -d libxml2 ] && git clone --depth 1 --branch v$v_libxml2 https://gitlab.gnome.org/GNOME/libxml2.git libxml2

# fontconfig
[ ! -d fontconfig ] && git clone --depth 1 --branch $v_fontconfig https://gitlab.freedesktop.org/fontconfig/fontconfig.git fontconfig

# libass
[ ! -d libass ] && git clone --depth 1 --branch $v_libass https://github.com/libass/libass.git libass

# lua
# Never silently reuse an old unverified source directory or pipe a response to tar.
bash -e ../include/download-lua.sh "$v_lua" "$v_lua_sha256"

[ ! -d libplacebo ] && git clone --depth 1 --branch v$v_libplacebo --recurse-submodules https://code.videolan.org/videolan/libplacebo.git libplacebo

# mpv
[ ! -d mpv ] && git clone --depth 1 --branch v$v_mpv https://github.com/mpv-player/mpv.git mpv

cd ..
