# InvestAI — daily local paper run (Windows Task Scheduler target).
# Runs the engine in PAPER mode and refreshes docs/index.html. No real orders.
$ErrorActionPreference = "Stop"
$proj = "C:\Users\ganes\projectspace\investai"
$py   = "C:\Users\ganes\.conda\envs\investai\python.exe"
Set-Location $proj
& $py -m investai dashboard --out docs
Write-Output "InvestAI paper run complete: $(Get-Date -Format s)  ->  $proj\docs\index.html"
