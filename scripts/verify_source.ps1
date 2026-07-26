param(
  [string]$EvidenceDir = "D:\Temp\AFlow\phase1-baseline\verify"
)
$ErrorActionPreference = "Stop"
$ts = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$base = Join-Path $EvidenceDir $ts
New-Item -ItemType Directory -Force -Path $base | Out-Null
function Run-Group($name, $testArgs) {
  $bt = Join-Path $env:TEMP "aflow_pt_${name}_$ts"
  $pc = Join-Path $env:TEMP "aflow_pc_${name}_$ts"
  New-Item -ItemType Directory -Force -Path $bt,$pc | Out-Null
  Remove-Item -Recurse -Force "$env:TEMP\pytest-of-*" -ErrorAction SilentlyContinue
  Write-Host "Running $name with $testArgs"
  & D:\Dev\Aflow_cli\.venv\Scripts\python.exe -m pytest @testArgs --basetemp $bt -o cache_dir=$pc -p no:cacheprovider -v 2>&1 | Tee-Object -FilePath (Join-Path $base "$name.txt")
  $code = $LASTEXITCODE
  Remove-Item -Recurse -Force $bt,$pc -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force "$env:TEMP\pytest-of-*" -ErrorAction SilentlyContinue
  if($code -ne 0){ Write-Error "$name failed exit $code"; exit $code }
}
# Run all groups sequentially to avoid Ollama concurrency
Run-Group "review" @("tests\test_review.py")
Run-Group "setup" @("tests\test_setup.py","tests\test_trigger_schema.py","tests\test_mcp_contract.py")
Run-Group "http" @("tests\test_http_service.py")
Run-Group "cli" @("tests\test_cli.py")
Run-Group "continuity-AB" @("tests\test_continuity.py::test_A_initial_artifacts","tests\test_continuity.py::test_B_stable_step_ids","tests\test_continuity.py::test_C_first_ready","tests\test_continuity.py::test_D_validated_completes")
Run-Group "continuity-CD" @("tests\test_continuity.py::test_E_unsupported_becomes_unverified","tests\test_continuity.py::test_F_failed_reopens","tests\test_continuity.py::test_G_stale_reopens_only_covered","tests\test_continuity.py::test_H_resume_only_tracked")
Run-Group "continuity-EF" @("tests\test_continuity.py::test_I_cli_mcp_equiv","tests\test_continuity.py::test_J_cli_mcp_resume_equiv","tests\test_continuity.py::test_K_no_canonical_rerun","tests\test_continuity.py::test_L_idempotent_resume","tests\test_continuity.py::test_M_revision_increment","tests\test_continuity.py::test_N_stale_revision_rejected")
Run-Group "continuity-GH" @("tests\test_continuity.py::test_O_concurrent_protection","tests\test_continuity.py::test_P_atomic_preservation","tests\test_continuity.py::test_Q_unknown_refs","tests\test_continuity.py::test_R_resume_operation_id_only","tests\test_continuity.py::test_S_completion_rejected_with_unresolved","tests\test_continuity.py::test_T_no_conversation")
# Final full suite attempt with external basetemp in one go but with longer timeout (avoid Ollama parallelism by disabling semantic in tests? Tests call review_plan directly without semantic, so only cli tests need Ollama)
$bt = Join-Path $env:TEMP "aflow_pt_full_$ts"
$pc = Join-Path $env:TEMP "aflow_pc_full_$ts"
New-Item -ItemType Directory -Force -Path $bt,$pc | Out-Null
Remove-Item -Recurse -Force "$env:TEMP\pytest-of-*" -ErrorAction SilentlyContinue
Write-Host "Running FULL suite"
& D:\Dev\Aflow_cli\.venv\Scripts\python.exe -m pytest -q --basetemp $bt -o cache_dir=$pc -p no:cacheprovider 2>&1 | Tee-Object -FilePath (Join-Path $base "full.txt")
$code = $LASTEXITCODE
Remove-Item -Recurse -Force $bt,$pc -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "$env:TEMP\pytest-of-*" -ErrorAction SilentlyContinue
Write-Host "FULL exit $code"
if($code -ne 0){ exit $code }
Write-Host "All groups passed"
exit 0
