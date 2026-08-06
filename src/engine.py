import sys
import json
import time
import subprocess
import requests
from pathlib import Path
from .config import Config

# Widget name order for each node type (required widgets + optional widgets).
# "control_after_generate" is a frontend-only pseudo-widget inserted by ComfyUI
# right after the seed INT; we track it here so we can skip it when building
# the API-format inputs dict.
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

# Widget names that are frontend-only and should NOT be sent to the API.
FRONTEND_ONLY_WIDGETS = {"control_after_generate"}


def _build_link_index(links):
    """Map link_id -> (origin_id, origin_slot) for fast lookup."""
    index = {}
    for link in links:
        if isinstance(link, dict):
            index[link["id"]] = (link["origin_id"], link["origin_slot"])
        else:
            # Legacy list format: [id, origin_id, origin_slot, target_id, target_slot, type]
            index[link[0]] = (link[1], link[2])
    return index


def _map_widgets_to_inputs(node_type, widgets_values):
    """Map widgets_values list to {widget_name: value} dict, skipping frontend-only widgets."""
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


def _find_subgraph(workflow):
    """Locate the first subgraph definition in the workflow."""
    definitions = workflow.get("definitions", {})
    subgraphs = definitions.get("subgraphs", [])
    if not subgraphs:
        return None
    return subgraphs[0]


def _resolve_subgraph_input_mapping(subgraph):
    """Map subgraph input names -> (target_node_id, target_slot) via link IDs.

    Returns:
        input_to_target: {input_name: (target_node_id, target_input_slot)}
        link_id_to_input: {link_id: input_name}
    """
    sg_inputs = subgraph.get("inputs", [])
    input_to_target = {}
    link_id_to_input = {}
    for sg_input in sg_inputs:
        name = sg_input["name"]
        for link_id in sg_input.get("linkIds", []):
            input_to_target[name] = None  # placeholder, filled below
            link_id_to_input[link_id] = name
    return input_to_target, link_id_to_input


