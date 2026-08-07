# ComfyUI 可视化应用打包指南

从零开始，将 ComfyUI 工作流打包为 Windows 独立 exe 可执行文件的完整流程。

---

## 目录

- [一、前期准备](#一前期准备)
- [二、项目结构搭建](#二项目结构搭建)
- [三、核心代码编写](#三核心代码编写)
- [四、本地测试](#四本地测试)
- [五、Git 仓库管理](#五git-仓库管理)
- [六、GitHub Actions 自动打包](#六github-actions-自动打包)
- [七、模型文件处理策略](#七模型文件处理策略)
- [八、Windows 本地打包](#八windows-本地打包)
- [九、Nuitka 打包参数说明](#九nuitka-打包参数说明)
- [十、完整操作速查表](#十完整操作速查表)

---

## 一、前期准备

### 1.1 环境要求

| 项目 | 要求 |
|------|------|
| 操作系统 | macOS / Windows / Linux（开发机） |
| Python | 3.10+ |
| ComfyUI | 已安装并可正常运行 |
| 模型文件 | 工作流所需的模型已下载到 `models/` 目录 |

**ComfyUI 安装位置示例**（本教程使用）：

```bash
# macOS 示例路径
/Users/qiaozhanwei/ComfyUI-Installs/ComfyUI/ComfyUI

# Windows 示例路径
C:\ComfyUI-Installs\ComfyUI\ComfyUI

# 后续文档中用 $COMFYUI_INSTALL 表示这个路径
```

> **提示**：你的 ComfyUI 安装路径可能不同，后续命令中请替换为实际路径。

### 1.2 导出工作流（API 格式）

1. 启动 ComfyUI，在浏览器中打开 `http://127.0.0.1:8188`
2. 加载你的工作流
3. 点击左上角的 **"图形"** 下拉菜单
4. 选择 **"导出 (API)"**
5. 保存为 `workflow_api.json`

```
图形 ▼
├── 重命名
├── 复制
├── 添加到书签
├── 保存
├── 另存为
── 导出
├── 导出 (API)  ← 选这个！
├── 清除工作流
└── 删除工作流
```

> **重要**：必须导出为 API 格式，否则无法通过 API 调用。

### 1.3 确认模型文件

检查 `comfyui_src/models/` 下是否有工作流所需的模型文件：

```
models/
├── text_encoders/          # 文本编码器（如 qwen_3_4b.safetensors）
├── diffusion_models/       # 扩散模型（如 z_image_turbo_bf16.safetensors）
├── vae/                    # VAE 模型（如 ae.safetensors）
└── ...
```

> **注意**：模型文件通常较大（几 GB ~ 十几 GB），**不要打包进 exe**。文档后续会说明如何处理。

---

## 二、项目结构搭建

### 2.1 创建项目目录

```bash
# 创建项目根目录
mkdir my_comfyui_app
cd my_comfyui_app
```

### 2.2 复制 ComfyUI 源码

将完整的 ComfyUI 安装目录复制到项目中：

```bash
# macOS 示例（请替换为你的实际路径）
cp -r /Users/qiaozhanwei/ComfyUI-Installs/ComfyUI/ComfyUI ./comfyui_src

# Windows 示例
# cp -r C:\ComfyUI-Installs\ComfyUI\ComfyUI .\comfyui_src

# ️ 删除内部的 .git，否则 Git 会识别为嵌入仓库（submodule）
rm -rf comfyui_src/.git
```

> **说明**：`comfyui_src` 就是完整的 ComfyUI 源码目录，包含 `main.py`、`comfy/`、`models/` 等所有文件。

### 2.3 创建项目结构

```bash
# 创建子目录
mkdir -p workflows src ui .github/workflows

# 创建空文件
touch src/__init__.py
```

最终结构如下：

```
my_comfyui_app/
├── comfyui_src/              # ComfyUI 完整源码（已删除 .git）
│   ├── main.py
│   ├── comfy/
│   ├── models/               # 模型文件（不打包进 exe）
│   └── ...
├── workflows/
│   └── workflow_api.json     # API 格式工作流
├── src/
│   ├── __init__.py
│   ├── config.py
│   └── engine.py
├── ui/
│   └── app.py
├── .github/
│   └── workflows/
│       └── build-windows.yml
├── main.py
├── requirements.txt
└── .gitignore
```

### 2.4 创建 .gitignore

```bash
cat << 'EOF' > .gitignore
# 模型文件太大，不纳入 Git
comfyui_src/models/*.safetensors
comfyui_src/models/*.ckpt
comfyui_src/models/*.pt

# ComfyUI 输出
comfyui_src/output/

# Python 缓存
__pycache__/
*.pyc
*.pyo
.venv/
venv/

# 系统文件
.DS_Store
Thumbs.db

# 打包产物
dist/
build/
*.egg-info/
EOF
```

---

## 三、核心代码编写

### 3.1 配置文件 `src/config.py`

```python
from pathlib import Path

class Config:
    # 项目根目录
    BASE_DIR = Path(__file__).parent.parent

    # ComfyUI 核心代码路径
    COMFYUI_PATH = BASE_DIR / "comfyui_src"

    # 工作流文件路径
    WORKFLOW_PATH = BASE_DIR / "workflows" / "workflow_api.json"

    # 模型目录
    MODEL_DIR = COMFYUI_PATH / "models"

    # 内部使用的端口
    PORT = 8188
```

### 3.2 引擎封装 `src/engine.py`

这是整个项目最复杂的部分，需要处理以下问题：
- ComfyUI 工作流有 **UI 格式**（含 subgraph）和 **API 格式**（扁平 dict）两种
- 需要将 UI 格式转换为 API 格式后才能提交
- 需要正确映射 `widgets_values` 数组到命名输入
- 需要正确处理 `control_after_generate` 等前端伪组件
- 需要正确解析 ComfyUI history API 的响应结构

```python
import sys
import json
import time
import subprocess
import requests
from pathlib import Path
from .config import Config

# 各节点类型的 widget 名称列表（按顺序）
# "control_after_generate" 是前端伪组件，记录在这里以便跳过
NODE_WIDGET_NAMES = {
    "CLIPLoader": ["clip_name", "type", "device"],
    "VAELoader": ["vae_name"],
    "UNETLoader": ["unet_name", "weight_dtype"],
    "CLIPTextEncode": ["text"],
    "EmptySD3LatentImage": ["width", "height", "batch_size"],
    "ModelSamplingAuraFlow": ["shift"],
    "KSampler": ["seed", "control_after_generate", "steps", "cfg",
                 "sampler_name", "scheduler", "denoise"],
    "VAEDecode": [],
    "ConditioningZeroOut": [],
    "SaveImage": ["filename_prefix"],
}

# 前端伪组件，不应发送到 API
FRONTEND_ONLY_WIDGETS = {"control_after_generate"}


def _build_link_index(links):
    """构建 link_id -> (origin_id, origin_slot) 索引"""
    index = {}
    for link in links:
        if isinstance(link, dict):
            index[link["id"]] = (link["origin_id"], link["origin_slot"])
        else:
            index[link[0]] = (link[1], link[2])
    return index


def _map_widgets_to_inputs(node_type, widgets_values):
    """将 widgets_values 列表映射为 {widget_name: value} 字典"""
    names = NODE_WIDGET_NAMES.get(node_type, [])
    result = {}
    value_idx = 0
    for name in names:
        if value_idx >= len(widgets_values):
            break
        if name not in FRONTEND_ONLY_WIDGETS:
            result[name] = widgets_values[value_idx]
        value_idx += 1
    return result


def convert_ui_workflow_to_api(workflow):
    """
    将 ComfyUI UI 格式工作流（含 subgraph）转换为 API 格式。

    API 格式为扁平字典：
        {"node_id": {"class_type": "...", "inputs": {...}}, ...}

    如果输入已经是 API 格式，直接返回。
    """
    # 检测是否已经是 API 格式
    sample = next(iter(workflow.values()), None)
    if isinstance(sample, dict) and "class_type" in sample:
        return workflow

    # 查找 subgraph 定义
    definitions = workflow.get("definitions", {})
    subgraphs = definitions.get("subgraphs", [])
    if not subgraphs:
        raise ValueError("Workflow has no subgraph definitions; cannot convert to API format")
    subgraph = subgraphs[0]

    sg_nodes = subgraph.get("nodes", [])
    sg_links = subgraph.get("links", [])
    link_index = _build_link_index(sg_links)

    # subgraph 输入定义
    sg_input_defs = subgraph.get("inputs", [])
    sg_input_link_to_name = {}
    for sg_input in sg_input_defs:
        for lid in sg_input.get("linkIds", []):
            sg_input_link_to_name[lid] = sg_input["name"]

    # 找到外层 subgraph 实例节点
    subgraph_type_id = subgraph.get("id")
    outer_node_for_sg = None
    for outer_node in workflow.get("nodes", []):
        if outer_node.get("type") == subgraph_type_id:
            outer_node_for_sg = outer_node
            break

    outer_widgets_values = outer_node_for_sg.get("widgets_values", []) if outer_node_for_sg else []
    proxy_widgets = outer_node_for_sg.get("properties", {}).get("proxyWidgets", []) if outer_node_for_sg else []

    # 构建 subgraph 输入 -> 外层 widget 值索引的映射
    sg_input_to_inner = {}
    for idx, pw in enumerate(proxy_widgets):
        if len(pw) == 2:
            inner_node_id_str, inner_widget_name = pw
            for sg_input in sg_input_defs:
                if sg_input["name"] == inner_widget_name:
                    sg_input_to_inner[sg_input["name"]] = {
                        "value_index": idx,
                    }
                    break

    # 构建 API 格式
    api_prompt = {}

    # 转换 subgraph 内部节点
    for node in sg_nodes:
        node_id = str(node["id"])
        node_type = node["type"]
        widgets_values = node.get("widgets_values", [])

        inputs = _map_widgets_to_inputs(node_type, widgets_values)

        for inp in node.get("inputs", []):
            inp_name = inp.get("name")
            link_id = inp.get("link")
            if link_id is None or link_id not in link_index:
                continue

            origin_id, origin_slot = link_index[link_id]

            if origin_id == -10:
                # 来自 subgraph 外部输入，从外层节点取值
                sg_input_name = sg_input_link_to_name.get(link_id)
                if sg_input_name and sg_input_name in sg_input_to_inner:
                    val_idx = sg_input_to_inner[sg_input_name]["value_index"]
                    if val_idx < len(outer_widgets_values):
                        inputs[inp_name] = outer_widgets_values[val_idx]
            else:
                inputs[inp_name] = [str(origin_id), origin_slot]

        api_prompt[node_id] = {
            "class_type": node_type,
            "inputs": inputs,
        }

    # 转换外层节点（跳过 subgraph 实例和笔记节点）
    for outer_node in workflow.get("nodes", []):
        node_id = str(outer_node["id"])
        node_type = outer_node.get("type", "")

        if node_type == subgraph_type_id or node_type in ("MarkdownNote", "Note"):
            continue

        widgets_values = outer_node.get("widgets_values", [])
        inputs = _map_widgets_to_inputs(node_type, widgets_values)

        for inp in outer_node.get("inputs", []):
            inp_name = inp.get("name")
            link_id = inp.get("link")
            if link_id is None:
                continue

            for outer_link in workflow.get("links", []):
                if isinstance(outer_link, dict):
                    if outer_link.get("id") == link_id:
                        origin_id = outer_link["origin_id"]
                        origin_slot = outer_link["origin_slot"]
                        break
                else:
                    if outer_link[0] == link_id:
                        origin_id = outer_link[1]
                        origin_slot = outer_link[2]
                        break
            else:
                continue

            if origin_id == outer_node.get("id"):
                continue

            is_subgraph_origin = any(
                on.get("id") == origin_id and on.get("type") == subgraph_type_id
                for on in workflow.get("nodes", [])
            )

            if is_subgraph_origin:
                sg_output_defs = subgraph.get("outputs", [])
                for sg_out in sg_output_defs:
                    for lid in sg_out.get("linkIds", []):
                        if lid in link_index:
                            actual_origin_id, actual_origin_slot = link_index[lid]
                            if actual_origin_id != -10:
                                inputs[inp_name] = [str(actual_origin_id), actual_origin_slot]
                                break
                    if inp_name in inputs and isinstance(inputs[inp_name], list):
                        break
            else:
                inputs[inp_name] = [str(origin_id), origin_slot]

        api_prompt[node_id] = {
            "class_type": node_type,
            "inputs": inputs,
        }

    return api_prompt


class ComfyUIEngine:
    def __init__(self):
        self.comfyui_path = Config.COMFYUI_PATH
        self.port = Config.PORT
        self.base_url = f"http://localhost:{self.port}"
        self.process = None

    def start_server(self):
        """启动 ComfyUI 无头服务（不打开浏览器）"""
        sys.path.insert(0, str(self.comfyui_path))

        self.process = subprocess.Popen(
            [
                sys.executable,
                str(self.comfyui_path / "main.py"),
                "--disable-auto-launch",
                "--port", str(self.port),
            ],
            cwd=str(self.comfyui_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # 等待服务启动（最多 60 秒）
        for _ in range(60):
            try:
                resp = requests.get(f"{self.base_url}/system_stats", timeout=2)
                if resp.status_code == 200:
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False

    def _load_and_convert_workflow(self):
        """加载工作流并转换为 API 格式"""
        with open(Config.WORKFLOW_PATH, 'r', encoding='utf-8') as f:
            workflow = json.load(f)
        return convert_ui_workflow_to_api(workflow)

    def _inject_params(self, api_prompt, prompt=None, seed=None, steps=None):
        """将用户参数注入到工作流节点中"""
        for node_id, node in api_prompt.items():
            class_type = node.get("class_type")
            inputs = node.get("inputs", {})

            if class_type == "CLIPTextEncode" and prompt is not None:
                inputs["text"] = prompt

            if class_type == "KSampler":
                if seed is not None:
                    inputs["seed"] = int(seed)
                if steps is not None:
                    inputs["steps"] = int(steps)

    def execute_workflow(self, **kwargs):
        """执行工作流并返回结果"""
        api_prompt = self._load_and_convert_workflow()
        self._inject_params(
            api_prompt,
            prompt=kwargs.get("prompt"),
            seed=kwargs.get("seed"),
            steps=kwargs.get("steps"),
        )

        # 提交任务
        resp = requests.post(
            f"{self.base_url}/prompt",
            json={"prompt": api_prompt},
        )
        resp.raise_for_status()
        prompt_id = resp.json()["prompt_id"]

        # 轮询结果
        while True:
            resp = requests.get(f"{self.base_url}/history/{prompt_id}")
            data = resp.json()
            if prompt_id in data:
                return data[prompt_id]
            time.sleep(0.5)

    def shutdown(self):
        """停止服务"""
        if self.process:
            self.process.terminate()
            self.process.wait(timeout=10)
```

### 3.3 前端界面 `ui/app.py`

```python
import gradio as gr
from src.engine import ComfyUIEngine
from src.config import Config

# 全局引擎（单例）
engine = None

def get_engine():
    global engine
    if engine is None:
        engine = ComfyUIEngine()
        if not engine.start_server():
            raise RuntimeError("无法启动 ComfyUI 服务")
    return engine

def generate_image(prompt, seed, steps):
    """生成图像的主函数"""
    engine = get_engine()
    result = engine.execute_workflow(
        prompt=prompt,
        seed=seed,
        steps=steps,
    )

    # ComfyUI history 响应结构：
    # {"prompt": [...], "outputs": {node_id: {"images": [{"filename": "...", "subfolder": "", "type": "output"}]}}, "status": {...}, "meta": {...}}
    outputs = result.get("outputs", {})

    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        if images:
            img_info = images[0]
            filename = img_info["filename"]
            subfolder = img_info.get("subfolder", "")
            if subfolder:
                return str(Config.COMFYUI_PATH / "output" / subfolder / filename)
            return str(Config.COMFYUI_PATH / "output" / filename)

    return None

# 构建 Gradio 界面
with gr.Blocks(title="AI 图像生成器") as demo:
    gr.Markdown("# 🎨 AI 图像生成器")
    with gr.Row():
        with gr.Column():
            prompt_input = gr.Textbox(label="提示词", lines=3)
            seed_input = gr.Number(label="随机种子", value=42)
            steps_input = gr.Slider(label="步数", minimum=1, maximum=50, value=20)
            btn = gr.Button("生成")
        with gr.Column():
            output = gr.Image(label="生成结果")
    btn.click(
        fn=generate_image,
        inputs=[prompt_input, seed_input, steps_input],
        outputs=output,
    )

if __name__ == "__main__":
    demo.launch()
```

### 3.4 程序入口 `main.py`

```python
from ui.app import demo

if __name__ == "__main__":
    demo.launch()
```

### 3.5 依赖清单 `requirements.txt`

```txt
gradio>=4.0.0
requests>=2.28.0
numpy>=1.24.0
Pillow>=10.0.0
```

> **说明**：`torch`、`comfy` 等 ComfyUI 自身的依赖不需要写在 `requirements.txt` 中，因为它们包含在 `comfyui_src` 里，运行时通过 `sys.path` 导入。打包时 Nuitka 会自动处理。

---

## 四、本地测试

### 4.1 安装依赖

```bash
cd my_comfyui_app

# 创建虚拟环境（推荐）
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# .venv\Scripts\activate   # Windows

# 安装应用依赖
pip install -r requirements.txt

# 安装 ComfyUI 自身的依赖
cd comfyui_src
pip install -r requirements.txt
cd ..
```

### 4.2 运行测试

```bash
python main.py
```

预期结果：
- 终端显示 `Running on local URL: http://127.0.0.1:7860`
- 浏览器打开 Gradio 界面
- 输入提示词，点击"生成"，右侧显示生成的图片
- 图片同时保存到 `comfyui_src/output/` 目录

### 4.3 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| `AttributeError: 'str' object has no attribute 'get'` | 工作流是 UI 格式，未转换 | 已修复，使用 `convert_ui_workflow_to_api()` |
| `400 Bad Request` | 模型文件缺失 | 检查 `models/` 目录是否有对应模型 |
| 图片不显示在 UI 上 | 路径解析错误 | 已修复，使用 `Config.COMFYUI_PATH / "output" / filename` |
| `'list' object has no attribute 'get'` | history 响应结构解析错误 | 已修复，`execute_workflow()` 返回的是内层 dict |
| 端口被占用 | 8188 端口被其他进程占用 | 修改 `Config.PORT` 或关闭占用进程 |

---

## 五、Git 仓库管理

### 5.1 初始化仓库

```bash
cd my_comfyui_app

git init
git branch -M main
git add .
git commit -m "Initial commit: ComfyUI app with Gradio UI"
```

### 5.2 处理 comfyui_src 的 .git

如果 `comfyui_src` 还保留着 `.git` 文件夹，Git 会把它当作 submodule：

```bash
# 检查是否有 submodule 问题
git status

# 如果看到 "comfyui_src @ 700821e"，说明 .git 没删干净
# 删除内部的 .git
rm -rf comfyui_src/.git

# 从 index 中移除旧的 submodule 引用
git rm --cached comfyui_src

# 删除 .gitmodules（如果存在）
rm -f .gitmodules

# 重新添加
git add comfyui_src
git commit -m "fix: add comfyui_src as regular directory"
```

### 5.3 推送到 Gitee（代码托管）

```bash
git remote add origin https://gitee.com/qzw2015/my-comfyui-app.git
git push -u origin main
```

### 5.4 推送到 GitHub（触发编译）

```bash
# 添加 GitHub remote（命名为 github，避免和 origin 冲突）
git remote add github https://github.com/qiaozhanwei/my-comfyui-app.git

# 使用 SSH 地址（更稳定，不受 GFW 影响）
git remote set-url github git@github.com:qiaozhanwei/my-comfyui-app.git

# 推送
git push github main
```

### 5.5 日常推送流程

```bash
# 推 Gitee（代码托管）
git push origin main

# 推 GitHub（触发编译）
git push github main
```

---

## 六、GitHub Actions 自动打包

### 6.1 创建 workflow 文件

文件路径：`.github/workflows/build-windows.yml`

```yaml
name: Build Windows EXE

on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    runs-on: windows-latest

    steps:
      - name: Checkout code
        uses: actions/checkout@v5

      - name: Setup Python
        uses: actions/setup-python@v6
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
          pip install numpy gradio requests nuitka

      - name: Build EXE
        shell: pwsh
        run: |
          $env:PYTHONPATH = "$env:PYTHONPATH;comfyui_src"
          nuitka --standalone --onefile --windows-console-mode=disable `
            --include-package=comfy `
            --include-package=comfy_api `
            --include-package=comfy_api_nodes `
            --include-package=comfy_config `
            --include-package=comfy_execution `
            --include-package=comfy_extras `
            --include-data-dir=comfyui_src\models=comfyui_src\models `
            --include-data-dir=workflows=workflows `
            --output-dir=dist `
            --output-filename=MyComfyUIApp.exe `
            main.py
          if (-not (Test-Path "dist\MyComfyUIApp.exe")) {
            Write-Error "Build failed: MyComfyUIApp.exe not found in dist/"
            Get-ChildItem dist -ErrorAction SilentlyContinue
            exit 1
          }
          Write-Host "Build successful: $(Get-Item dist\MyComfyUIApp.exe | Select-Object -ExpandProperty Length) bytes"

      - name: Upload artifact
        uses: actions/upload-artifact@v5
        with:
          name: MyComfyUIApp
          path: dist/MyComfyUIApp.exe
```

### 6.2 提交并触发编译

```bash
git add .github/workflows/build-windows.yml
git commit -m "ci: add GitHub Actions Windows build workflow"
git push github main
```

推送后 GitHub Actions 会自动开始编译。

### 6.3 下载编译产物

1. 打开 GitHub 仓库 → **Actions** 标签页
2. 找到最新的 workflow 运行记录（绿色的 ✓ 表示成功）
3. **点击该记录**进入详情页（不是在列表页找）
4. 在详情页**往下滚动到底部**
5. 找到 **"Artifacts"** 区域 → 点击 `MyComfyUIApp` → 开始下载

```
Actions 列表页                          详情页（点进去后）
┌─────────────────────────┐             ┌─────────────────────────┐
│ ✓ fix: correct YAML...  │ ← 点这个   │  运行日志...             │
│ ✓ test                  │             │  运行日志...             │
│  fix: use actions/...  │             │                         │
└─────────────────────────┘             │  Artifacts              │
                                        │  ┌─────────────────┐   │
                                        │  │ MyComfyUIApp    │   │
                                        │  └─────────────────┘   │
                                        │  ↑ 下载在这里！         │
                                        └─────────────────────────┘
```

> **注意**：Artifacts 只在 workflow **运行成功后**才会出现。如果运行失败（红色 ✗），需要先修复错误。Artifacts 保留 **90 天**，过期后需重新触发编译。

### 6.4 YAML 缩进注意事项

YAML 对缩进要求极严格，正确的层级关系：

```yaml
jobs:
  build:                    # ← 2 空格缩进
    runs-on: windows-latest # ← 4 空格缩进（build 的子项）
    steps:                  # ← 4 空格缩进（build 的子项）
      - name: Step 1        # ← 6 空格缩进（steps 的子项）
        uses: action@v1     # ← 8 空格缩进（step 的子项）
```

常见错误：
- `runs-on:` 和 `build:` 同级 → 报 `Unexpected value 'build'`
- `steps:` 缩进不够 → 报 `A sequence was not expected`
- 使用 Tab 而非空格 → YAML 不允许 Tab 缩进

---

## 七、模型文件处理策略

### 7.1 为什么模型不打包进 exe

| 原因 | 说明 |
|------|------|
| 文件太大 | 单个模型 2-8 GB，总大小 10+ GB |
| 分发不便 | exe 文件超过 2 GB，下载和传输困难 |
| 更新灵活 | 模型更新时不需要重新编译 |
| 按需加载 | 用户可以只下载需要的模型 |

### 7.2 模型放置方案

exe 运行后，用户需要手动放置模型文件：

```
MyComfyUIApp.exe 同级目录/
├── comfyui_src/
│   └── models/
│       ├── text_encoders/
│       │   ── qwen_3_4b.safetensors
│       ├── diffusion_models/
│       │   └── z_image_turbo_bf16.safetensors
│       └── vae/
│           └── ae.safetensors
└── workflows/
    ── workflow_api.json
```

### 7.3 自动下载模型（进阶）

可以在 `Config` 中添加模型下载逻辑，首次运行时自动下载：

```python
# src/config.py 中添加
MODEL_DOWNLOAD_URLS = {
    "qwen_3_4b.safetensors": "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/text_encoders/qwen_3_4b.safetensors",
    "z_image_turbo_bf16.safetensors": "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/diffusion_models/z_image_turbo_bf16.safetensors",
    "ae.safetensors": "https://huggingface.co/Comfy-Org/z_image_turbo/resolve/main/split_files/vae/ae.safetensors",
}
```

---

## 八、Windows 本地打包

> **注意**：打包 Windows `.exe` **必须在 Windows 环境下进行**。Nuitka 不支持跨平台编译，macOS/Linux 上无法生成 `.exe`。如果没有 Windows 机器，请使用 [第六节 GitHub Actions](#六github-actions-自动打包)。

### 8.1 环境准备

| 工具 | 用途 | 下载地址 |
|------|------|----------|
| Python 3.10-3.12 | 运行环境（3.13+ 可能与 torch 不兼容） | https://www.python.org/downloads/ |
| Visual Studio Build Tools | Nuitka 编译 C 代码 | https://visualstudio.microsoft.com/visual-cpp-build-tools/ |
| Git | 拉取代码 | https://git-scm.com/ |

> **重要**：Python 版本推荐 **3.11** 或 **3.12**。3.13 是较新版本，部分依赖（如 torch）可能尚未适配。

### 8.2 安装 Visual Studio Build Tools

1. 下载 [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
2. 运行安装程序，勾选 **"Desktop development with C++"**
3. 确保右侧包含 **"MSVC v143 build tools"** 和 **"Windows 10/11 SDK"**
4. 安装完成后重启终端

### 8.3 本地打包步骤

```powershell
# 1. 克隆或复制项目到 Windows
cd C:\projects\my_comfyui_app

# 2. 创建虚拟环境
python -m venv .venv
.venv\Scripts\activate

# 3. 安装依赖
pip install -r requirements.txt
cd comfyui_src
pip install -r requirements.txt
cd ..

# 4. 安装打包工具
pip install nuitka

# 5. 执行打包（设置 PYTHONPATH 让 Nuitka 找到 comfyui_src 下的包）
$env:PYTHONPATH = "$env:PYTHONPATH;comfyui_src"
nuitka --standalone --onefile --windows-console-mode=disable `
    --include-package=comfy `
    --include-package=comfy_api `
    --include-package=comfy_api_nodes `
    --include-package=comfy_config `
    --include-package=comfy_execution `
    --include-package=comfy_extras `
    --include-data-dir=comfyui_src\models=comfyui_src\models `
    --include-data-dir=workflows=workflows `
    --output-dir=dist `
    --output-filename=MyComfyUIApp.exe `
    main.py
```

> **提示**：首次编译 Nuitka 需要编译大量 C 代码，耗时 **30-90 分钟**（取决于 CPU）。后续增量编译会快很多。

### 8.4 本地打包 vs GitHub Actions

| 对比项 | 本地打包 | GitHub Actions |
|--------|----------|----------------|
| 速度 | 快（直接用本机 CPU） | 慢（共享 runner，15-40 分钟） |
| 环境 | 需要手动安装 Build Tools | 开箱即用 |
| 隐私 | 代码不离开本机 | 代码上传到 GitHub |
| 自动化 | 每次需手动执行 | 推送自动触发 |
| 适合场景 | 开发调试、快速迭代 | 正式发布、CI/CD |

---

## 九、Nuitka 打包参数说明

| 参数 | 作用 |
|------|------|
| `--standalone` | 生成独立可执行文件，包含所有依赖 |
| `--onefile` | 打包为单个 exe 文件 |
| `--windows-console-mode=disable` | Windows 下不显示命令行窗口（替代已弃用的 `--disable-console`） |
| `--include-package=comfy` | 强制包含 comfy 包 |
| `--include-package=comfy_api` | 强制包含 comfy_api 包 |
| `--include-package=comfy_api_nodes` | 强制包含 comfy_api_nodes 包 |
| `--include-package=comfy_config` | 强制包含 comfy_config 包 |
| `--include-package=comfy_execution` | 强制包含 comfy_execution 包 |
| `--include-package=comfy_extras` | 强制包含 comfy_extras 包 |
| `--include-data-dir=comfyui_src\models=...` | 包含模型目录（用户需自行放置模型文件） |
| `--include-data-dir=workflows=workflows` | 包含工作流文件目录 |
| `--output-dir=dist` | 输出目录 |
| `--output-filename=MyComfyUIApp.exe` | 输出文件名 |

> **关键**：必须在运行 Nuitka 前设置 `PYTHONPATH` 指向 `comfyui_src`，否则 Nuitka 找不到 `comfy` 等包：
> ```powershell
> $env:PYTHONPATH = "$env:PYTHONPATH;comfyui_src"
> ```

### 常见打包错误

| 错误 | 原因 | 解决 |
|------|------|------|
| `No module named 'xxx'` | 缺少模块 | 添加 `--include-package=xxx` |
| 编译超时 | Nuitka 编译 torch 很慢 | 在 GitHub Actions 上编译，有 6 小时限制 |
| 磁盘空间不足 | runner 磁盘有限 | 编译前删除不需要的文件 |

---

## 十、完整操作速查表

```bash
# === 首次设置 ===
mkdir my_comfyui_app && cd my_comfyui_app
cp -r /path/to/ComfyUI ./comfyui_src
rm -rf comfyui_src/.git
mkdir -p workflows src ui .github/workflows
touch src/__init__.py

# === 开发测试 ===
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python main.py

# === Git 提交 ===
git init && git branch -M main
git add . && git commit -m "Initial commit"
git remote add origin https://gitee.com/qzw2015/my-comfyui-app.git
git remote add github git@github.com:qiaozhanwei/my-comfyui-app.git
git push origin main
git push github main

# === 触发编译 ===
# 推送到 GitHub 后自动触发
# 去 Actions 页面查看进度并下载 exe
```

---

## 附录 A：工作流格式说明

ComfyUI 有两种工作流格式：

### UI 格式（前端导出）
- 包含 `nodes`、`links`、`definitions`、`subgraphs` 等字段
- 节点有位置、大小、颜色等 UI 属性
- **不能**直接通过 API 提交

### API 格式（Save API Format）
- 扁平字典：`{"node_id": {"class_type": "...", "inputs": {...}}}`
- 链接用 `["node_id", slot_index]` 表示
- **可以**直接通过 API 提交

本项目的 `convert_ui_workflow_to_api()` 函数可以自动转换两种格式。

## 附录 B：ComfyUI History API 响应结构

```json
{
  "prompt_id": {
    "prompt": [["node_id", "class_type", {...}]],
    "outputs": {
      "node_id": {
        "images": [
          {
            "filename": "z-image-turbo_00001_.png",
            "subfolder": "",
            "type": "output"
          }
        ]
      }
    },
    "status": {
      "status_str": "success",
      "completed": true
    },
    "meta": {}
  }
}
```

`execute_workflow()` 返回的是 `data[prompt_id]`，即内层 dict。

## 附录 C：端口冲突解决

如果 8188 端口被占用：

```python
# config.py 中修改
PORT = 8189  # 改为其他端口

# 或者动态查找空闲端口
import socket

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

PORT = find_free_port()
```


