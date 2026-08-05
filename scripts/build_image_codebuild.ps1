# Build & push trading-lab image via AWS CodeBuild (no local Docker).
# Usage:
#   .\scripts\build_image_codebuild.ps1
#   .\scripts\build_image_codebuild.ps1 -UpdateLambda
#   .\scripts\build_image_codebuild.ps1 -ImageTag abc1234 -UpdateLambda

param(
    [string]$ImageTag = "",
    [switch]$UpdateLambda,
    [string]$ProjectName = "trading-lab-image-build",
    [string]$RoleName = "trading-lab-codebuild-role",
    [string]$EcrRepoName = "trading-lab",
    [string]$LambdaName = "trading-lab-worker"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Invoke-Aws {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$AwsArgs)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & aws @AwsArgs
        if ($LASTEXITCODE -ne 0) {
            throw "aws $($AwsArgs -join ' ') failed with exit $LASTEXITCODE"
        }
    }
    finally {
        $ErrorActionPreference = $prev
    }
}

$AwsRegion = if ($env:AWS_DEFAULT_REGION) { $env:AWS_DEFAULT_REGION } else { "us-east-1" }
$AccountId = (Invoke-Aws sts get-caller-identity --query Account --output text | Out-String).Trim()
if (-not $ImageTag) {
    $ImageTag = (git -C $Root rev-parse --short HEAD).Trim()
}
$Bucket = (Invoke-Aws s3api list-buckets --query "Buckets[?starts_with(Name, 'trading-lab-artifacts-')].Name | [0]" --output text | Out-String).Trim()
if (-not $Bucket -or $Bucket -eq "None") {
    throw "No trading-lab-artifacts-* bucket found"
}
$SourceKey = "codebuild-src/${ImageTag}.zip"
$EcrUri = "$AccountId.dkr.ecr.$AwsRegion.amazonaws.com/$EcrRepoName"
$ImageUri = "${EcrUri}:${ImageTag}"
$RoleArn = "arn:aws:iam::${AccountId}:role/${RoleName}"

Write-Host "=== zip source (tag=$ImageTag) ==="
$ZipPath = Join-Path $env:TEMP "trading-lab-$ImageTag.zip"
if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
$ErrorActionPreference = "Continue"
tar -a -cf $ZipPath `
    --exclude=".venv" `
    --exclude=".git" `
    --exclude=".pytest_cache" `
    --exclude=".ruff_cache" `
    --exclude="__pycache__" `
    --exclude="*.pyc" `
    -C $Root `
    api src Dockerfile pyproject.toml uv.lock buildspec.yml
if ($LASTEXITCODE -ne 0) {
    $stage = Join-Path $env:TEMP "tl-src-$ImageTag"
    if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
    New-Item -ItemType Directory -Path $stage | Out-Null
    foreach ($p in @("api", "src", "Dockerfile", "pyproject.toml", "uv.lock", "buildspec.yml")) {
        Copy-Item -Path (Join-Path $Root $p) -Destination (Join-Path $stage $p) -Recurse -Force
    }
    Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $ZipPath -Force
}
$ErrorActionPreference = "Stop"
if (-not (Test-Path $ZipPath)) { throw "Failed to create source zip" }

Write-Host "=== upload s3://$Bucket/$SourceKey ==="
Invoke-Aws s3 cp $ZipPath "s3://$Bucket/$SourceKey" --region $AwsRegion | Out-Null

Write-Host "=== ensure ECR repository ==="
$ErrorActionPreference = "Continue"
& aws ecr describe-repositories --repository-names $EcrRepoName --region $AwsRegion 2>$null | Out-Null
$ecrOk = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $ecrOk) {
    Invoke-Aws ecr create-repository --repository-name $EcrRepoName --region $AwsRegion | Out-Null
}

Write-Host "=== ensure CodeBuild IAM role ==="
$trust = @"
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Service": "codebuild.amazonaws.com" },
    "Action": "sts:AssumeRole"
  }]
}
"@
$trustPath = Join-Path $env:TEMP "tl-cb-trust.json"
[System.IO.File]::WriteAllText($trustPath, $trust, [System.Text.UTF8Encoding]::new($false))

$ErrorActionPreference = "Continue"
& aws iam get-role --role-name $RoleName 2>$null | Out-Null
$roleExists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $roleExists) {
    Invoke-Aws iam create-role --role-name $RoleName --assume-role-policy-document "file://$trustPath" | Out-Null
}

