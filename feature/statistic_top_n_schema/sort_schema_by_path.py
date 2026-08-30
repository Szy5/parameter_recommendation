import json
#根据schema命中case进行排序
json_file = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/path_schemas_reversed.json"
output_file = "/Users/jiangzifeng/PycharmProjects/parameter_recommendation/data/sort_schemas_by_path.json"
def sort_schema(json_file ,output_file):
    with open(json_file , encoding='utf-8') as f:
        result = json.load(f)
    schema_dict = result[0]['total_path_schemas']
    del_schema = []
    for schema, _ in schema_dict.items():
        schema = str(schema)
        values = schema.strip().split(' <- ')
        if values[-1] not in ('汽车风格', '汽车车型'):
            del_schema.append(schema)
    for schema in del_schema:
        del schema_dict[schema]

    sorted_items = sorted(schema_dict.items(), key=lambda x: x[1], reverse=True)


    schema_list = [{"schema" : key , "count" : value} for key, value in sorted_items]
    print(schema_list)
    sorted_schema_list = sorted(schema_list, key=lambda x: x['count'], reverse=True)
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(sorted_schema_list, f, ensure_ascii=False, indent=2)



if __name__ == '__main__':
    sort_schema(json_file , output_file)
