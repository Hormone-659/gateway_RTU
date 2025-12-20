# 工业网关远程部署辅助脚本
# 用于将项目文件通过 SSH/SCP 传输到网关

# --- 配置项 (请根据实际情况修改) ---
$GatewayIP = "192.168.1.100"   # 网关的 IP 地址
$GatewayUser = "root"          # 网关的 SSH 用户名
$RemotePath = "/tmp/gateway_deploy" # 临时上传路径
# --------------------------------

Write-Host "正在准备部署..."
Write-Host "目标网关: $GatewayUser@$GatewayIP"
Write-Host "上传路径: $RemotePath"

# 1. 检查 SSH 连接
Write-Host "`n[1/3] 检查连接..."
ssh -q -o BatchMode=yes -o ConnectTimeout=5 $GatewayUser@$GatewayIP "echo Connection OK"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "无法连接到网关，请检查："
    Write-Warning "1. 网线是否连接"
    Write-Warning "2. IP 地址是否正确 (ping $GatewayIP)"
    Write-Warning "3. 是否需要输入密码 (首次连接需手动接受指纹)"
    # 不退出，因为可能是因为需要密码导致 BatchMode 失败，继续尝试 SCP
}

# 2. 创建远程目录
Write-Host "`n[2/3] 创建远程目录..."
ssh $GatewayUser@$GatewayIP "mkdir -p $RemotePath"

# 3. 上传文件
Write-Host "`n[3/3] 上传文件 (可能需要输入密码)..."
# 获取脚本所在目录的上一级目录 (项目根目录)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir

# 构建 scp 命令
# 上传 src, requirements.txt, deploy 到远程临时目录
$SrcPath = Join-Path $ProjectRoot "src"
$ReqPath = Join-Path $ProjectRoot "requirements.txt"
$DeployPath = Join-Path $ProjectRoot "deploy"

scp -r $SrcPath $ReqPath $DeployPath "$GatewayUser@$GatewayIP:$RemotePath/"

if ($?) {
    Write-Host "`n--------------------------------------------------"
    Write-Host "文件传输成功！"
    Write-Host "接下来请执行以下步骤完成部署："
    Write-Host "1. SSH 登录网关: ssh $GatewayUser@$GatewayIP"
    Write-Host "2. 进入目录:     cd $RemotePath/deploy"
    Write-Host "3. 运行安装脚本: sudo ./install.sh"
    Write-Host "--------------------------------------------------"
} else {
    Write-Error "文件传输失败，请检查网络或权限。"
}