$policy = @"
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ],
      "Resource": "arn:aws:logs:${AwsRegion}:${AccountId}:log-group:/aws/codebuild/${ProjectName}*"
    },
    {
      "Effect": "Allow",
      "Action": ["s3:GetObject", "s3:GetObjectVersion"],
      "Resource": "arn:aws:s3:::${Bucket}/codebuild-src/*"
    },
    {
      "Effect": "Allow",
      "Action": ["ecr:GetAuthorizationToken"],
      "Resource": "*"
    },
    {
      "Effect": "Allow",
      "Action": [
        "ecr:BatchCheckLayerAvailability",
        "ecr:GetDownloadUrlForLayer",
        "ecr:BatchGetImage",
        "ecr:PutImage",
        "ecr:InitiateLayerUpload",
        "ecr:UploadLayerPart",
        "ecr:CompleteLayerUpload"
      ],
      "Resource": "arn:aws:ecr:${AwsRegion}:${AccountId}:repository/${EcrRepoName}"
    }
  ]
}
"@
$policyPath = Join-Path $env:TEMP "tl-cb-policy.json"
[System.IO.File]::WriteAllText($policyPath, $policy, [System.Text.UTF8Encoding]::new($false))
Invoke-Aws iam put-role-policy --role-name $RoleName --policy-name trading-lab-codebuild --policy-document "file://$policyPath" | Out-Null

Start-Sleep -Seconds 8

Write-Host "=== ensure CodeBuild project ==="
$projectJson = @{
    name        = $ProjectName
    description = "Build trading-lab Lambda container image"
    source      = @{
        type      = "S3"
        location  = "$Bucket/$SourceKey"
        buildspec = "buildspec.yml"
    }
    artifacts   = @{ type = "NO_ARTIFACTS" }
    environment = @{
        type                 = "LINUX_CONTAINER"
        image                = "aws/codebuild/amazonlinux2-x86_64-standard:5.0"
        computeType          = "BUILD_GENERAL1_MEDIUM"
        privilegedMode       = $true
        environmentVariables = @(
            @{ name = "IMAGE_TAG"; value = $ImageTag; type = "PLAINTEXT" }
            @{ name = "ECR_REPO"; value = $EcrRepoName; type = "PLAINTEXT" }
        )
    }
    serviceRole = $RoleArn
}
$projPath = Join-Path $env:TEMP "tl-cb-project.json"
[System.IO.File]::WriteAllText(
    $projPath,
    ($projectJson | ConvertTo-Json -Depth 8),
    [System.Text.UTF8Encoding]::new($false)
)

$ErrorActionPreference = "Continue"
$check = & aws codebuild batch-get-projects --names $ProjectName --output json 2>$null | Out-String
$ErrorActionPreference = "Stop"
$projExists = ($check -match "`"name`":\s*`"$ProjectName`"")

if ($projExists) {
    Invoke-Aws codebuild update-project --cli-input-json "file://$projPath" | Out-Null
}
else {
    Invoke-Aws codebuild create-project --cli-input-json "file://$projPath" | Out-Null
}

Write-Host "=== start-build ==="
$buildOut = Invoke-Aws codebuild start-build `
    --project-name $ProjectName `
    --environment-variables-override "name=IMAGE_TAG,value=$ImageTag,type=PLAINTEXT" `
    --query "build.id" --output text | Out-String
$BuildId = $buildOut.Trim()
Write-Host "BuildId=$BuildId"

Write-Host "=== wait for build (this can take several minutes) ==="
do {
    Start-Sleep -Seconds 15
    $status = (Invoke-Aws codebuild batch-get-builds --ids $BuildId --query "builds[0].buildStatus" --output text | Out-String).Trim()
    Write-Host "status=$status"
} while ($status -eq "IN_PROGRESS")

if ($status -ne "SUCCEEDED") {
    Write-Host "=== build failed; recent logs hint ==="
    $group = (Invoke-Aws codebuild batch-get-builds --ids $BuildId --query "builds[0].logs.groupName" --output text | Out-String).Trim()
    $stream = (Invoke-Aws codebuild batch-get-builds --ids $BuildId --query "builds[0].logs.streamName" --output text | Out-String).Trim()
    if ($group -and $stream -and $group -ne "None") {
        Invoke-Aws logs get-log-events --log-group-name $group --log-stream-name $stream --limit 40 --query "events[*].message" --output text
    }
    throw "CodeBuild failed: $status"
}

Write-Host "Image ready: $ImageUri"

if ($UpdateLambda) {
    Write-Host "=== update Lambda $LambdaName ==="
    Invoke-Aws lambda update-function-code `
        --function-name $LambdaName `
        --image-uri $ImageUri `
        --region $AwsRegion | Out-Null
    Invoke-Aws lambda wait function-updated --function-name $LambdaName --region $AwsRegion
    Write-Host "Lambda $LambdaName updated to $ImageUri"
}

Write-Host "Done."
# Emit tag for callers (deploy_local.py)
Write-Output "IMAGE_TAG=$ImageTag"
