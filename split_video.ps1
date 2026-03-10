$video = "C:\Users\OVERLORD\Videos\VideoAutomation\video_workspace\videos\9_Particles_Physics_Predicted_and_Whether_They_ve_Been_Found.mp4"
$outDir = "C:\Users\OVERLORD\Videos\VideoAutomation\video_workspace\videos\parts"

if (-not (Test-Path $video)) {
    Write-Host "Video not found: $video"
    exit 1
}

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

# Get duration
$probe = & ffprobe -v quiet -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 $video
$duration = [math]::Round([double]$probe, 2)
Write-Host "Video duration: $duration seconds"

# Split into 6 equal parts
$parts = 6
$segLen = [math]::Ceiling($duration / $parts)

$names = @(
    "Part_1_Neutrino",
    "Part_2_Positron",
    "Part_3_Gluon",
    "Part_4_Quarks_and_Pion",
    "Part_5_Top_Quark_and_Higgs_Boson",
    "Part_6_Graviton"
)

for ($i = 0; $i -lt $parts; $i++) {
    $start = $i * $segLen
    $name = $names[$i]
    $out = "$outDir\$name.mp4"
    Write-Host "Cutting part $($i+1): start=$start s -> $name"
    & ffmpeg -y -i $video -ss $start -t $segLen -c copy $out 2>&1 | Out-Null
    if (Test-Path $out) {
        $mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
        Write-Host "  Done: $name.mp4 ($mb MB)"
    } else {
        Write-Host "  FAILED: $name"
    }
}

Write-Host "`nAll parts saved to: $outDir"
