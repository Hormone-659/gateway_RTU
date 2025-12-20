# 下载依赖包到本地，用于离线安装
# 请在有互联网连接的 Windows 电脑上运行此脚本

$DepsDir = ".\deps"
$ReqFile = "..\requirements.txt"

# 创建存放目录
if (-not (Test-Path $DepsDir)) {
    New-Item -ItemType Directory -Path $DepsDir | Out-Null
}

Write-Host "正在下载依赖包到 $DepsDir ..."

# 使用 pip 下载 (需要本地安装有 Python)
# 注意：我们需要下载 Linux 平台的包，因为网关是 Ubuntu
# 如果本地是 Windows，直接 pip download 可能会下载 Windows 的包
# 所以最好指定平台参数，但这需要 pip 版本较新且支持

# 尝试 1: 简单下载 (如果本地也是 Linux 或者包是纯 Python 的)
# pymodbus 和 pyserial 大部分是纯 Python，但也可能有依赖
# 为了保险，我们尝试指定平台为 manylinux (通用 Linux)

Write-Host "注意：正在尝试下载 Linux 兼容的 whl 包..."
pip download -r $ReqFile -d $DepsDir --platform manylinux2014_x86_64 --only-binary=:all: --python-version 38 --implementation cp --abi cp38

if ($?) {
    Write-Host "`n下载成功！"
    Write-Host "请将 deps 文件夹上传到网关的 /opt/gateway_rtu/ 目录下"
    Write-Host "然后运行: /root/venv38/bin/python -m pip install --no-index --find-links=./deps -r requirements.txt"
} else {
    Write-Warning "下载可能遇到问题，尝试不指定平台下载（可能下载到 Windows 包，但在纯 Python 库情况下也能用）..."
    pip download -r $ReqFile -d $DepsDir
}

