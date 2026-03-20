$ErrorActionPreference = 'Stop'

function Invoke-Hook {
    param(
        [string]$Script,
        [hashtable]$Payload
    )

    $json = $Payload | ConvertTo-Json -Depth 20
    $tmp = New-TemporaryFile
    try {
        Set-Content -Path $tmp -Value $json -Encoding UTF8
        Get-Content $tmp | python $Script
    }
    finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

Write-Host '--- PreToolUse: list_dir(path) ---'
Invoke-Hook -Script '.github/hooks/scripts/fast_grep_pre.py' -Payload @{
    tool_name = 'list_dir'
    cwd = (Get-Location).Path
    tool_input = @{ path = 'path' }
}

Write-Host '--- FS after Pre (path should point to .fast-grep/path) ---'
Get-ChildItem 'path' -Force | Select-Object Name, FullName

Write-Host '--- PostToolUse: cleanup ---'
Invoke-Hook -Script '.github/hooks/scripts/fast_grep_post.py' -Payload @{
    tool_name = 'list_dir'
    cwd = (Get-Location).Path
}

Write-Host '--- FS after Post cleanup (path restored) ---'
Get-ChildItem 'path' -Force | Select-Object Name, FullName

Write-Host '--- Hook Log Tail ---'
Get-Content '.github/hooks/fast_grep_hook.log' -Tail 10
