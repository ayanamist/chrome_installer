#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$UserDataDir = (Join-Path $env:LOCALAPPDATA 'Google\Chrome\User Data')
)
$ErrorActionPreference = 'Stop'

# Chrome checks the neutral Google location first (32-bit view), then
# its own enrollment location (64-bit view). Preserve ANY existing value,
# including an empty or differently typed value; never print its contents.
$TokenKey = 'SOFTWARE\Google\Chrome\Enrollment'
function Test-TokenExists([string]$Path, [Microsoft.Win32.RegistryView]$View) {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine',$View)
    $key = $null
    try {
        $key = $base.OpenSubKey($Path)
        return ($null -ne $key -and $key.GetValueNames() -contains 'dmtoken')
    }
    finally { if ($key) { $key.Dispose() }; $base.Dispose() }
}

# Policy caches use protobuf. Read wire fields directly so this script
# needs neither Python, Go, a protobuf package nor a compiled helper.
function Read-Varint([byte[]]$Data, [ref]$Offset) {
    [long]$value = 0
    for ($shift = 0; $shift -lt 63; $shift += 7) {
        if ($Offset.Value -ge $Data.Length) { throw 'Truncated protobuf varint.' }
        $b = $Data[$Offset.Value]; $Offset.Value++
        $value = $value -bor ([long]($b -band 127) -shl $shift)
        if (($b -band 128) -eq 0) { return $value }
    }
    throw 'Protobuf varint overflow.'
}
function Read-Proto([byte[]]$Data) {
    $fields = @{}; $offset = 0
    while ($offset -lt $Data.Length) {
        $tag = Read-Varint $Data ([ref]$offset)
        $number = [int]($tag -shr 3); $wire = [int]($tag -band 7)
        if ($number -le 0 -or $number -gt 536870911) { throw 'Invalid protobuf field.' }
        $value = $null
        if ($wire -eq 0) { $value = Read-Varint $Data ([ref]$offset) }
        else {
            switch ($wire) {
                1 { $size = 8 }
                5 { $size = 4 }
                2 { $size = Read-Varint $Data ([ref]$offset) }
                default { throw 'Unsupported protobuf wire type.' }
            }
            if ($size -gt ($Data.Length - $offset)) { throw 'Truncated protobuf field.' }
            $value = [byte[]]@()
            if ($size -gt 0) { $value = $Data[$offset..($offset + $size - 1)] }
            $offset += [int]$size
        }
        if (-not $fields.ContainsKey($number)) { $fields[$number] = New-Object System.Collections.ArrayList }
        [void]$fields[$number].Add(@{ Wire = $wire; Value = $value })
    }
    return $fields
}
function Get-Field($Fields, [int]$Number, [int]$Wire, [switch]$Optional) {
    if (-not $Fields.ContainsKey($Number)) {
        if ($Optional) { return 0 }
        throw "Missing policy field $Number."
    }
    $items = $Fields[$Number]
    if ($items.Count -ne 1 -or $items[0].Wire -ne $Wire) { throw "Invalid or duplicate policy field $Number." }
    if ($Wire -eq 2) { return ,([byte[]]$items[0].Value) }
    return $items[0].Value
}

