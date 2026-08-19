#!/usr/bin/env python3
"""Run LLM-as-Judge to score weight on Guides edges."""

from __future__ import annotations

import argparse
import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROMPT_VERSION = "v1.1"
ROOT = Path(__file__).resolve().parents[1]
PROMPT_FILE = Path(__file__).resolve().parent / "prompt_version.md"

SYSTEM_PROMPT = """你是资深汽车造型与整车工程领域专家，同时担任知识图谱关系强度评审（LLM as Judge）。

任务：判断「美学概念」对已关联「设计参数」的影响强度 weight——即实现该美学时，该参数有多关键、调整它能多大程度改变美学表达。

判断依据（按优先级综合使用，不要只盯着边文本）：
1. **领域知识**：汽车造型/包装/人机/气动/灯光等常识——该参数在工程与造型实践中，是否通常用来表达该美学。
2. **美学描述**：概念想传达什么气质、姿态、体验。
3. **参数语义**：参数控制什么几何/物理量，与美学的因果是否成立。
4. **how_to_guide**：边上已有指导说明，作为图谱证据；可印证、细化你的判断。若其空泛、偏题或与领域常识冲突，以领域合理性为准，并在 reason 中简要说明。

硬约束：
1. 只评影响强度；不编造具体数值区间；不否认关系存在。
2. weight ∈ [0.0, 1.0]，一位小数；只表示强度，不表示调大/调小方向。
3. 若美学与参数在领域上几乎无关，即使 how_to_guide 写得很满，也应压低 weight。
4. 若 how_to_guide 很弱/缺失，但领域上该参数确实是该美学的常用抓手，仍可给中高 weight，并写明依据来自领域知识。
5. 只输出一个 JSON 对象；不要 Markdown；不要额外文本。

评分标尺：
- 0.0–0.2：领域上几乎无关，或仅为牵强附会
- 0.3–0.4：弱相关，偶发/次要手段
- 0.5–0.6：明确相关，常见辅助手段之一
- 0.7–0.8：强相关，实现该美学时的主要造型/工程抓手
- 0.9–1.0：近乎决定性，缺少该参数很难成立该美学表达

【reason 写作要求——必须遵守，避免模板化】
- 用自然中文写 1–3 句专业判断，像设计评审发言，而不是判卷套话。
- **禁止**以下模板句式（及近义变体）作为主结构：
  - 「how_to_guide明确…因此影响强度高/中等/弱」
  - 「直接对应…因此影响强度…」
  - 「与…目标直接相关，因此…」
  - 「缺少how_to_guide…故保守给予…」
- 不要以「how_to_guide」三字开头；不要每条都以「因此影响强度X」结尾。
- 先说清「为什么这个参数会影响该美学」（机制/造型逻辑），必要时再点出边文本或领域经验哪一点支撑了你的分数。
- 不同样本的 reason 句式应有变化；可点名具体造型现象（姿态、光影、比例、包覆、视野等）。

Few-shot（学标尺与 reason 风格，勿照抄）：

示例1（强）：
美学=运动感；参数=A柱倾角；guide=通过加大A柱后倾强化前冲姿态与流线感
→ {"edge_id":"demo_1","weight":0.8,"reason":"A柱后倾直接改变侧面剪影的前冲感与风挡倾角联动，是运动姿态最常用的比例抓手之一；边上也把它当作强化流线的手段，故权重偏高。","confidence":0.85}

示例2（中）：
美学=舒适豪华；参数=轴距；guide=适当拉长轴距以改善乘坐空间，并配合柔和侧面线条
→ {"edge_id":"demo_2","weight":0.5,"reason":"加长轴距有助于后排乘坐与修长侧面，对豪华舒适有帮助，但豪华感还强依赖材质、隔音与线面处理，轴距只是空间侧的一环，不宜打太满。","confidence":0.75}

示例3（领域纠偏/弱）：
美学=未来感；参数=制造公差；guide=（空）
→ {"edge_id":"demo_3","weight":0.2,"reason":"制造公差主要约束装配与品质一致性，几乎不直接塑造「未来感」的造型语言；缺少边说明也不改变这一领域判断，故给很低强度。","confidence":0.7}

示例4（guide弱但领域强）：
美学=越野硬派；参数=离地间隙；guide=需综合考虑通过性
→ {"edge_id":"demo_4","weight":0.8,"reason":"离地间隙抬升会立刻改变轮拱空隙与车身姿态，是硬派越野视觉识别的核心量之一；边文本虽笼统，按造型常识仍应给高强度。","confidence":0.8}

输出 JSON Schema：
{
  "edge_id": "string",
  "weight": 0.0,
  "reason": "string",
  "confidence": 0.0
}
confidence ∈ [0.0, 1.0] 为你对本次判定的把握。
"""

def load_env() -> tuple[str, str, str]:
    load_dotenv(ROOT / ".env")
    # support "KEY = value" with spaces
    api_key = os.getenv("API_KEY", "").strip()
    base_url = os.getenv("BASE_URL", "").strip()
    model = os.getenv("MODEL_NAME", "").strip()
    if not api_key or not base_url or not model:
        raise SystemExit("Missing API_KEY / BASE_URL / MODEL_NAME in feature_v2/.env")
    return api_key, base_url, model


