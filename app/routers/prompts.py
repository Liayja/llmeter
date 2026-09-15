"""提示词库 API。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import catalog, runner_bridge
from ..settings import PROJECT_ROOT

router = APIRouter(prefix="/api/prompts", tags=["prompts"])


class PromptIn(BaseModel):
    name: str
    content: str
    tags: str = ""


@router.get("")
def list_prompts() -> list[dict]:
    rows = catalog.list_prompts()
    for r in rows:
        text = r.get("content") or ""
        r["chars"] = len(text)
        r["tokens_est"] = runner_bridge.count_tokens(text) if text else 0
    return rows


@router.get("/files")
def list_prompt_files() -> list[dict]:
    """列出项目 prompts/ 目录下的 .txt 文件，供「一键生成输入梯度场景」使用。"""
    folder = PROJECT_ROOT / "prompts"
    out = []
    for p in sorted(folder.glob("*.txt")):
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        out.append({
            "file": f"prompts/{p.name}",
            "name": p.stem,
            "size": p.stat().st_size,
            "tokens_est": runner_bridge.count_tokens(text[:20000]),
        })
    out.sort(key=lambda x: x["tokens_est"] if x["tokens_est"] else x["size"])
    return out


@router.post("")
def create_prompt(payload: PromptIn) -> dict:
    new_id = catalog.create_prompt(payload.name, payload.content, payload.tags)
    return catalog.get_prompt(new_id)


@router.put("/{prompt_id}")
def update_prompt(prompt_id: int, payload: PromptIn) -> dict:
    if not catalog.get_prompt(prompt_id):
        raise HTTPException(404, "提示词不存在")
    catalog.update_prompt(prompt_id, payload.name, payload.content, payload.tags)
    return catalog.get_prompt(prompt_id)


@router.delete("/{prompt_id}")
def delete_prompt(prompt_id: int) -> dict:
    if not catalog.get_prompt(prompt_id):
        raise HTTPException(404, "提示词不存在")
    catalog.delete_prompt(prompt_id)
    return {"ok": True}
