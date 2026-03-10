$audio = "C:\Users\OVERLORD\Downloads\9_Particles_Physics_Predicted_and_Whether_They_ve_Been_FoundMP3.mp3"
$outDir = "C:\Users\OVERLORD\Downloads\audio_parts"

New-Item -ItemType Directory -Force -Path $outDir | Out-Null

$parts = @(
    @{ name = "Part1_Neutrino";                  start = 0;       end = 156.14 },
    @{ name = "Part2_Positron";                  start = 156.14;  end = 324.94 },
    @{ name = "Part3_Gluon";                     start = 324.94;  end = 635.40 },
    @{ name = "Part4_Quarks_and_Pion";            start = 635.40;  end = 939.99 },
    @{ name = "Part5_Top_Quark_and_Higgs_Boson"; start = 939.99;  end = 1272.99 },
    @{ name = "Part6_Graviton";                  start = 1272.99; end = 1459.20 }
)

foreach ($p in $parts) {
    $out = "$outDir\$($p.name).mp3"
    $dur = $p.end - $p.start
    Write-Host "Cutting: $($p.name) ($($p.start)s - $($p.end)s)"
    & ffmpeg -y -i $audio -ss $($p.start) -t $dur -acodec copy $out 2>&1 | Out-Null
    if (Test-Path $out) {
        $mb = [math]::Round((Get-Item $out).Length / 1MB, 1)
        Write-Host "  OK: $($p.name).mp3 ($mb MB)"
    } else {
        Write-Host "  FAILED: $($p.name)"
    }
}

Write-Host "`nDone. Parts saved to: $outDir"
