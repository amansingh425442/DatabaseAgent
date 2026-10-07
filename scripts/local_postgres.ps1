<#
.SYNOPSIS
Set up, start, stop, or inspect the project's portable local PostgreSQL server.
.DESCRIPTION
Uses official PostgreSQL 16 EDB binaries and pgvector v0.8.6 source. The existing
Visual Studio C++ tools build the extension; no installer or administrator rights
are needed. Binaries, database files, downloads and logs remain under .runtime.
Credentials are read from .env and never displayed. Existing clusters are kept.
Start uses a hidden WMI worker outside the calling terminal/tool process tree.
StartDirect is an internal worker action; use Start for normal local startup.
#>
[CmdletBinding()]
param(
    [ValidateSet('Setup', 'Start', 'StartDirect', 'Stop', 'Status', 'Provenance')]
    [string]$Action = 'Start'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$RuntimeRoot = Join-Path $ProjectRoot '.runtime'
$Downloads = Join-Path $RuntimeRoot 'downloads'
$Logs = Join-Path $RuntimeRoot 'logs'
$PostgresRoot = Join-Path $RuntimeRoot 'postgres\pgsql'
$DataDir = Join-Path $RuntimeRoot 'postgres-data'
$SourceDir = Join-Path $RuntimeRoot 'pgvector-source'
$PgCtl = Join-Path $PostgresRoot 'bin\pg_ctl.exe'
$PostgresVersion = '16.15'
$VectorVersion = '0.8.6'
$PostgresUrl = 'https://get.enterprisedb.com/postgresql/postgresql-16.15-1-windows-x64-binaries.zip'
$VectorUrl = 'https://codeload.github.com/pgvector/pgvector/zip/refs/tags/v0.8.6'
$BindAddress = '127.0.0.1'
$Port = 55432

function Assert-RuntimePath {
    param([string]$Path)
    $AbsolutePath = [IO.Path]::GetFullPath($Path)
    $AllowedPrefix = [IO.Path]::GetFullPath($RuntimeRoot).TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $AbsolutePath.StartsWith($AllowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes the project runtime directory: $AbsolutePath"
    }
    return $AbsolutePath
}

function Invoke-LoggedNative {
    param([string]$Executable, [string[]]$Arguments, [string]$LogName)
    $LogPath = Join-Path $Logs $LogName
    # All callers pass public paths/options only; secrets travel in files or env.
    $ArgumentString = ($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
    }) -join ' '
    $Process = Start-Process -FilePath $Executable -ArgumentList $ArgumentString -WindowStyle Hidden -Wait -PassThru `
        -RedirectStandardOutput $LogPath -RedirectStandardError "$LogPath.stderr"
    if ($Process.ExitCode -ne 0) {
        throw "Command failed with exit code $($Process.ExitCode). See $LogPath and $LogPath.stderr"
    }
}

function Get-LocalAdminSettings {
    $EnvPath = Join-Path $ProjectRoot '.env'
    if (-not (Test-Path -LiteralPath $EnvPath -PathType Leaf)) {
        throw 'Create .env first with python scripts/configure_local.py.'
    }
    $Line = Get-Content -LiteralPath $EnvPath | Where-Object {
        $_ -match '^\s*ADMIN_DATABASE_URL\s*='
    } | Select-Object -Last 1
    if (-not $Line) { throw 'ADMIN_DATABASE_URL is missing from .env.' }
    $Value = ($Line -replace '^\s*ADMIN_DATABASE_URL\s*=\s*', '').Trim()
    if (($Value.StartsWith('"') -and $Value.EndsWith('"')) -or
        ($Value.StartsWith("'") -and $Value.EndsWith("'"))) {
        $Value = $Value.Substring(1, $Value.Length - 2)
    }
    try { $Uri = [Uri]$Value } catch { throw 'ADMIN_DATABASE_URL is invalid.' }
    if ($Uri.Scheme -notin @('postgresql', 'postgresql+psycopg') -or
        $Uri.Host -notin @('localhost', '127.0.0.1') -or
        $Uri.Port -ne $Port) {
        throw 'Portable PostgreSQL requires ADMIN_DATABASE_URL at localhost:55432.'
    }
    $Parts = $Uri.UserInfo.Split(':', 2)
    $User = [Uri]::UnescapeDataString($Parts[0])
    $Database = [Uri]::UnescapeDataString($Uri.AbsolutePath.TrimStart('/'))
    if ($Parts.Count -ne 2 -or -not $Parts[1] -or -not $User -or -not $Database) {
        throw 'ADMIN_DATABASE_URL must contain a user, password and database name.'
    }
    if ($User -notmatch '^[a-zA-Z_][a-zA-Z0-9_]*$' -or
        $Database -notmatch '^[a-zA-Z_][a-zA-Z0-9_]*$') {
        throw 'Use simple PostgreSQL identifiers for the local user and database.'
    }
    return @{ User = $User; Password = [Uri]::UnescapeDataString($Parts[1]); Database = $Database }
}

function Get-Archive {
    param([string]$Url, [string]$Target, [string]$LogName)
    if (-not (Test-Path -LiteralPath $Target -PathType Leaf)) {
        $Partial = "$Target.partial"
        Write-Host "Downloading $(Split-Path $Target -Leaf)..."
        Invoke-LoggedNative 'curl.exe' @('--fail', '--location', '--retry', '3', '--output', $Partial, $Url) $LogName
        Move-Item -LiteralPath $Partial -Destination $Target
    }
}

function Expand-PostgresCore {
    param([string]$ArchivePath, [string]$ExtractRoot)
    $CheckedRoot = Assert-RuntimePath $ExtractRoot
    $AllowedPrefix = $CheckedRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    $null = New-Item -ItemType Directory -Path $CheckedRoot -Force
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $Archive = [IO.Compression.ZipFile]::OpenRead($ArchivePath)
    $Count = 0
    try {
        foreach ($Entry in $Archive.Entries) {
            # The EDB archive also has 17,000+ pgAdmin files. The local DB needs
            # only the server, command-line tools, development headers and libs.
            if ($Entry.FullName -notmatch '^pgsql/(bin|include|lib|share)/|^pgsql/server_license\.txt$' -or
                -not $Entry.Name) { continue }
            $Target = Assert-RuntimePath ([IO.Path]::GetFullPath((Join-Path $CheckedRoot $Entry.FullName)))
            if (-not $Target.StartsWith($AllowedPrefix, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Archive entry escapes the PostgreSQL extraction directory.'
            }
            $null = [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($Target))
            if (-not [IO.File]::Exists($Target) -or ([IO.FileInfo]$Target).Length -ne $Entry.Length) {
                [IO.Compression.ZipFileExtensions]::ExtractToFile($Entry, $Target, $true)
            }
            $Count++
        }
    } finally { $Archive.Dispose() }
    Write-Host "Verified/extracted $Count PostgreSQL core files."
}

function Start-LocalPostgres {
    if (-not (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION'))) {
        throw 'No initialized local cluster. Run this script with -Action Setup first.'
    }
    $ClusterVersion = (Get-Content -LiteralPath (Join-Path $DataDir 'PG_VERSION') -Raw).Trim()
    if ($ClusterVersion -ne '16') { throw 'Existing data directory belongs to another PostgreSQL version; preserving it.' }
    & $PgCtl status -D $DataDir *> (Join-Path $Logs 'postgres-status.log')
    if ($LASTEXITCODE -eq 0) { Write-Host 'Local PostgreSQL is already running.'; return }
    $Listener = [Net.Sockets.TcpClient]::new()
    try {
        $Probe = $Listener.ConnectAsync($BindAddress, $Port)
        if ($Probe.Wait(1000) -and $Listener.Connected) {
            throw 'Port 55432 is already occupied; no existing server will be changed.'
        }
    } catch [System.AggregateException] {
        # Connection refused means the loopback port is available.
    } finally { $Listener.Dispose() }
    $Arguments = "-D `"$DataDir`" -l `"$(Join-Path $Logs 'postgres-server.log')`" -o `"-h $BindAddress -p $Port`" -w start"
    # Start-Process -Wait waits for descendants too on Windows; PostgreSQL's
    # background server must outlive pg_ctl. Wait only for the pg_ctl process.
    $Process = Start-Process -FilePath $PgCtl -ArgumentList $Arguments -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput (Join-Path $Logs 'postgres-start.log') `
        -RedirectStandardError (Join-Path $Logs 'postgres-start-error.log')
    $Process.WaitForExit()
    # Windows PowerShell can lose ExitCode for a fast-exiting process object;
    # confirm the actual owned server state rather than treating null as failure.
    if ($null -ne $Process.ExitCode -and $Process.ExitCode -ne 0) {
        throw 'PostgreSQL did not start. See .runtime/logs/postgres-start*.log.'
    }
    & $PgCtl status -D $DataDir *> (Join-Path $Logs 'postgres-status.log')
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL is not running after startup; inspect .runtime/logs.' }
    Write-Host "PostgreSQL is running on ${BindAddress}:$Port."
}

