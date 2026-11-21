# 本地开发环境

本机用于数据整理、标注检查和 CPU 调试；正式模型训练使用华为云。

## 安装位置

| 内容 | 路径 |
| --- | --- |
| 项目 | `D:\CODE\python\ict` |
| Python 3.11.9 | `D:\CODE\python\ict\.tools\python311\python.exe` |
| 项目解释器 | `D:\CODE\python\ict\.venv\Scripts\python.exe` |
| Python 依赖 | `D:\CODE\python\ict\.venv\Lib\site-packages` |
| 命令行工具 | `D:\CODE\python\ict\.venv\Scripts` |
| 官方 Python 安装包及日志 | `D:\CODE\python\ict\.tools\downloads` |
| VS Code 项目配置 | `D:\CODE\python\ict\.vscode\settings.json` |
| Remote SSH 扩展 | `C:\Users\ligua\.vscode\extensions\ms-vscode-remote.remote-ssh-0.128.0` |

Remote SSH 同时安装了配套的 `ms-vscode-remote.remote-ssh-edit-0.87.0` 和 `ms-vscode.remote-explorer-0.5.0`，均位于 `C:\Users\ligua\.vscode\extensions`。

原有 `D:\python3.13` 保留，系统 PATH 未主动修改。

## 使用方法

在项目目录打开 PowerShell，无需激活环境，直接调用：

```powershell
.\.venv\Scripts\python.exe scripts\check_environment.py
.\.venv\Scripts\python.exe your_script.py
.\.venv\Scripts\python.exe -m pip list
```

也可以在终端执行 `.\.venv\Scripts\Activate.ps1` 激活环境。若被 PowerShell 执行策略拦截，继续使用上面的完整解释器路径，无需修改系统执行策略。

VS Code 中运行 `Python: Select Interpreter`，选择 `.venv\Scripts\python.exe`。项目已设置默认解释器。

## 依赖记录

`requirements-local.txt` 记录本地用途的直接依赖；`requirements-local.lock.txt` 在安装完成后记录实际版本。

本地使用 CPU 版 PyTorch，不安装 CUDA。云端必须按实际 GPU/NPU 型号另建训练环境，不要直接复用本地 CPU 版锁定文件。

## 验证结果

安装后执行 `scripts/check_environment.py`，结果保存在 `environment-check.json`。检查包括依赖导入、OpenCV 图像操作、PyTorch CPU 运算和 torchvision NMS。

此检查不下载模型或数据，也不代表已经完成道路病害训练或视频推理。
