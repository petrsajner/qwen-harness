Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like '*qh-smoke*' } |
  ForEach-Object {
    Write-Output ("killing {0}: {1}" -f $_.ProcessId, $_.CommandLine.Substring(0, [Math]::Min(140, $_.CommandLine.Length)))
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
  }
Write-Output "---done---"
