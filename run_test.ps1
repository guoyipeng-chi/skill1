$logfile = 'D:\skill1\test_output.log'
$null = '' | Out-File $logfile -Force

# STEP 1
Add-Content $logfile "=== STEP 1: PRE HOOK ===" 
$pre_payload = @{
    tool_name = 'list_dir'
    cwd = 'D:\skill1'
    tool_input = @{ path = 'path' }
} | ConvertTo-Json -Depth 20

$pre_output = $pre_payload | python D:\skill1\.github\hooks\scripts\fast_grep_pre.py 2>&1
Add-Content $logfile "PRE OUTPUT: $pre_output"

# STEP 2 - IMMEDIATELY
Add-Content $logfile "
=== STEP 2: FILES CHECK ===" 
$files = Get-ChildItem D:\skill1\path 2> | Select -ExpandProperty Name
Add-Content $logfile "FILES: $files"

# JUNCTION CHECK
Add-Content $logfile "
=== JUNCTION CHECK ===" 
$junct = cmd /c fsutil reparsepoint query D:\skill1\path 2>&1 | Select -First 5
Add-Content $logfile $junct

# STEP 3
Add-Content $logfile "
=== STEP 3: POST HOOK ===" 
$post_payload = @{
    tool_name = 'list_dir'
    cwd = 'D:\skill1'
    tool_input = @{ path = 'path' }
} | ConvertTo-Json -Depth 20

$post_output = $post_payload | python D:\skill1\.github\hooks\scripts\fast_grep_post.py 2>&1
Add-Content $logfile "POST OUTPUT: $post_output"

# FINAL
Add-Content $logfile "
=== FINAL CHECK ===" 
$final = Get-ChildItem D:\skill1\path 2> | Select -ExpandProperty Name
Add-Content $logfile "FINAL FILES: $final"

Write-Host "Test complete. Results in D:\skill1\test_output.log"