def build_user_prompt(rec: dict) -> str:
    aes = rec.get("aesthetic") or {}
    param = rec.get("parameter") or {}
    how = rec.get("how_to_guide") or "（空）"
    desc = aes.get("description") or "（描述缺失）"
    range_text = param.get("range_text") or "（无）"
    unit = param.get("unit") or "（无）"
    return f"""请以造型/工程专家身份，综合领域知识与下列信息，判定影响强度 weight。

edge_id: {rec["edge_id"]}

【美学概念】
名称: {aes.get("name") or "（无）"}
描述: {desc}

【设计参数】
名称: {param.get("name") or "（无）"}
范围文本: {range_text}
单位: {unit}

【边上的指导方式 how_to_guide】（图谱证据，可参考但非唯一依据）
{how}

请输出 JSON：
{{"edge_id":"...","weight":0.0,"reason":"...","confidence":0.0}}
"""

def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            raise
        return json.loads(m.group(0))


def normalize_result(raw: dict, edge_id: str) -> dict:
    weight = float(raw.get("weight"))
    weight = max(0.0, min(1.0, weight))
    weight = round(weight, 1)
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        raise ValueError("empty reason")
    confidence = raw.get("confidence", 0.5)
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, round(confidence, 2)))
    return {
        "edge_id": edge_id,
        "weight": weight,
        "reason": reason,
        "confidence": confidence,
    }


def judge_one(client: OpenAI, model: str, rec: dict, temperature: float, max_retries: int) -> dict:
    user = build_user_prompt(rec)
    last_err = None
    raw_text = ""
    for attempt in range(max_retries + 1):
        try:
            resp = client.chat.completions.create(
                model=model,
                temperature=temperature,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ],
            )
            raw_text = resp.choices[0].message.content or ""
            parsed = extract_json(raw_text)
            normalized = normalize_result(parsed, rec["edge_id"])
            # force edge_id match
            normalized["edge_id"] = rec["edge_id"]
            return {
                **normalized,
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "judged_at": datetime.now(timezone.utc).astimezone().isoformat(),
                "raw_response": raw_text,
                "status": "ok",
                "attempt": attempt + 1,
            }
        except Exception as e:  # noqa: BLE001
            last_err = e
            time.sleep(1.0 * (attempt + 1))
    return {
        "edge_id": rec["edge_id"],
        "weight": None,
        "reason": None,
        "confidence": None,
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "judged_at": datetime.now(timezone.utc).astimezone().isoformat(),
        "raw_response": raw_text,
        "status": "error",
        "error": str(last_err),
        "attempt": max_retries + 1,
    }


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


def load_done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    done = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if obj.get("status") == "ok" and obj.get("edge_id"):
                done.add(str(obj["edge_id"]))
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM as Judge for Guides weight")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--meta", type=Path, default=None)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=0.2)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1, help="Concurrent API workers")
    args = parser.parse_args()

    api_key, base_url, model = load_env()
    client = OpenAI(api_key=api_key, base_url=base_url)

    records = read_jsonl(args.input)
    if args.limit > 0:
        records = records[: args.limit]

    done = load_done_ids(args.output)
    pending = [r for r in records if str(r["edge_id"]) not in done]
    print(
        f"Total={len(records)} done={len(done)} pending={len(pending)} "
        f"model={model} workers={args.workers}",
        flush=True,
    )

    ok = 0
    err = 0
    weights = []
    t0 = time.time()
    write_lock = threading.Lock()
    counter_lock = threading.Lock()
    finished = 0

    def handle_result(i: int, rec: dict, result: dict) -> None:
        nonlocal ok, err, finished
        with write_lock:
            append_jsonl(args.output, result)
        with counter_lock:
            finished += 1
            cur = finished
            if result["status"] == "ok":
                ok += 1
                weights.append(result["weight"])
                print(
                    f"[{cur}/{len(pending)}] ok weight={result['weight']} "
                    f"aes={rec['aesthetic']['name'][:30]!r} "
                    f"param={rec['parameter']['name'][:30]!r}",
                    flush=True,
                )
            else:
                err += 1
                print(
                    f"[{cur}/{len(pending)}] ERROR edge={rec['edge_id']}: {result.get('error')}",
                    flush=True,
                )

    if args.workers <= 1:
        for i, rec in enumerate(pending, 1):
            result = judge_one(client, model, rec, args.temperature, args.max_retries)
            handle_result(i, rec, result)
            if args.sleep > 0:
                time.sleep(args.sleep)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def work(rec: dict) -> tuple[dict, dict]:
            if args.sleep > 0:
                time.sleep(args.sleep * 0.1)
            result = judge_one(client, model, rec, args.temperature, args.max_retries)
            return rec, result

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futs = [ex.submit(work, rec) for rec in pending]
            for i, fut in enumerate(as_completed(futs), 1):
                rec, result = fut.result()
                handle_result(i, rec, result)

    # recompute stats from output file for accuracy under concurrency
    all_out = read_jsonl(args.output) if args.output.exists() else []
    ok_rows = [o for o in all_out if o.get("status") == "ok"]
    err_rows = [o for o in all_out if o.get("status") != "ok"]
    ws = [o["weight"] for o in ok_rows if o.get("weight") is not None]

    elapsed = round(time.time() - t0, 2)
    meta = {
        "prompt_version": PROMPT_VERSION,
        "prompt_file": str(PROMPT_FILE),
        "model": model,
        "base_url": base_url,
        "temperature": args.temperature,
        "workers": args.workers,
        "input": str(args.input),
        "output": str(args.output),
        "input_count": len(records),
        "already_done_before": len(done),
        "newly_ok": ok,
        "newly_error": err,
        "output_ok_total": len(ok_rows),
        "output_error_total": len(err_rows),
        "elapsed_sec": elapsed,
        "weight_mean": round(sum(ws) / len(ws), 3) if ws else None,
        "weight_min": min(ws) if ws else None,
        "weight_max": max(ws) if ws else None,
        "finished_at": datetime.now(timezone.utc).astimezone().isoformat(),
    }
    meta_path = args.meta or args.output.with_name("run_meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
