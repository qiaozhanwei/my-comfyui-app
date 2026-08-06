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
    # execute_workflow returns the prompt entry directly:
    # {"prompt": [...], "outputs": {node_id: {"images": [...]}}, "status": {...}, "meta": {...}}
    result = engine.execute_workflow(
        prompt=prompt,
        seed=seed,
        steps=steps
    )

    outputs = result.get("outputs", {})

    # Find the first node that has image outputs (e.g. SaveImage)
    for node_id, node_output in outputs.items():
        images = node_output.get("images", [])
        if images:
            img_info = images[0]
            filename = img_info["filename"]
            subfolder = img_info.get("subfolder", "")
            # Build the full file path from ComfyUI's output directory
            if subfolder:
                return str(Config.COMFYUI_PATH / "output" / subfolder / filename)
            return str(Config.COMFYUI_PATH / "output" / filename)

    return None

# 构建界面
with gr.Blocks(title="AI 图像生成器") as demo:
    gr.Markdown("# 🎨 AI 图像生成器")
    with gr.Row():
        with gr.Column():
            prompt = gr.Textbox(label="提示词", lines=3)
            seed = gr.Number(label="随机种子", value=42)
            steps = gr.Slider(label="步数", minimum=1, maximum=50, value=20)
            btn = gr.Button("生成")
        with gr.Column():
            output = gr.Image(label="生成结果")
    btn.click(
        fn=generate_image,
        inputs=[prompt, seed, steps],
        outputs=output
    )

if __name__ == "__main__":
    demo.launch()
