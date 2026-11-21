import base64
import json
import re
from openai import OpenAI
from pydantic import BaseModel, Field
from .config import cfg


class ObjectEvidence(BaseModel):
    label: str = Field(min_length=1)
    category: str
    description: str
    attributes: dict[str,str]
    action: str
    bbox_2d: tuple[float,float,float,float]
    confidence: float = Field(ge=0, le=1)


class Scene(BaseModel):
    scene_description: str
    objects: list[ObjectEvidence]


def pixels(box, width, height):
    if not all(0 <= x <= 1000 for x in box):
        raise ValueError("qwen_bbox_out_of_range")
    x1,y1,x2,y2 = [round(v*d/1000) for v,d in zip(box,(width,height,width,height))]
    if x1 >= x2 or y1 >= y2:
        raise ValueError("qwen_empty_bbox")
    return [x1,y1,x2,y2]


class QwenVLMProvider:
    def __init__(self):
        if not cfg.dashscope_api_key:
            raise ValueError("请填写 DASHSCOPE_API_KEY")
        self.client = OpenAI(api_key=cfg.dashscope_api_key, base_url=cfg.qwen_base_url, timeout=150, max_retries=1)

    def request(self, jpeg, prompt, structured=False):
        data = base64.b64encode(jpeg).decode()
        kwargs = {"response_format":{"type":"json_object"}} if structured else {}
        reply = self.client.chat.completions.create(model=cfg.qwen_model, messages=[
            {"role":"system","content":"你分析视频画面。只依据可见内容和提供的观察回答。证据不足时说明无法确定。"},
            {"role":"user","content":[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,"+data}},
                                      {"type":"text","text":prompt}]}], **kwargs)
        content = reply.choices[0].message.content
        if not content:
            raise ValueError("qwen_empty_response")
        return content

    def analyze(self, jpeg):
        prompt = ("用中文描述场景和可互动的人物、动物、物品。按重要性排序，最多3个。"
                  "bbox_2d按左上右下返回0至1000的相对坐标，confidence为0至1。"
                  "只返回符合此 schema 的 JSON，不要输出解释或代码围栏。"
                  + json.dumps(Scene.model_json_schema(),ensure_ascii=False))
        content = self.request(jpeg,prompt,True).strip()
        # Qwen occasionally wraps JSON in ```json ... ``` despite json_object mode.
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", content, re.S | re.I)
        if fenced:
            content = fenced.group(1).strip()
        if not content.startswith("{"):
            start = content.find("{")
            if start < 0:
                raise ValueError("qwen_non_json_response")
            content = content[start:]
        return Scene.model_validate_json(content)
