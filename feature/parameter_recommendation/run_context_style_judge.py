#!/usr/bin/env python3
"""Run single-pass graph-context automobile style judging without images."""

from __future__ import annotations

import argparse
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from feature_v2.parameter_recommendation.common import (  # type: ignore
        MAIN_STYLES,
        append_jsonl,
        chat_completion,
        clamp_float,
        extract_json_object,
        load_llm_config,
        prompt_hash,
        read_jsonl,
        retry_delay,
    )
    from feature_v2.parameter_recommendation.extract_context_style_inputs import (  # type: ignore
        PROMPT_VERSION,
        RELEVANT_TAIL_LABELS,
    )
else:
    from .common import (
        MAIN_STYLES,
        append_jsonl,
        chat_completion,
        clamp_float,
        extract_json_object,
        load_llm_config,
        prompt_hash,
        read_jsonl,
        retry_delay,
    )
    from .extract_context_style_inputs import PROMPT_VERSION, RELEVANT_TAIL_LABELS


SYSTEM_PROMPT = """你是基于汽车知识图谱证据进行风格判定的汽车设计专家。

输入由两部分组成：
1. 固定的 style_criteria：从美学知识图谱提取的七种主风格判定标准。每种风格包含该 AestheticConcept 通过 Guides(指导) 关联到的 DesignParameter、范围、单位、描述和指导内容。
2. 每次请求中的 vehicle_context：当前汽车实例以及它通过 包含 关系连接到的关键尾节点和属性。这些是判断当前汽车是否满足风格标准的唯一汽车证据。

允许的风格只有：科技、运动、豪华、硬派越野、简约、商务、复古。

判定规则：
1. 将 vehicle_context 中的实际属性，与 style_criteria 中每种风格的 DesignParameter 和 Guides 进行语义匹配。
2. 只有汽车上下文中存在支持某种风格标准的明确属性证据时，才能输出该风格。
3. 车型名称、品牌、级别、售价或能源类型只能作为辅助上下文，不能单独决定风格。
4. 配备运动模式不能单独证明运动风格；配备中控屏不能单独证明科技风格；售价较高不能单独证明豪华风格；车型级别是 SUV 不能单独证明硬派越野风格。
5. 同一条汽车属性可以支持多个风格，但必须分别说明与对应 DesignParameter 或 Guides 的匹配关系。
6. score 表示汽车属性满足该风格设计标准的强度，范围为 0 到 1。
7. confidence 表示图谱上下文是否充分、属性是否明确以及判断是否稳定，范围为 0 到 1。
8. 如果缺少判断某种风格所需的关键属性，应降低 confidence 或不输出，不得用常识补全缺失信息。
9. evidence 必须同时写明汽车实例-包含-尾节点中的具体属性和值，以及相匹配的 DesignParameter 或 Guides；DesignParameter 名称必须逐字来自当前目标风格自己的清单。
10. 不得把其他风格清单中的 DesignParameter 借给当前风格，也不得用清单外的品牌印象、车型常识或配置常识补充判据。
11. 通用配置不能单独决定风格。原则上每个输出风格应具有至少两个相互独立的图谱证据；只有一个弱证据时不要输出。
12. 不看图片、不引用图片、不假设未提供的外观特征。
13. 不输出 parameters、parameter_names 或具体参数列表；这些由程序从图谱补充。
14. 只输出 score>=0.65 且 confidence>=0.65 的风格。
15. 可以输出多个风格；没有风格达到门槛时返回空数组。
16. 只返回一个合法 JSON 对象，不输出 Markdown 或额外说明。

输出格式：
{
  "styles": [
    {
      "style": "科技",
      "score": 0.86,
      "confidence": 0.90,
      "evidence": "汽车实例-包含-屏幕/系统中的中控屏幕尺寸=15.4英寸，以及汽车实例-包含-驾驶硬件中的摄像头数量=10，与科技风格的 Infotainment/Central Screen Size 和 Sensor integration packaging 相匹配。"
    }
  ]
}

以下是固定的 style_criteria：
{criteria}
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def build_system_prompt(criteria: Dict[str, Any]) -> str:
    styles = criteria.get("styles") or {}
    # Full descriptions remain in the auditable criteria artifact. The prompt
    # uses the decision-bearing fields to control token cost and reduce noise.
    prompt_styles = {
        style: [
            {
                key: value
                for key, value in item.items()
                if key in {"name", "range", "unit", "guidance"}
                and value not in (None, "", [])
            }
            for item in styles.get(style, [])
        ]
        for style in MAIN_STYLES
    }
    return SYSTEM_PROMPT.replace(
        "{criteria}", json.dumps(prompt_styles, ensure_ascii=False, separators=(",", ":"))
    )


def user_prompt(record: Dict[str, Any]) -> str:
    payload = {
        "instance_id": record.get("instance_id"),
        "vehicle": record.get("vehicle") or {},
        "contains_context": record.get("contains_context") or {},
    }
    return (
        "请严格依据固定的 style_criteria 和下面的 vehicle_context，判断该汽车体现的主风格。\n"
        "vehicle_context:\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


def normalize(
    raw: Dict[str, Any], record: Dict[str, Any], criteria: Dict[str, Any]
) -> Dict[str, Any]:
    raw_styles = raw.get("styles")
    if not isinstance(raw_styles, list):
        raise ValueError("styles must be a list")
    styles: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    available_labels = set((record.get("contains_context") or {}).keys())
    for item in raw_styles:
        if not isinstance(item, dict):
            raise ValueError("each style must be an object")
        if "parameters" in item or "parameter_names" in item:
            raise ValueError("LLM must not output parameters")
        style = str(item.get("style") or "").strip()
        if style not in MAIN_STYLES:
            raise ValueError("unknown style: %s" % style)
        if style in seen:
            raise ValueError("duplicate style: %s" % style)
        seen.add(style)
        score = round(clamp_float(item.get("score")), 3)
        confidence = round(clamp_float(item.get("confidence")), 3)
        if score < 0.65 or confidence < 0.65:
            raise ValueError("returned styles require score and confidence >= 0.65")
        evidence = str(item.get("evidence") or "").strip()
        if len(evidence) < 20:
            raise ValueError("evidence is too short")
        if available_labels and not any(label in evidence for label in available_labels):
            raise ValueError("evidence must cite at least one provided tail-node label")
        allowed_parameter_names = [
            str(parameter.get("name") or "")
            for parameter in (criteria.get("styles") or {}).get(style, [])
            if isinstance(parameter, dict) and parameter.get("name")
        ]
        if allowed_parameter_names and not any(
            name in evidence for name in allowed_parameter_names
        ):
            raise ValueError(
                "evidence must quote a DesignParameter name from the target style criteria"
            )
        styles.append(
            {
                "style": style,
                "score": score,
                "confidence": confidence,
                "evidence": evidence,
            }
        )
    return {
        "instance_id": str(record["instance_id"]),
        "model_name": record.get("model_name"),
        "car_class": record.get("car_class"),
        "styles": styles,
        "context_tail_labels": sorted(available_labels),
    }


def load_done(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    return {
        str(row["instance_id"])
        for row in read_jsonl(path)
        if row.get("status") == "ok" and row.get("instance_id") is not None
    }


def judge_one(
    record: Dict[str, Any],
    criteria: Dict[str, Any],
    system_prompt: str,
    api_key: str,
    base_url: str,
    model: str,
    temperature: Optional[float],
    max_retries: int,
    timeout: int,
) -> Dict[str, Any]:
    base_user_prompt = user_prompt(record)
    active_user_prompt = base_user_prompt
    raw_response = ""
    usage: Dict[str, Any] = {}
    last_error = ""
    history: List[Dict[str, Any]] = []
    for attempt in range(max_retries + 1):
        try:
            raw_response, usage = chat_completion(
                api_key=api_key,
                base_url=base_url,
                model=model,
                system_prompt=system_prompt,
                user_prompt=active_user_prompt,
                image_urls=None,
                temperature=temperature,
                timeout=timeout,
            )
            normalized = normalize(extract_json_object(raw_response), record, criteria)
            history.append(
                {
                    "attempt": attempt + 1,
                    "status": "ok",
                    "called_at": now_iso(),
                    "prompt_hash": prompt_hash(system_prompt, active_user_prompt),
                    "usage": usage,
                    "raw_response": raw_response,
                }
            )
            return {
                **normalized,
                "task": "expresses_style_graph_context",
                "status": "ok",
                "model": model,
                "prompt_version": PROMPT_VERSION,
                "prompt_hash": prompt_hash(system_prompt, active_user_prompt),
                "judged_at": now_iso(),
                "attempt": attempt + 1,
                "usage": usage,
                "raw_response": raw_response,
                "attempt_history": history,
            }
        except Exception as exc:  # noqa: BLE001
            last_error = "%s: %s" % (type(exc).__name__, str(exc))
            history.append(
                {
                    "attempt": attempt + 1,
                    "status": "error",
                    "called_at": now_iso(),
                    "prompt_hash": prompt_hash(system_prompt, active_user_prompt),
                    "usage": usage,
                    "error": last_error,
                    "raw_response": raw_response,
                }
            )
            if attempt < max_retries:
                active_user_prompt = base_user_prompt + (
                    "\n上次输出未通过校验（%s）。请重新判断，只返回合法 JSON；"
                    "不要输出 parameters，并在 evidence 中引用实际尾节点名称、属性和值。"
                    % last_error
                )
                retry_delay(attempt)
    return {
        "instance_id": str(record["instance_id"]),
        "task": "expresses_style_graph_context",
        "status": "error",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "judged_at": now_iso(),
        "error": last_error,
        "raw_response": raw_response,
        "attempt_history": history,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run graph-context automobile style Judge")
    parser.add_argument("--criteria", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--meta", type=Path, required=True)
    parser.add_argument("--env", type=Path, default=Path(".env"))
    parser.add_argument("--model", default=None)
    parser.add_argument("--workers", type=int, default=50)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.1)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--timeout", type=int, default=240)
    args = parser.parse_args()

    criteria = json.loads(args.criteria.read_text(encoding="utf-8"))
    system_prompt = build_system_prompt(criteria)
    api_key, base_url, model = load_llm_config(args.env, model_override=args.model)
    records = read_jsonl(args.input)
    if args.limit > 0:
        records = records[: args.limit]
    done = load_done(args.output)
    pending = [row for row in records if str(row["instance_id"]) not in done]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    (args.output.parent / "prompt_version_graph_context.md").write_text(
        "# %s\n\n## System Prompt\n\n```text\n%s\n```\n"
        % (PROMPT_VERSION, system_prompt),
        encoding="utf-8",
    )
    started = time.time()
    ok_count = 0
    error_count = 0
    lock = threading.Lock()
    print(
        "task=expresses_style_graph_context model=%s total=%d done=%d pending=%d workers=%d"
        % (model, len(records), len(done), len(pending), args.workers),
        flush=True,
    )

    def run(record: Dict[str, Any]) -> Dict[str, Any]:
        return judge_one(
            record,
            criteria,
            system_prompt,
            api_key,
            base_url,
            model,
            args.temperature,
            args.max_retries,
            args.timeout,
        )

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = [executor.submit(run, row) for row in pending]
        for index, future in enumerate(as_completed(futures), 1):
            result = future.result()
            with lock:
                append_jsonl(args.output, result)
                if result.get("status") == "ok":
                    ok_count += 1
                else:
                    error_count += 1
            if index % 10 == 0 or index == len(futures):
                print(
                    "completed=%d/%d ok=%d error=%d"
                    % (index, len(futures), ok_count, error_count),
                    flush=True,
                )

    meta = {
        "task": "expresses_style_graph_context",
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "criteria": str(args.criteria),
        "input": str(args.input),
        "output": str(args.output),
        "requested_records": len(records),
        "already_done": len(done),
        "new_ok": ok_count,
        "new_error": error_count,
        "workers": args.workers,
        "temperature": args.temperature,
        "max_retries": args.max_retries,
        "system_prompt_characters": len(system_prompt),
        "elapsed_seconds": round(time.time() - started, 3),
        "finished_at": now_iso(),
    }
    args.meta.parent.mkdir(parents=True, exist_ok=True)
    args.meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if error_count:
        raise SystemExit("Judge completed with %d errors" % error_count)


if __name__ == "__main__":
    main()