function Start-BackgroundPostgres {
    if (-not (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION'))) {
        throw 'No initialized local cluster. Run this script with -Action Setup first.'
    }
    & $PgCtl status -D $DataDir *> (Join-Path $Logs 'postgres-status.log')
    if ($LASTEXITCODE -eq 0) { Write-Host 'Local PostgreSQL is already running.'; return }

    $WorkerScript = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'local_postgres.ps1'))
    $ProjectPrefix = $ProjectRoot.TrimEnd('\', '/') + [IO.Path]::DirectorySeparatorChar
    if (-not $WorkerScript.StartsWith($ProjectPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Background worker script escapes the project directory.'
    }
    $PowerShellExe = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    # WMI launches as the current user through its separate provider process.
    # CREATE_BREAKAWAY_FROM_JOB keeps the worker outside provider job limits;
    # ShowWindow=0 and -WindowStyle Hidden prevent an interactive console.
    $Startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        ShowWindow = [uint16]0
        CreateFlags = [uint32]16777216
    }
    $Launch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = ('"' + $PowerShellExe + '" -NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $WorkerScript + '" -Action StartDirect')
        CurrentDirectory = $ProjectRoot
        ProcessStartupInformation = $Startup
    }
    if ($Launch.ReturnValue -ne 0) {
        throw "Background PostgreSQL worker could not start (WMI code $($Launch.ReturnValue))."
    }
    @{
        worker_pid = $Launch.ProcessId
        method = 'Win32_Process.Create'
        create_flags = 16777216
        generated_utc = [DateTime]::UtcNow.ToString('o')
    } | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $Logs 'postgres-launch.json') -Encoding UTF8

    $Deadline = [DateTime]::UtcNow.AddSeconds(75)
    $PgIsReady = Join-Path $PostgresRoot 'bin\pg_isready.exe'
    while ([DateTime]::UtcNow -lt $Deadline) {
        & $PgCtl status -D $DataDir *> (Join-Path $Logs 'postgres-status.log')
        if ($LASTEXITCODE -eq 0) {
            & $PgIsReady -h $BindAddress -p $Port -t 2 -U olist_admin -d postgres *> (Join-Path $Logs 'postgres-readiness.log')
            if ($LASTEXITCODE -eq 0) {
                Write-Host "PostgreSQL is running independently on ${BindAddress}:$Port."
                return
            }
        }
        Start-Sleep -Milliseconds 300
    }
    throw 'Background PostgreSQL startup did not become ready; inspect .runtime/logs/postgres-start*.log.'
}

