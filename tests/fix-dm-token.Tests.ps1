$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot '../scripts/chrome-dm-token/fix-dm-token.ps1')

function Assert([bool]$Condition,[string]$Message) { if (-not $Condition) { throw $Message } }
function Assert-Throws([scriptblock]$Action,[string]$Message) {
    $thrown = $false
    try { & $Action | Out-Null } catch { $thrown = $true }
    Assert $thrown $Message
}
# Synthetic protobuf: type(string), timestamp(varint), token(bytes).
# Exercises alternating scalar and byte fields without touching real policy.
$data = [byte[]]@(10,1,120,16,150,1,26,2,97,98)
$fields = Read-Proto $data
Assert ([Text.Encoding]::ASCII.GetString((Get-Field $fields 1 2)) -eq 'x') 'Type field changed.'
Assert ((Get-Field $fields 2 0) -eq 150) 'Varint scalar changed.'
Assert ([Text.Encoding]::ASCII.GetString((Get-Field $fields 3 2)) -eq 'ab') 'Token bytes changed.'
Assert ((Get-Field $fields 9 0 -Optional) -eq 0) 'Missing optional state did not default to active.'
Assert-Throws { Get-Field $fields 2 2 } 'Wrong wire type was accepted.'
Assert-Throws { Read-Proto ([byte[]]@(26,128)) } 'Truncated length was accepted.'
Assert-Throws { Read-Proto ([byte[]]@(26,4,97)) } 'Truncated payload was accepted.'
Assert-Throws { Read-Proto ([byte[]]@(0)) } 'Zero field number was accepted.'
$duplicate = Read-Proto ([byte[]]@(26,1,97,26,1,98))
Assert-Throws { Get-Field $duplicate 3 2 } 'Duplicate token field was accepted.'
$offset = 0
Assert-Throws { Read-Varint ([byte[]]@(255,255,255,255,255,255,255,255,255,1)) ([ref]$offset) } 'Oversized varint was accepted.'
$empty = Read-Proto ([byte[]]@(26,0))
Assert ((Get-Field $empty 3 2).Length -eq 0) 'Empty bytes field changed.'
Write-Output 'All isolated parser checks passed. No registry or policy data was modified.'
