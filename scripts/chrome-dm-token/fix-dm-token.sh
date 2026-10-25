#!/bin/bash
# Restore an existing local CBCM registration; never enroll a new device.
# Bash 3.2 compatible (including the Bash shipped with macOS). No Python needed.
set +xv

fail() { printf 'Error: %s\n' "$1" >&2; exit 1; }

# Hex strings keep NUL bytes intact without storing binary data in Bash variables.
# All parser results are returned in variables, never on stdout.
read_varint() {
    local byte shift
    VARINT=0
    for ((shift=0; shift<63; shift+=7)); do
        ((OFFSET + 2 <= ${#PROTO})) || fail 'Truncated protobuf varint.'
        byte=$((16#${PROTO:OFFSET:2})); OFFSET=$((OFFSET + 2))
        VARINT=$((VARINT | ((byte & 127) << shift)))
        ((byte & 128)) || return 0
    done
    fail 'Protobuf varint overflow.'
}

proto_field() {
    local PROTO=$1 OFFSET=0 wanted=$2 expected=$3 optional=${4:-no}
    local number wire size value found=0
    FIELD=
    while ((OFFSET < ${#PROTO})); do
        read_varint
        number=$((VARINT >> 3)); wire=$((VARINT & 7))
        ((number > 0 && number <= 536870911)) || fail 'Invalid protobuf field.'
        case $wire in
            0) read_varint; value=$VARINT ;;
            1|2|5)
                case $wire in
                    1) size=8 ;;
                    5) size=4 ;;
                    2) read_varint; size=$VARINT ;;
                esac
                ((size <= (${#PROTO} - OFFSET) / 2)) || fail 'Truncated protobuf field.'
                value=${PROTO:OFFSET:size*2}; OFFSET=$((OFFSET + size*2)) ;;
            *) fail 'Unsupported protobuf wire type.' ;;
        esac
        if ((number == wanted)); then
            ((found == 0 && wire == expected)) || fail 'Invalid or duplicate policy field.'
            found=1; FIELD=$value
        fi
    done
    if ((found == 0)); then
        [[ $optional == yes ]] || fail 'Missing policy field.'
        FIELD=0
    fi
}

decode_ascii() {
    local hex=$1 i byte char
    ASCII=
    for ((i=0; i<${#hex}; i+=2)); do
        byte=$((16#${hex:i:2}))
        ((byte >= 33 && byte <= 126)) || fail 'Invalid ASCII policy field.'
        printf -v char '%b' "\\x${hex:i:2}"
        ASCII=$ASCII$char
    done
}

hash_filename() {
    # Chromium uses SHA-1 for compatibility, then base64url without padding.
    printf '%s' "$1" | openssl dgst -sha1 -binary | openssl base64 -A | tr '+/' '-_' | tr -d '='
}

load_cached_token() {
    local cache=$1 device=$2 data policy stamp now hex
    [[ -f $cache && -r $cache ]] || fail 'Policy cache is missing or unreadable. Refresh Chrome policies first.'
    # Bound the read itself, including when the file grows while being read.
    data=$(head -c 16777217 "$cache" | od -An -v -tx1 | tr -d ' \n')
    ((${#data} <= 33554432)) || fail 'Policy cache is too large.'
    proto_field "$data" 3 2; policy=$FIELD
    proto_field "$policy" 1 2; decode_ascii "$FIELD"
    [[ $ASCII == google/chrome/machine-level-user ]] || fail 'Unexpected policy type.'
    proto_field "$policy" 8 2; decode_ascii "$FIELD"
    [[ -n $device && $ASCII == "$device" ]] || fail 'Cached registration does not belong to this machine.'
    proto_field "$policy" 9 0 yes
    [[ $FIELD == 0 ]] || fail 'Cached registration is not active.'
    proto_field "$policy" 2 0; stamp=$FIELD
    now=$(date +%s)
    ((stamp > 0 && stamp / 1000 >= now - 86400 && stamp / 1000 <= now + 300)) ||
        fail 'Policy cache is too old or in the future. Refresh Chrome policies and try again.'
    proto_field "$policy" 3 2; hex=$FIELD
    ((${#hex} > 0 && ${#hex} <= 8192)) || fail 'Invalid cached token length.'
    decode_ascii "$hex"; TOKEN=$ASCII
    [[ $TOKEN != INVALID_DM_TOKEN ]] || fail 'Cached token is invalidated.'
}

persist_token() {
    local target=$1 parent
    # Preserve any existing object, even an empty file or a dangling symlink.
    if [[ -e $target || -L $target ]]; then
        printf '%s\n' 'DM token already exists. Nothing was changed.'
        return
    fi
    parent=${target%/*}
    (umask 077; mkdir -p "$parent")
    # noclobber uses exclusive creation and also protects a value created since
    # the check. A single builtin printf writes the bytes without a newline.
    if ! (umask 077; set -o noclobber; printf '%s' "$TOKEN" > "$target") 2>/dev/null; then
        fail 'Could not create DM token (permission error, I/O error or destination now exists).'
    fi
    cmp -s "$target" <(printf '%s' "$TOKEN") || fail 'DM token write verification failed.'
    printf '%s\n' 'Success: DM token persisted and verified. Restart Chrome normally.'
}

main() {
    local platform user_data='' device target command_name machine serial
    if [[ ${1:-} == --help ]]; then
        printf '%s\n' 'Usage: bash fix-dm-token.sh [--user-data-dir PATH]' \
            'Run as the Chrome user, without sudo. Close Chrome before applying.'
        return
    fi
    if (($#)); then
        [[ $# == 2 && $1 == --user-data-dir && -n $2 ]] || fail 'Usage: bash fix-dm-token.sh [--user-data-dir PATH]'
        user_data=$2
    fi
    for command_name in uname openssl tr head od date mkdir cmp cat; do
        command -v "$command_name" >/dev/null || fail "Required command is missing: $command_name"
    done
    platform=$(uname -s)
    case $platform in
        Linux)
            if [[ -z $user_data ]]; then
                # Match Chrome's default config root and environment overrides.
                user_data=${CHROME_USER_DATA_DIR:-${CHROME_CONFIG_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}}/google-chrome}
            fi
            [[ -r /etc/machine-id ]] || fail 'Cannot read /etc/machine-id.'
            machine=$(cat /etc/machine-id)
            # Chromium removes trailing ASCII whitespace only.
            while [[ $machine == *[$' \t\r\n\v\f'] ]]; do machine=${machine%?}; done
            ((${#machine} == 32)) || fail 'Invalid /etc/machine-id length.'
            device=$(hash_filename "$machine")
            target=$user_data/Policy/Enrollment/$device ;;
        Darwin)
            command -v ioreg >/dev/null || fail 'Required command is missing: ioreg'
            command -v sed >/dev/null || fail 'Required command is missing: sed'
            user_data=${user_data:-$HOME/Library/Application Support/Google/Chrome}
            serial=$(ioreg -rd1 -c IOPlatformExpertDevice)
            device=$(printf '%s\n' "$serial" | sed -n 's/.*"IOPlatformSerialNumber" = "\([^"]*\)".*/\1/p')
            [[ -n $device && $device != *$'\n'* ]] || fail 'Cannot determine the platform serial number.'
            target=$HOME/Library/Application\ Support/Google/Chrome\ Cloud\ Enrollment/$(hash_filename "$device") ;;
        *) fail 'This script supports macOS and Linux only.' ;;
    esac
    if [[ -e $target || -L $target ]]; then
        printf '%s\n' 'DM token already exists. Nothing was changed.'
        return
    fi
    printf '%s\n' 'Reading and validating the local policy cache...'
    load_cached_token "$user_data/Policy/Machine Level User Cloud Policy" "$device"
    persist_token "$target"
}

# Tests can source parser and writer functions without running a repair.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
    set -euo pipefail
    export LC_ALL=C
    main "$@"
fi
