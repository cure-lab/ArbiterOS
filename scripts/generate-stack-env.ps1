Param(
  [Parameter(Mandatory = $false)]
  [string]$OutFile = "stack.env",

  # Langfuse init user (optional)
  [Parameter(Mandatory = $false)]
  [string]$InitUserEmail = "admin@example.com",

  [Parameter(Mandatory = $false)]
  [string]$InitUserName = "Admin",

  [Parameter(Mandatory = $false)]
  [string]$InitUserPassword = "ArbiterOS",

  # Langfuse init org/project (optional)
  [Parameter(Mandatory = $false)]
  [string]$OrgId = "arbiteros-org",

  [Parameter(Mandatory = $false)]
  [string]$OrgName = "ArbiterOS",

  [Parameter(Mandatory = $false)]
  [string]$ProjectId = "arbiteros-proj",

  [Parameter(Mandatory = $false)]
  [string]$ProjectName = "ArbiterOS"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function New-RandomBytes([int]$Length) {
  $bytes = New-Object byte[] $Length
  $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  try {
    $rng.GetBytes($bytes)
  } finally {
    $rng.Dispose()
  }
  return $bytes
}

function New-RandomHex([int]$ByteLen) {
  $bytes = New-RandomBytes -Length $ByteLen
  return ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""
}

function New-RandomBase64([int]$ByteLen) {
  $bytes = New-RandomBytes -Length $ByteLen
  return [Convert]::ToBase64String($bytes)
}

function New-RandomPassword([int]$Length = 24) {
  # URL-safe-ish, no quotes/spaces, good for env files
  $alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
  $bytes = New-RandomBytes -Length $Length
  $chars = New-Object char[] $Length
  for ($i = 0; $i -lt $Length; $i++) {
    $chars[$i] = $alphabet[$bytes[$i] % $alphabet.Length]
  }
  return -join $chars
}

function New-LangfuseKey([string]$Prefix, [int]$BodyLen = 24) {
  return "$Prefix$(New-RandomPassword -Length $BodyLen)"
}

$outPath = Join-Path -Path (Get-Location) -ChildPath $OutFile

# Reuse existing POSTGRES_PASSWORD from existing file if available, otherwise generate once
$postgresPassword = $null
if (Test-Path $outPath) {
  $existingLine = Get-Content $outPath | Where-Object { $_ -like 'POSTGRES_PASSWORD=*' } | Select-Object -First 1
  if ($existingLine) {
    $parts = $existingLine.Split('=', 2)
    if ($parts.Count -eq 2 -and -not [string]::IsNullOrWhiteSpace($parts[1])) {
      $postgresPassword = $parts[1]
    }
  }
}
if (-not $postgresPassword) {
  $postgresPassword = New-RandomPassword 24
}
$redisAuth = New-RandomPassword 24
$clickhousePassword = New-RandomPassword 24
$minioPassword = New-RandomPassword 24

$nextAuthSecret = New-RandomBase64 32
$salt = New-RandomBase64 32
$encryptionKey = New-RandomHex 32 # 32 bytes => 64 hex chars

$pk = New-LangfuseKey -Prefix "pk-lf-"
$sk = New-LangfuseKey -Prefix "sk-lf-"

$content = @"
POSTGRES_USER=postgres
POSTGRES_PASSWORD=$postgresPassword
POSTGRES_DB=postgres
POSTGRES_VERSION=17

REDIS_AUTH=$redisAuth

CLICKHOUSE_USER=clickhouse
CLICKHOUSE_PASSWORD=$clickhousePassword

MINIO_ROOT_USER=minio
MINIO_ROOT_PASSWORD=$minioPassword

NEXTAUTH_URL=http://localhost:3000
NEXTAUTH_SECRET=$nextAuthSecret
SALT=$salt
ENCRYPTION_KEY=$encryptionKey

TELEMETRY_ENABLED=true
NEXT_PUBLIC_LANGFUSE_CLOUD_REGION=

LANGFUSE_INIT_ORG_ID=$OrgId
LANGFUSE_INIT_ORG_NAME=$OrgName
LANGFUSE_INIT_PROJECT_ID=$ProjectId
LANGFUSE_INIT_PROJECT_NAME=$ProjectName
LANGFUSE_INIT_PROJECT_PUBLIC_KEY=$pk
LANGFUSE_INIT_PROJECT_SECRET_KEY=$sk
LANGFUSE_INIT_USER_EMAIL=$InitUserEmail
LANGFUSE_INIT_USER_NAME=$InitUserName
LANGFUSE_INIT_USER_PASSWORD=$InitUserPassword

ARBITEROS_LANGFUSE_BASE_URL=http://langfuse-web:3000
"@

[System.IO.File]::WriteAllText($outPath, $content, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "Wrote $OutFile"
Write-Host "Langfuse init keys:"
Write-Host "  LANGFUSE_INIT_PROJECT_PUBLIC_KEY=$pk"
Write-Host "  LANGFUSE_INIT_PROJECT_SECRET_KEY=$sk"
Write-Host "Langfuse init user:"
Write-Host "  LANGFUSE_INIT_USER_EMAIL=$InitUserEmail"
Write-Host "  LANGFUSE_INIT_USER_PASSWORD=$InitUserPassword"

