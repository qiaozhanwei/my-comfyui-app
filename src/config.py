from pathlib import Path

class Config:
    # 项目根目录
    BASE_DIR = Path(__file__).parent.parent

    # ComfyUI 核心代码路径（我们复制进来的整个目录）
    COMFYUI_PATH = BASE_DIR / "comfyui_src"

    # 工作流文件路径
    WORKFLOW_PATH = BASE_DIR / "workflows" / "workflow_api.json"

    # 模型目录（外置，不打包到 exe，用户可自行指定）
    # 这里默认指向 ComfyUI 原来的 models 目录，但可以改为环境变量
    MODEL_DIR = COMFYUI_PATH / "models"

    # 内部使用的端口（确保不被占用）
    PORT = 8188
