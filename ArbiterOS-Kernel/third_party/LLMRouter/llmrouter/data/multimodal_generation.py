"""
Multimodal utilities for dataset conversion pipelines.
"""

import base64
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from PIL import Image

from llmrouter.utils import call_api


def encode_image_to_base64(image):
    """
    Encode a PIL image into a base64 string (JPEG).
    Accepts PIL.Image or string path.
    """
    
    if isinstance(image, str):
        image = Image.open(image)
    
    if image.mode != "RGB":
        image = image.convert("RGB")
    
    buffered = io.BytesIO()
    image.save(buffered, format="JPEG")
    return base64.b64encode(buffered.getvalue()).decode("utf-8")


def image_to_data_url(image):
    """
    Convert a PIL image into an inline data URL (JPEG, base64).
    """
    b64 = encode_image_to_base64(image)
    return f"data:image/jpeg;base64,{b64}"


def build_vision_content(prompt, images):
    """
    Build OpenAI-style vision content: a list of content items containing text and image_url entries.
    """
    content = [{"type": "text", "text": prompt}]
    for img in images:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_to_data_url(img)},
            }
        )
    return content


def call_vlm_with_images(
    model_name,
    vlm_config,
    prompt,
    images,
    system_prompt=None,
    max_tokens=1024,
    temperature=0.2,
):
    """
    Call a VLM with one or more images.
    """
    content = build_vision_content(prompt=prompt, images=images)

    request = {
        "api_endpoint": vlm_config["api_endpoint"],
        "query": content,
        "model_name": model_name,
        "api_name": vlm_config["model"],
        "service": vlm_config["service"],
    }
    if system_prompt:
        request["system_prompt"] = system_prompt

    return call_api(request, max_tokens=max_tokens, temperature=temperature)


def vlm_describe_images(
    prompt,
    images,
    model_name="gemma-3-27b-it",
    vlm_config=None,
    system_prompt=None,
    max_tokens=1024,
    temperature=0.2,
):
    """
    Convenience wrapper returning only the VLM response text.
    """
    if vlm_config is None:
        vlm_config = load_vlm_config(model_name)
    
    result = call_vlm_with_images(
        model_name=model_name,
        vlm_config=vlm_config,
        prompt=prompt,
        images=images,
        system_prompt=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
    )
    return str(result.get("response", ""))


def load_vlm_config(vlm_name="gemma-3-27b-it", vlm_config_path=None):
    """
    Load VLM config from JSON file.
    """
    import json
    from pathlib import Path
    
    if vlm_config_path is None:
        vlm_config_path = str(Path(__file__).parent.parent.parent / "data" / "example_data" / "llm_candidates" / "default_vlm.json")
    
    with open(vlm_config_path, "r", encoding="utf-8") as f:
        all_vlms = json.load(f)
    return all_vlms[vlm_name]


def batch_vlm_describe_images(prompt, images_list, max_workers=100):
    """
    Batch process multiple images with VLM in parallel.
    """
    def worker(idx, prompt, images):
        desc = vlm_describe_images(prompt, images)
        return idx, desc

    tasks = [(idx, prompt, images) for idx, images in enumerate(images_list)]
    results = {}

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(worker, idx, prompt, images): idx for idx, prompt, images in tasks}
        for future in as_completed(futures):
            idx, description = future.result()
            results[idx] = description

    return [results.get(idx) for idx in range(len(images_list))]
