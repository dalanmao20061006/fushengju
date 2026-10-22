# 浮生居 POC · 一键推机 + 跑分（Windows PowerShell）
#
# 用法：
#   .\scripts\bench-device.ps1 -Model "D:\models\xxx.gguf" -Threads 6
#   .\scripts\bench-device.ps1 -Model "..." -Serial "xxxx" -Ctx 8192
#
param(
    [Parameter(Mandatory = $true)][string]$Model,
    [int]$Threads = 6,
    [int]$Ctx = 8192,
    [int]$NGpuLayers = 0,
    [string]$Serial = "",
    [string]$Dist = "$PSScriptRoot\..\dist"
)

$ErrorActionPreference = "Stop"

function Invoke-Adb {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)
    if ($Serial) { & adb -s $Serial @Args } else { & adb @Args }
}

if (-not (Get-Command adb -ErrorAction SilentlyContinue)) {
    throw "找不到 adb，请把 platform-tools 加进 PATH"
}
if (-not (Test-Path $Model)) {
    throw "模型不存在: $Model"
}
if (-not (Test-Path "$Dist\llama-cli") -and -not (Test-Path "$Dist\llama-cli.exe")) {
    throw "找不到 $Dist\llama-cli，请先从 Actions artifact 解压出来"
}

Write-Host "== 设备信息 ==" -ForegroundColor Cyan
Invoke-Adb shell "getprop ro.product.model; getprop ro.soc.model; getprop ro.build.version.release; getprop ro.build.version.sdk"
Invoke-Adb shell "cat /proc/meminfo | head -3"

Write-Host "`n== 推二进制 ==" -ForegroundColor Cyan
Invoke-Adb push "$Dist\llama-cli" /data/local/tmp/
Invoke-Adb push "$Dist\llama-bench" /data/local/tmp/
Invoke-Adb shell "chmod 755 /data/local/tmp/llama-cli /data/local/tmp/llama-bench"

Write-Host "`n== 推模型（大文件，等一会儿）==" -ForegroundColor Cyan
Invoke-Adb push $Model /data/local/tmp/model.gguf

Write-Host "`n== llama-bench：预填充 / 生成 ==" -ForegroundColor Cyan
Invoke-Adb shell "/data/local/tmp/llama-bench -m /data/local/tmp/model.gguf -p 512 -n 128 -t $Threads -ngl $NGpuLayers"

Write-Host "`n== llama-cli：8K 上下文 + 首字延迟 ==" -ForegroundColor Cyan
Invoke-Adb shell "/data/local/tmp/llama-cli -m /data/local/tmp/model.gguf -c $Ctx -t $Threads -ngl $NGpuLayers -n 64 -no-cnv -p '你好，做个自我介绍'"

Write-Host "`n== 完成。把上面两段输出贴回来 ==" -ForegroundColor Green
