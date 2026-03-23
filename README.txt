ComfyUI-Auto-Model-Repair

这是完整可用的本地匹配版插件，不是占位文件。

安装：
1. 解压到 ComfyUI/custom_nodes/ComfyUI-Auto-Model-Repair/
2. 进入该目录安装依赖：
   pip install -r requirements.txt
3. 重启 ComfyUI
4. 打开工作流后点击“扫描缺失模型”

当前功能：
- 扫描工作流中的模型引用
- 检查本地对应模型目录是否存在
- 对缺失模型做同类型模糊匹配
- 高分匹配可一键自动回填

注意：
- 这是 MVP，本版只做本地匹配，不含魔塔下载
- 某些社区节点需要在 config.json 继续补规则
