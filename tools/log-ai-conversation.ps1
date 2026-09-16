[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)] [string] $File,
    [string] $Date = (Get-Date -Format 'yyyy-MM-dd'),
    [string] $Topic = '未命名会话',
    [string] $Goal = '',
    [string] $Decisions = '',
    [string] $Actions = '',
    [string] $Files = '',
    [string] $Validation = '',
    [string] $Unfinished = '',
    [string] $Next = ''
)

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$resolvedFile = [IO.Path]::GetFullPath((Join-Path (Get-Location) $File))
$privateRoot = [IO.Path]::GetFullPath((Join-Path $repo 'PRIVATE'))
if (-not $resolvedFile.StartsWith($privateRoot, [StringComparison]::OrdinalIgnoreCase)) { throw '账本文件必须位于 PRIVATE/ 目录内。' }
if ($resolvedFile -notmatch '\\conversations\\\d{4}-\d{2}-\d{2}\.md$') { throw '账本路径必须符合 PRIVATE/conversations/YYYY-MM-DD.md。' }
if ($resolvedFile -notmatch [regex]::Escape("$Date.md") + '$') { throw '文件名日期必须与 -Date 参数一致。' }

$parent = Split-Path -Parent $resolvedFile
New-Item -ItemType Directory -Force -Path $parent | Out-Null
if (-not (Test-Path -LiteralPath $resolvedFile)) {
    @("# AI 对话账本：$Date", '', '本文件为本地私有资料，不提交 Git。', '') | Set-Content -LiteralPath $resolvedFile -Encoding UTF8
}

$time = Get-Date -Format 'HH:mm'
$entry = @"
## $time — $Topic

- 会话参与者：用户 / AI 工具
- 目标：$Goal
- 用户明确决定：$Decisions
- AI 执行动作：$Actions
- 修改文件：$Files
- 验证：$Validation
- 未完成或阻塞：$Unfinished
- 下一步：$Next
- 数据边界：仅记录可复用事实、决定、行动和结果；未写入隐藏推理、密钥、完整私有代码或不必要个人数据。

"@
Add-Content -LiteralPath $resolvedFile -Value $entry -Encoding UTF8
Write-Output "已追加私有 AI 对话账本：$resolvedFile"