def convert_ui_workflow_to_api(workflow):
    """Convert a ComfyUI UI-format workflow (with subgraphs) to API format.

    The API format is a flat dict:
        {"node_id_str": {"class_type": "...", "inputs": {...}}, ...}

    Linked inputs use the form [origin_node_id_str, origin_slot_index].
    """
    # If the workflow is already in API format (flat dict with class_type), return as-is.
    sample = next(iter(workflow.values()), None)
    if isinstance(sample, dict) and "class_type" in sample:
        return workflow

    subgraph = _find_subgraph(workflow)
    if subgraph is None:
        raise ValueError("Workflow has no subgraph definitions; cannot convert to API format")

    sg_nodes = subgraph.get("nodes", [])
    sg_links = subgraph.get("links", [])
    link_index = _build_link_index(sg_links)

    # Build mapping: which subgraph-level input feeds which (node_id, slot)
    sg_input_defs = subgraph.get("inputs", [])
    # link_id -> subgraph input name
    sg_input_link_to_name = {}
    for sg_input in sg_input_defs:
        for lid in sg_input.get("linkIds", []):
            sg_input_link_to_name[lid] = sg_input["name"]

    # For each outer node that instantiates this subgraph, capture its widget values
    # so we can resolve subgraph input values.
    subgraph_type_id = subgraph.get("id")
    outer_node_widgets = {}
    for outer_node in workflow.get("nodes", []):
        if outer_node.get("type") == subgraph_type_id:
            for inp in outer_node.get("inputs", []):
                widget_name = inp.get("name")
                if widget_name and "widget" in inp:
                    # Value comes from the outer node's widgets_values
                    outer_node_widgets[widget_name] = inp.get("widget", {})

    # Collect outer node's widgets_values (positional)
    outer_widgets_values = []
    for outer_node in workflow.get("nodes", []):
        if outer_node.get("type") == subgraph_type_id:
            outer_widgets_values = outer_node.get("widgets_values", [])
            break

    # Map subgraph input names to their positional index in the outer node's
    # widget list by matching the proxyWidgets order.
    outer_node_for_sg = None
    for outer_node in workflow.get("nodes", []):
        if outer_node.get("type") == subgraph_type_id:
            outer_node_for_sg = outer_node
            break

    proxy_widgets = []
    if outer_node_for_sg:
        proxy_widgets = outer_node_for_sg.get("properties", {}).get("proxyWidgets", [])

    # proxy_widgets is a list of [node_id_str, widget_name] pairs, in the same
    # order as the outer node's widgets_values.
    # Build: subgraph_input_name -> (inner_node_id, inner_widget_name)
    sg_input_to_inner = {}
    for idx, pw in enumerate(proxy_widgets):
        if len(pw) == 2:
            inner_node_id_str, inner_widget_name = pw
            # Find which subgraph input this corresponds to
            for sg_input in sg_input_defs:
                if sg_input["name"] == inner_widget_name:
                    for lid in sg_input.get("linkIds", []):
                        if lid in link_index:
                            target_id, target_slot = link_index[lid]
                            sg_input_to_inner[sg_input["name"]] = {
                                "inner_node_id": int(inner_node_id_str),
                                "inner_widget_name": inner_widget_name,
                                "value_index": idx,
                            }
                    break

    # Build the API format
    api_prompt = {}

    # Convert each subgraph node
    for node in sg_nodes:
        node_id = str(node["id"])
        node_type = node["type"]
        widgets_values = node.get("widgets_values", [])

        # Start with widget values mapped to names
        inputs = _map_widgets_to_inputs(node_type, widgets_values)

        # Override with linked inputs
        for inp in node.get("inputs", []):
            inp_name = inp.get("name")
            link_id = inp.get("link")
            if link_id is None:
                continue
            if link_id not in link_index:
                continue

            origin_id, origin_slot = link_index[link_id]

            if origin_id == -10:
                # This input comes from the subgraph's external input.
                # Resolve the actual value from the outer node.
                sg_input_name = sg_input_link_to_name.get(link_id)
                if sg_input_name and sg_input_name in sg_input_to_inner:
                    mapping = sg_input_to_inner[sg_input_name]
                    val_idx = mapping["value_index"]
                    if val_idx < len(outer_widgets_values):
                        inputs[inp_name] = outer_widgets_values[val_idx]
                    # else: keep the default from widgets_values
                # else: keep the default from widgets_values
            else:
                # Regular link to another node
                inputs[inp_name] = [str(origin_id), origin_slot]

        api_prompt[node_id] = {
            "class_type": node_type,
            "inputs": inputs,
        }

    # Convert outer nodes (skip the subgraph instance and notes)
    for outer_node in workflow.get("nodes", []):
        node_id = str(outer_node["id"])
        node_type = outer_node.get("type", "")

        # Skip subgraph instances (already flattened) and notes
        if node_type == subgraph_type_id:
            continue
        if node_type in ("MarkdownNote", "Note"):
            continue

        widgets_values = outer_node.get("widgets_values", [])
        inputs = _map_widgets_to_inputs(node_type, widgets_values)

        # Resolve links from outer nodes
        for inp in outer_node.get("inputs", []):
            inp_name = inp.get("name")
            link_id = inp.get("link")
            if link_id is None:
                continue

            # Outer links format: [link_id, origin_id, origin_slot, target_id, target_slot, type]
            # But we need to find the link in the outer workflow's links array
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

            # If the origin is the subgraph instance, find the actual source node
            if origin_id == outer_node.get("id"):
                continue  # self-reference, skip

            # Check if origin is the subgraph instance node
            is_subgraph_origin = False
            for on in workflow.get("nodes", []):
                if on.get("id") == origin_id and on.get("type") == subgraph_type_id:
                    is_subgraph_origin = True
                    break

            if is_subgraph_origin:
                # Find which subgraph output this connects to
                sg_output_defs = subgraph.get("outputs", [])
                for sg_out in sg_output_defs:
                    for lid in sg_out.get("linkIds", []):
                        # Find this link in the subgraph's links
                        if lid in link_index:
                            actual_origin_id, actual_origin_slot = link_index[lid]
                            if actual_origin_id != -10:  # not from subgraph input
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
        # 将 ComfyUI 路径加入 Python 模块搜索路径
        sys.path.insert(0, str(self.comfyui_path))

        # 启动子进程
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(self.comfyui_path / "main.py"),
                "--disable-auto-launch",    # 不打开浏览器
                "--port", str(self.port)
            ],
            cwd=str(self.comfyui_path),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # 等待服务启动（最多 60 秒）
        for _ in range(60):
            try:
                resp = requests.get(f"{self.base_url}/system_stats", timeout=2)
                if resp.status_code == 200:
                    return True
            except:
                pass
            time.sleep(1)
        return False

    def _load_and_convert_workflow(self):
        """Load the workflow JSON and convert to API format."""
        with open(Config.WORKFLOW_PATH, 'r', encoding='utf-8') as f:
            workflow = json.load(f)
        return convert_ui_workflow_to_api(workflow)

    def _inject_params(self, api_prompt, prompt=None, seed=None, steps=None):
        """Inject user parameters into the converted API prompt."""
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
        """执行工作流，将 UI 格式的工作流转换为 API 格式后提交。"""
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
            json={"prompt": api_prompt}
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