$null = New-Item -ItemType Directory -Path $RuntimeRoot, $Downloads, $Logs -Force

if ($Action -eq 'Setup') {
    $Admin = Get-LocalAdminSettings
    $PgArchive = Join-Path $Downloads "postgresql-$PostgresVersion-1-windows-x64-binaries.zip"
    $VectorArchive = Join-Path $Downloads "pgvector-v$VectorVersion.zip"
    Get-Archive $PostgresUrl $PgArchive 'postgres-download.log'
    Get-Archive $VectorUrl $VectorArchive 'pgvector-download.log'

    $ExtractRoot = Join-Path $RuntimeRoot 'postgres'
    $ExtractMarker = Join-Path $ExtractRoot ".core-$PostgresVersion-complete"
    if (-not (Test-Path -LiteralPath $ExtractMarker)) {
        if (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION')) {
            throw 'Existing data cluster has no matching binary extraction marker; preserving it for inspection.'
        }
        Write-Host 'Extracting PostgreSQL core binaries...'
        Expand-PostgresCore $PgArchive $ExtractRoot
        Set-Content -LiteralPath $ExtractMarker -Value $PostgresVersion -Encoding ASCII
    }
    $InstalledVersion = (& (Join-Path $PostgresRoot 'bin\postgres.exe') --version)
    if ($LASTEXITCODE -ne 0 -or $InstalledVersion -notmatch "\b$([regex]::Escape($PostgresVersion))\b") {
        throw 'Unexpected PostgreSQL binary version.'
    }
    if (-not (Test-Path -LiteralPath $SourceDir)) {
        $SourceStage = Join-Path $RuntimeRoot 'pgvector-extract'
        if (Test-Path -LiteralPath $SourceStage) { throw 'Existing pgvector extraction stage is preserved; inspect it before continuing.' }
        Expand-Archive -LiteralPath $VectorArchive -DestinationPath $SourceStage
        # Verify both resolved absolute targets before moving a whole directory.
        $MoveSource = Assert-RuntimePath ([IO.Path]::GetFullPath((Resolve-Path -LiteralPath (Join-Path $SourceStage "pgvector-$VectorVersion")).ProviderPath))
        $MoveDestination = Assert-RuntimePath ([IO.Path]::GetFullPath($SourceDir))
        Move-Item -LiteralPath $MoveSource -Destination $MoveDestination
    }
    $VectorInstalled = $true
    foreach ($Relative in @('lib\vector.dll', 'share\extension\vector.control', "share\extension\vector--$VectorVersion.sql")) {
        if (-not (Test-Path -LiteralPath (Join-Path $PostgresRoot $Relative))) { $VectorInstalled = $false }
    }
    if ($VectorInstalled) {
        $Control = Get-Content -LiteralPath (Join-Path $PostgresRoot 'share\extension\vector.control') -Raw
        $VectorInstalled = $Control -match "default_version\s*=\s*'$([regex]::Escape($VectorVersion))'"
    }
    $VsPath = $null
    if (-not $VectorInstalled) {
    if (Test-Path -LiteralPath (Join-Path $DataDir 'postmaster.pid')) {
        throw 'The local server may be running; stop it explicitly before installing a missing/different pgvector build.'
    }
    $VsWhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (-not (Test-Path -LiteralPath $VsWhere)) { throw 'Install Visual Studio C++ tools before compiling pgvector.' }
    $VsPath = & $VsWhere -version '[17.0,18.0)' -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $VsPath) {
        $VsPath = & $VsWhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    }
    if (-not $VsPath) { throw 'No existing Visual Studio C++ toolchain was found.' }
    $VsPath = @($VsPath)[0]
    $VcVars = Join-Path $VsPath 'VC\Auxiliary\Build\vcvars64.bat'
    $BuildScript = Join-Path $RuntimeRoot 'build-pgvector.cmd'
    $BuildBody = @"
@echo off
call "$VcVars"
if errorlevel 1 exit /b %errorlevel%
set "PGROOT=$PostgresRoot"
cd /d "$SourceDir"
nmake /F Makefile.win
if errorlevel 1 exit /b %errorlevel%
nmake /F Makefile.win install
exit /b %errorlevel%
"@
    [IO.File]::WriteAllText($BuildScript, $BuildBody, [Text.Encoding]::ASCII)
    Write-Host "Compiling pgvector $VectorVersion with the existing Visual Studio toolchain..."
    Invoke-LoggedNative $env:ComSpec @('/d', '/c', $BuildScript) 'pgvector-build.log'
    foreach ($Relative in @('lib\vector.dll', 'share\extension\vector.control', "share\extension\vector--$VectorVersion.sql")) {
        if (-not (Test-Path -LiteralPath (Join-Path $PostgresRoot $Relative))) { throw "Missing extension file: $Relative" }
    }
    } else {
        Write-Host "pgvector $VectorVersion is already installed; keeping the existing DLL."
        $ExistingManifestPath = Join-Path $RuntimeRoot 'postgres-provenance.json'
        if (Test-Path -LiteralPath $ExistingManifestPath) {
            $VsPath = (Get-Content -LiteralPath $ExistingManifestPath -Raw | ConvertFrom-Json).compiler_installation
        }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION'))) {
        if (Test-Path -LiteralPath $DataDir) { throw 'An existing uninitialized data directory is preserved; inspect it before continuing.' }
        $PasswordFile = Join-Path $RuntimeRoot ("initdb-password-$([guid]::NewGuid().ToString('N')).tmp")
        try {
            [IO.File]::WriteAllText($PasswordFile, $Admin.Password + "`n", [Text.UTF8Encoding]::new($false))
            Invoke-LoggedNative (Join-Path $PostgresRoot 'bin\initdb.exe') `
                @('-D', $DataDir, '--encoding=UTF8', '--locale=C', '--auth=scram-sha-256', "--username=$($Admin.User)", "--pwfile=$PasswordFile") `
                'postgres-initdb.log'
            Add-Content -LiteralPath (Join-Path $DataDir 'postgresql.conf') -Value "`n# Project local-only development endpoint.`nlisten_addresses = '$BindAddress'`nport = $Port`npassword_encryption = 'scram-sha-256'"
        } finally {
            if (Test-Path -LiteralPath $PasswordFile) { Remove-Item -LiteralPath $PasswordFile -Force }
        }
    }
    Start-BackgroundPostgres
    $PreviousPassword = $env:PGPASSWORD
    try {
        $env:PGPASSWORD = $Admin.Password
        $Psql = Join-Path $PostgresRoot 'bin\psql.exe'
        $Connection = @('-h', $BindAddress, '-p', "$Port", '-U', $Admin.User, '-d', 'postgres', '--no-password', '--set', 'ON_ERROR_STOP=1')
        $Exists = & $Psql @Connection -tAc "SELECT 1 FROM pg_database WHERE datname = '$($Admin.Database)';"
        if ($LASTEXITCODE -ne 0) { throw 'Configured admin credentials did not connect; existing credentials were preserved.' }
        if (([string]$Exists).Trim() -ne '1') {
            Invoke-LoggedNative (Join-Path $PostgresRoot 'bin\createdb.exe') `
                @('-h', $BindAddress, '-p', "$Port", '-U', $Admin.User, '--no-password', $Admin.Database) 'postgres-createdb.log'
        }
    } finally { $env:PGPASSWORD = $PreviousPassword }
    Write-Host "Ready: PostgreSQL $PostgresVersion with pgvector $VectorVersion installed; database $($Admin.Database)."
    Write-Host 'Next: python -m olist_agent.cli init-db'
    $Manifest = @{
        postgres_version = $PostgresVersion
        postgres_url = $PostgresUrl
        pgvector_version = $VectorVersion
        pgvector_url = $VectorUrl
        compiler_installation = $VsPath
        generated_utc = [DateTime]::UtcNow.ToString('o')
    }
    $Manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $RuntimeRoot 'postgres-provenance.json') -Encoding UTF8
    Write-Host 'Official-source provenance recorded; -Action Provenance adds archive SHA256 hashes separately.'
    exit 0
}

if (-not (Test-Path -LiteralPath $PgCtl)) { throw 'Portable PostgreSQL is missing. Run -Action Setup first.' }
switch ($Action) {
    'Start' { Start-BackgroundPostgres }
    'StartDirect' { Start-LocalPostgres }
    'Stop' {
        if (-not (Test-Path -LiteralPath (Join-Path $DataDir 'PG_VERSION'))) { throw 'Local data directory does not exist.' }
        & $PgCtl status -D $DataDir *> (Join-Path $Logs 'postgres-status.log')
        if ($LASTEXITCODE -eq 0) {
            Invoke-LoggedNative $PgCtl @('-D', $DataDir, '-m', 'fast', '-w', 'stop') 'postgres-stop.log'
            Write-Host 'Local PostgreSQL stopped. Database files are preserved.'
        } else { Write-Host 'Local PostgreSQL is already stopped.' }
    }
    'Status' {
        & $PgCtl status -D $DataDir
        exit $LASTEXITCODE
    }
    'Provenance' {
        $ManifestPath = Join-Path $RuntimeRoot 'postgres-provenance.json'
        if (-not (Test-Path -LiteralPath $ManifestPath)) { throw 'Run -Action Setup before recording archive hashes.' }
        $Manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
        foreach ($Item in @(
            @{ Field = 'postgres_sha256'; Path = (Join-Path $Downloads "postgresql-$PostgresVersion-1-windows-x64-binaries.zip") },
            @{ Field = 'pgvector_sha256'; Path = (Join-Path $Downloads "pgvector-v$VectorVersion.zip") }
        )) {
            $Digest = & python -c "import hashlib,sys; source=open(sys.argv[1],'rb'); print(hashlib.file_digest(source,'sha256').hexdigest()); source.close()" $Item.Path
            if ($LASTEXITCODE -ne 0 -or ([string]$Digest).Trim() -notmatch '^[a-f0-9]{64}$') { throw 'Archive hashing failed; existing provenance is preserved.' }
            $Manifest | Add-Member -NotePropertyName $Item.Field -NotePropertyValue ([string]$Digest).Trim() -Force
        }
        $Manifest | ConvertTo-Json | Set-Content -LiteralPath $ManifestPath -Encoding UTF8
        Write-Host 'Official-source archive SHA256 hashes recorded in .runtime/postgres-provenance.json.'
    }
}
