$dirs = @(
    "$env:USERPROFILE\.lmstudio",
    "$env:USERPROFILE\AppData\Local\LM-Studio",
    "$env:USERPROFILE\AppData\Roaming\LM-Studio"
)
foreach ($dir in $dirs) {
    if (Test-Path $dir) {
        Get-ChildItem $dir -Recurse -Filter "*.gguf" -ErrorAction SilentlyContinue | ForEach-Object {
            $gb = [math]::Round($_.Length / 1GB, 2)
            Write-Host "$gb GB  $($_.FullName)"
        }
    }
}
