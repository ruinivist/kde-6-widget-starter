#!/usr/bin/env bash
set -euo pipefail

archive_path=${1:?usage: $0 ARCHIVE [SMOKE_FILE]}
smoke_file=${2:-$(dirname "${BASH_SOURCE[0]}")/cppbridge_smoke.qml}
extract_dir=$(mktemp -d)
trap 'rm -rf "$extract_dir"' EXIT

unzip -q "$archive_path" -d "$extract_dir"
bridge_dir="$extract_dir/contents/ui/cppbridge"
qmldir="$bridge_dir/qmldir"

test -f "$qmldir"
link_target=$(awk '$1 == "linktarget" { print $2; exit }' "$qmldir")
plugin_target=$(awk '$1 == "optional" && $2 == "plugin" { print $3; exit }' "$qmldir")
typeinfo=$(awk '$1 == "typeinfo" { print $2; exit }' "$qmldir")

test -n "$link_target"
test -n "$plugin_target"
test -n "$typeinfo"
test -f "$bridge_dir/$typeinfo"
test -f "$bridge_dir/lib$link_target.so"
test -f "$bridge_dir/lib$plugin_target.so"

native_libraries=("$bridge_dir"/*.so)
for library in "${native_libraries[@]}"; do
    if readelf -d "$library" | grep -Eq 'Library (rpath|runpath): \[[[:space:]]*/'; then
        echo "absolute RPATH/RUNPATH in $library" >&2
        exit 1
    fi
    if ldd "$library" | grep -q 'not found'; then
        echo "unresolved native dependency in $library" >&2
        exit 1
    fi
done

qml_runner=""
for candidate in qml6 qml /usr/lib/qt6/bin/qml; do
    if command -v "$candidate" >/dev/null 2>&1; then
        qml_runner=$(command -v "$candidate")
        break
    fi
done
if [ -z "$qml_runner" ]; then
    echo "Qt QML runner not found" >&2
    exit 1
fi

"$qml_runner" -platform offscreen -I "$extract_dir/contents/ui" "$smoke_file"