# PolicyFetchResponse field 3 contains PolicyData. Its fields are:
# 1 = policy type, 2 = timestamp, 3 = DM token, 8 = device ID, 9 = state.
function Read-CachedToken([string]$Root) {
    $path = Join-Path $Root 'Policy\Machine Level User Cloud Policy'
    Write-Output "Reading policy cache: $path" | Out-Host
    if ((Get-Item -LiteralPath $path).Length -gt 16777216) { throw 'Policy cache is too large.' }
    $response = Read-Proto ([IO.File]::ReadAllBytes($path))
    $policy = Read-Proto (Get-Field $response 3 2)
    if ([Text.Encoding]::UTF8.GetString((Get-Field $policy 1 2)) -ne 'google/chrome/machine-level-user') {
        throw 'Unexpected policy type.'
    }

    # Do not persist a registration copied from another machine.
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine','Registry64')
    $key = $null
    try {
        $key = $base.OpenSubKey('SOFTWARE\Microsoft\Cryptography')
        $machine = $key.GetValue('MachineGuid')
    }
    finally { if ($key) { $key.Dispose() }; $base.Dispose() }
    if (-not $machine -or [Text.Encoding]::UTF8.GetString((Get-Field $policy 8 2)) -cne $machine) {
        throw 'Cached registration does not belong to this machine.'
    }
    if ((Get-Field $policy 9 0 -Optional) -ne 0) { throw 'Cached registration is not active.' }
    $stamp = [DateTimeOffset]::FromUnixTimeMilliseconds((Get-Field $policy 2 0))
    $age = [DateTimeOffset]::UtcNow - $stamp
    if ($age.TotalHours -gt 24 -or $age.TotalMinutes -lt -5) {
        throw 'Policy cache is too old or in the future. Refresh Chrome policies and try again.'
    }

    [byte[]]$token = Get-Field $policy 3 2
    if ($token.Length -eq 0 -or $token.Length -gt 4096) { throw 'Invalid cached token length.' }
    foreach ($b in $token) { if ($b -lt 33 -or $b -gt 126) { throw 'Invalid cached token format.' } }
    if ([Text.Encoding]::ASCII.GetString($token) -eq 'INVALID_DM_TOKEN') { throw 'Cached token is invalidated.' }
    Write-Output 'Cache validated: matching device, active registration and recent policy.' | Out-Host
    return ,$token
}

# Allow parser tests to load functions without applying a repair.
if ($MyInvocation.InvocationName -eq '.') { return }

try {
    Write-Output '[1/4] Checking for an existing DM token...'
    if ((Test-TokenExists 'SOFTWARE\Google\Enrollment' Registry32) -or
        (Test-TokenExists $TokenKey Registry64)) {
        Write-Output 'DM token already exists. Nothing was changed.'
        exit 0
    }

    Write-Output '[2/4] Checking administrator privileges...'
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        $principal = New-Object Security.Principal.WindowsPrincipal($identity)
        if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
            throw 'Run this script from an administrator PowerShell window.'
        }
    }
    finally { $identity.Dispose() }

    Write-Output '[3/4] Reading and validating the cached registration...'
    [byte[]]$token = Read-CachedToken $UserDataDir

    Write-Output '[4/4] Persisting the DM token in the 64-bit Chrome enrollment key...'
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine','Registry64')
    $key = $null
    try {
        # Recheck after cache parsing, including through the writable handle,
        # so a value created meanwhile is left untouched.
        if (Test-TokenExists 'SOFTWARE\Google\Enrollment' Registry32) {
            Write-Output 'A DM token now exists in the Google enrollment key. Nothing was changed.'
            exit 0
        }
        $key = $base.CreateSubKey($TokenKey)
        if ($key.GetValueNames() -contains 'dmtoken') {
            Write-Output 'A DM token now exists in the Chrome enrollment key. Its value was not changed.'
            exit 0
        }
        $key.SetValue('dmtoken',$token,[Microsoft.Win32.RegistryValueKind]::Binary)
        $key.Flush()

        # Verify the write without displaying or saving the token.
        $saved = $key.GetValue('dmtoken')
        if ($key.GetValueKind('dmtoken') -ne [Microsoft.Win32.RegistryValueKind]::Binary -or
            [Convert]::ToBase64String($saved) -cne [Convert]::ToBase64String($token)) {
            throw 'DM token write verification failed.'
        }
    }
    finally { if ($key) { $key.Dispose() }; $base.Dispose() }
    Write-Output 'Success: DM token persisted and verified. Restart Chrome normally.'
}
catch { Write-Error -Message $_.Exception.Message -ErrorAction Continue; exit 1 }
