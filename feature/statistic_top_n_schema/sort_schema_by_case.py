import json
from collections import defaultdict
#根据schema命中case进行排序
json_file = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/path_schemas_500_reversed.json"
output_file = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/sort_schemas_by_case_500.json"
def sort_schema(json_file ,output_file):
    with open(json_file , encoding='utf-8') as f:
        result = json.load(f)
    result = result[1:]
    schema_dict = {}
    schema_case_set = defaultdict(set)
    for record in result:
        schemas = record["hit_path_schemas"]
        for schema in schemas:
            schema = str(schema)
            values = schema.strip().split(' <- ')
            if values[-1] not in ('汽车风格' , '汽车车型'):
                continue
            schema_dict[str(schema)] = schema_dict.get(str(schema), 0) + 1
            schema_case_set[schema].add(record['id'])

    schema_list = [{"schema" : key , "count" : value , "case" : list(schema_case_set[key])} for key, value in schema_dict.items()]
    print(schema_list)
    sorted_schema_list = sorted(schema_list, key=lambda x: x['count'], reverse=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(sorted_schema_list, f, ensure_ascii=False, indent=2)



if __name__ == '__main__':
    sort_schema(json_file , output_file)
