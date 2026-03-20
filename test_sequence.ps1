# STEP 1
$pre_payload = @{
    tool_name = 'list_dir'
    cwd = 'D:\skill1'
    tool_input = @{ path = 'path' }
} | ConvertTo-Json -Depth 20

$pre_output = $pre_payload | python D:\skill1\.github\hooks\scripts\fast_grep_pre.py 2>&1
Write-Host "PRE_OUTPUT:" 
Write-Host $pre_output

# STEP 2  
$files = Get-ChildItem D:\skill1\path 2> | Select -ExpandProperty Name
Write-Host "FILES_FOUND: $($files)"

# JUNCTION CHECK
cmd /c fsutil reparsepoint query D:\skill1\path 2>&1 | head -5

# STEP 3
$post_payload = @{
    tool_name = 'list_dir'
    cwd = 'D:\skill1'
    tool_input = @{ path = 'path' }
} | ConvertTo-Json -Depth 20

$post_output = $post_payload | python D:\skill1\.github\hooks\scripts\fast_grep_post.py 2>&1
Write-Host "POST_OUTPUT:"
Write-Host $post_output

# FINAL CHECK
$final = Get-ChildItem D:\skill1\path 2> | Select -ExpandProperty Name  
Write-Host "FINAL_FILES: $($final)"
