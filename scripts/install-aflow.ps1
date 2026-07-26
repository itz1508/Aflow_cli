param(
  [string]$RepositoryPath = (Get-Location).Path
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Docker is required' }
$resolved = [IO.Path]::GetFullPath($RepositoryPath)
$compose = Join-Path $resolved 'docker-compose.yml'
$envFile = Join-Path $resolved '.env.example'
docker compose --env-file $envFile -f $compose up -d --build
docker compose --env-file $envFile -f $compose run --rm aflow doctor
Write-Output 'A-Flow Docker services are configured. MCP stdio command:'
Write-Output "docker compose --env-file `"$envFile`" -f `"$compose`" run --rm -T aflow mcp"
