"""场景模板 API。"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services import catalog, runner_bridge
from ..settings import PROJECT_ROOT

router = APIRouter(prefix="/api/scenarios", tags=["scenarios"])


class ScenarioIn(BaseModel):
    name: str
    prompt: str = ""
    prompt_file: str = ""
    overrides: dict | None = None      # 如 {"max_tokens": 512, "timeout": 90, "qps": 8}
    sort: int = 0


@router.get("")
def list_scenarios() -> list[dict]:
    rows = catalog.list_scenarios()
    for r in rows:
        text = ""
        source = ""
        if r.get("prompt_file"):
            path = PROJECT_ROOT / r["prompt_file"]
            source = f"文件 {r['prompt_file']}"
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
        else:
            text = r.get("prompt") or ""
            source = "内置文本（复制内容）" if text else "未绑定提示词"
        r["prompt_source"] = source
        r["prompt_chars"] = len(text)
        r["prompt_tokens_est"] = runner_bridge.count_tokens(text) if text else 0
    return rows


@router.post("")
def create_scenario(payload: ScenarioIn) -> dict:
    new_id = catalog.create_scenario(payload.name, payload.prompt, payload.prompt_file,
                                    payload.overrides, payload.sort)
    return catalog.get_scenario(new_id)


class BulkScenarioIn(BaseModel):
    files: list[str] = []          # 相对项目根的提示词文件，如 prompts/input-3k.txt
    prompt_ids: list[int] = []     # 或从提示词库导入
    group: str = ""                # 场景名前缀，如 "输入"


@router.post("/bulk")
def bulk_create(payload: BulkScenarioIn) -> dict:
    """批量生成场景：一次把「输入梯度」这类多档场景建好（单模型多场景的主力用法）。"""
    created = []
    for pid in payload.prompt_ids:
        p = catalog.get_prompt(pid)
        if not p:
            continue
        name = f"{payload.group}-{p['name']}" if payload.group else p["name"]
        created.append(catalog.create_scenario(name, p["content"], ""))
    for rel in payload.files:
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        stem = Path(rel).stem
        # prompts/input-3k.txt + 前缀「输入」→ 输入-3k（避免 输入-input-3k 这类冗余）
        for pre in ("input-", "output-"):
            if stem.lower().startswith(pre):
                stem = stem[len(pre):]
                break
        name = f"{payload.group}-{stem}" if payload.group else stem
        created.append(catalog.create_scenario(name, "", rel))
    if not created:
        raise HTTPException(400, "没有可导入的提示词")
    return {"created": created}


@router.put("/{scenario_id}")
def update_scenario(scenario_id: int, payload: ScenarioIn) -> dict:
    if not catalog.get_scenario(scenario_id):
        raise HTTPException(404, "场景不存在")
    catalog.update_scenario(scenario_id, payload.name, payload.prompt, payload.prompt_file,
                            payload.overrides, payload.sort)
    return catalog.get_scenario(scenario_id)


@router.delete("/{scenario_id}")
def delete_scenario(scenario_id: int) -> dict:
    if not catalog.get_scenario(scenario_id):
        raise HTTPException(404, "场景不存在")
    catalog.delete_scenario(scenario_id)
    return {"ok": True}
