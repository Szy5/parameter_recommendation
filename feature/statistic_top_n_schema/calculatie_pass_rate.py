import json

TOP_N_SCHEMA = 1

input_schemas = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/sort_schemas.json"
input_path_schemas = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/path_schemas_reversed.json"
output_json = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/pass_rate.json"

def get_pass_rate():
    with open(input_schemas, "r" , encoding='utf-8') as f:
        schemas = json.load(f)[:TOP_N_SCHEMA]
    top_n_schemas = []
    for schema in schemas:
        top_n_schemas.append(schema['schema'])

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
    get_pass_rate()

