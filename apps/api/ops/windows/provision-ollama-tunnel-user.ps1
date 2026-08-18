param(
    [string]$UserName = "ollama_tunnel",
    [string]$PublicKeyPath = "C:\sites\ashiraai\app\ops\windows\ollama_tunnel.pub",
    [string]$SshdConfigPath = "C:\ProgramData\ssh\sshd_config"
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path -LiteralPath $PublicKeyPath)) {
    throw "Tunnel public key was not found"
}

$user = Get-LocalUser -Name $UserName -ErrorAction SilentlyContinue
if ($null -eq $user) {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $password = ConvertTo-SecureString ([Convert]::ToBase64String($bytes)) -AsPlainText -Force
    New-LocalUser -Name $UserName -Password $password -AccountNeverExpires `
        -PasswordNeverExpires -UserMayNotChangePassword `
        -Description "Ashiraai Ollama reverse tunnel" | Out-Null
}

$sshDirectory = "C:\Users\$UserName\.ssh"
$authorizedKeys = Join-Path $sshDirectory "authorized_keys"
New-Item -ItemType Directory -Path $sshDirectory -Force | Out-Null
$publicKey = (Get-Content -LiteralPath $PublicKeyPath -Raw).Trim()
# `-N` requests no session channel, so the forced command never runs for the
# tunnel. Any attempted shell/session is restricted to internal-sftp.
$authorizedLine = 'restrict,port-forwarding,command="internal-sftp" ' + $publicKey
[IO.File]::WriteAllText($authorizedKeys, $authorizedLine + "`n", [Text.Encoding]::ASCII)

& icacls $sshDirectory /inheritance:r /grant:r "${UserName}:(OI)(CI)F" "SYSTEM:(OI)(CI)F" "*S-1-5-32-544:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "failed to secure tunnel .ssh directory" }
& icacls $sshDirectory /setowner $UserName | Out-Null
if ($LASTEXITCODE -ne 0) { throw "failed to set tunnel .ssh owner" }
& icacls $authorizedKeys /inheritance:r /grant:r "${UserName}:F" "SYSTEM:F" "*S-1-5-32-544:F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "failed to secure tunnel authorized_keys" }
& icacls $authorizedKeys /setowner $UserName | Out-Null
if ($LASTEXITCODE -ne 0) { throw "failed to set tunnel authorized_keys owner" }

$configuration = [IO.File]::ReadAllText($SshdConfigPath)
$allowUsersPattern = '(?im)^\s*AllowUsers\s+([^\r\n]+)$'
$allowUsersMatch = [Text.RegularExpressions.Regex]::Match($configuration, $allowUsersPattern)
if ($allowUsersMatch.Success) {
    $allowedUsers = @($allowUsersMatch.Groups[1].Value -split '\s+' | Where-Object { $_ })
    if ($allowedUsers -notcontains $UserName) {
        $allowedUsers += $UserName
        $replacement = "AllowUsers " + ($allowedUsers -join " ")
        $configuration = [Text.RegularExpressions.Regex]::Replace(
            $configuration,
            $allowUsersPattern,
            $replacement,
            1
        )
    }
} else {
    $configuration = "AllowUsers $UserName`r`n" + $configuration
}

$blockStart = "# BEGIN ASHIRAAI OLLAMA TUNNEL"
$blockEnd = "# END ASHIRAAI OLLAMA TUNNEL"
$blockPattern = '(?ms)^# BEGIN ASHIRAAI OLLAMA TUNNEL\r?\n.*?^# END ASHIRAAI OLLAMA TUNNEL\r?\n?'
$configuration = [Text.RegularExpressions.Regex]::Replace($configuration, $blockPattern, "")
$matchBlock = @"
$blockStart
Match User $UserName
    AuthorizedKeysFile C:/Users/$UserName/.ssh/authorized_keys
    PasswordAuthentication no
    AuthenticationMethods publickey
    AllowTcpForwarding remote
    PermitTTY no
    X11Forwarding no
$blockEnd
"@
$configuration = $configuration.TrimEnd() + "`r`n`r`n" + $matchBlock.Trim() + "`r`n"

$backupPath = $SshdConfigPath + ".pre-ashiraai-tunnel.bak"
if (-not (Test-Path -LiteralPath $backupPath)) {
    Copy-Item -LiteralPath $SshdConfigPath -Destination $backupPath
}
[IO.File]::WriteAllText($SshdConfigPath, $configuration, [Text.UTF8Encoding]::new($false))
& "C:\Windows\System32\OpenSSH\sshd.exe" -t
if ($LASTEXITCODE -ne 0) {
    Copy-Item -LiteralPath $backupPath -Destination $SshdConfigPath -Force
    throw "sshd configuration validation failed"
}
Restart-Service -Name sshd

Write-Output ("tunnel user ready: " + $UserName)
Write-Output "password login remains disabled; public-key tunnel only"
