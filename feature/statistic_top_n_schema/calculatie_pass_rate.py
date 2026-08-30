import json

TOP_N_SCHEMA = 25

input_schemas = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/sort_schemas_by_case.json"
input_path_schemas = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/path_schemas_reversed.json"
output_json = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/pass_rate.json"


def get_top_n_schema():
    """贪心选择能够让更多 case 同时命中风格和车型的 schema。"""
    with open(input_schemas, "r" , encoding='utf-8') as f:
        schemas = json.load(f)

    candidates = []
    for rank, schema_info in enumerate(schemas):
        schema = schema_info["schema"]
        schema_case_set = set(schema_info["case"])
        if not schema_case_set:
            continue

        schema_type = schema.strip().split(" <- ")[-1]
        if schema_type not in ("汽车风格", "汽车车型"):
            continue

        candidates.append(
            {
                "schema": schema,
                "case_set": schema_case_set,
                "schema_type": schema_type,
                "rank": rank,
            }
        )

    selected_schemas = []
    selected_schema_set = set()
    covered_cases = {
        "汽车风格": set(),
        "汽车车型": set(),
    }

    while len(selected_schemas) < TOP_N_SCHEMA:
        current_pass_cases = covered_cases["汽车风格"] & covered_cases["汽车车型"]
        best_candidate = None
        best_score = None

        for candidate in candidates:
            if candidate["schema"] in selected_schema_set:
                continue

            schema_type = candidate["schema_type"]
            candidate_cases = candidate["case_set"]
            new_style_cases = covered_cases["汽车风格"]
            new_type_cases = covered_cases["汽车车型"]
            if schema_type == "汽车风格":
                new_style_cases = new_style_cases | candidate_cases
            else:
                new_type_cases = new_type_cases | candidate_cases

            new_pass_cases = new_style_cases & new_type_cases
            marginal_pass_count = len(new_pass_cases - current_pass_cases)
            marginal_type_coverage = len(candidate_cases - covered_cases[schema_type])

            # 第一目标是增加同时命中风格和车型的 case。分数相同时，
            # 再比较当前类别的新增覆盖、高频程度和输入文件中的原始排名。
            score = (
                marginal_pass_count,
                marginal_type_coverage,
                len(candidate_cases),
                -candidate["rank"],
            )
            if best_score is None or score > best_score:
                best_score = score
                best_candidate = candidate

        if best_candidate is None:
            break

        selected_schemas.append(best_candidate["schema"])
        selected_schema_set.add(best_candidate["schema"])
        covered_cases[best_candidate["schema_type"]].update(best_candidate["case_set"])

    if len(selected_schemas) != TOP_N_SCHEMA:
        print(f"只获取到 {len(selected_schemas)} 个有效 schema，目标为 {TOP_N_SCHEMA} 个")

    selected_pass_count = len(covered_cases["汽车风格"] & covered_cases["汽车车型"])
    print(selected_schemas)
    print(f"selected schema count = {len(selected_schemas)}")
    print(f"selected schema covered pass cases = {selected_pass_count}")
    return selected_schemas


def get_pass_rate(flag):
    top_n_schemas = []
    if flag:
        with open(input_schemas, "r" , encoding='utf-8') as f:
            schemas = json.load(f)[:TOP_N_SCHEMA]
            for schema in schemas:
                top_n_schemas.append(schema['schema'])
    else:
        top_n_schemas = get_top_n_schema()

    with open(input_path_schemas, "r" , encoding='utf-8') as f:
        path_schemas = json.load(f)[1:]

    outpus = []
    outpus.append({
        "TOP_N_SCHEMA": TOP_N_SCHEMA,
        "SCHEMAS": top_n_schemas
    })
    valid_count = 0

    for case in path_schemas:
        hit_path_schemas = case['hit_path_schemas']
        hit_schema = []
        hit_count = 0
        style_count = 0
        type_count = 0
        for top_n_schema in top_n_schemas:
            if top_n_schema in hit_path_schemas:
                hit_schema.append(top_n_schema)
                hit_count += 1
                values = str(top_n_schema).strip().split(' <- ')
                if values[-1] == '汽车风格':
                    style_count += 1
                if values[-1] == '汽车车型':
                    type_count += 1


        if hit_count > 0 and style_count > 0 and type_count > 0:
            valid_count += 1


        outpus.append({
            "id" : case['id'],
            "keywords" : case['keywords'],
            "schemas" : hit_path_schemas,
            "valid_schemas" :hit_schema,
            "valid_count" : hit_count,
            "style_count" : style_count,
            "type_count" : type_count,
            "is_pass" : hit_count > 0 and style_count > 0 and type_count > 0,
        })

    outpus.append({
        "pass_count" : valid_count,
        "pass_rate" : valid_count / len(path_schemas),
    })

    with open(output_json, 'w', encoding='utf-8') as f:
        json.dump(outpus, f, ensure_ascii=False, indent=2)

    print(f"pass rate = {valid_count}%", )

if __name__ == "__main__":
    get_pass_rate(flag=False)
