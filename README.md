安装及使用说明，作者的主页，在B站搜索 神都猫玩AI
QQ群：340983417

# ComfyUI-Auto-Model-Repair

> 🔧 自动检测并修复 ComfyUI 工作流中的缺失模型

---

## ✨ 功能特点

- 🔍 自动扫描工作流中的缺失模型（UNET / CLIP / VAE / LoRA）
- 🧠 智能本地模糊匹配（支持不同命名）
- 🎯 手动选择候选模型（精准替换）
- ⚡ 一键自动修复高分匹配
- 🌐 一键跳转搜索：
  - 魔塔（ModelScope）
  - HuggingFace
- 🧩 悬浮式 UI，不影响原有界面

---

## 🚀 安装方法

1. 克隆或下载到 `custom_nodes` 目录：

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/signerzwb/ComfyUI-Auto-Model-Repair.git

# ComfyUI-Auto-Model-Repair

> 🔧 Automatically detect and repair missing models in ComfyUI workflows

---

## ✨ Features

- 🔍 Scan workflow and detect missing models (UNET / CLIP / VAE / LoRA)
- 🧠 Smart fuzzy matching for local models
- 🎯 Manual selection: choose which candidate to apply
- ⚡ One-click auto repair for high-confidence matches
- 🌐 Quick search links:
  - ModelScope (魔塔)
  - HuggingFace
- 🧩 Non-intrusive UI (floating panel)

---

## 📸 Preview

- Scan missing models  
- Show candidates with similarity score  
- Manual selection & apply  

---

## 🚀 Installation

1. Clone or download into your `custom_nodes`:

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/yourname/ComfyUI-Auto-Model-Repair.git
