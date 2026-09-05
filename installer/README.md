# Windows 安装器构建说明

安装器是 Windows .NET Framework WinForms EXE，不依赖 Python、Git、NSIS 或网络下载工具。

```powershell
.\installer\build.ps1
```

构建结果为 `dist\ShenDuMao-ComfyUI-Workflow-Assistant-v1.0.0-Setup.exe`。安装器在欢迎页后自动识别常见的 ComfyUI 路径，允许手动选择根目录，并且只接受含有 `main.py` 的目录。
