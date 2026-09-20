from kubernetes import client

SCALARS = {"str": str, "int": int, "bool": bool}


def _model(name: str):
    model = getattr(client, name, None)
    return model if hasattr(model, "openapi_types") else None


def problems(data, type_name: str, path: str = "") -> list[str]:
    if type_name.startswith("list["):
        inner = type_name[5:-1]
        if not isinstance(data, list):
            return [f"{path}: expected list"]
        return [p for i, item in enumerate(data) for p in problems(item, inner, f"{path}[{i}]")]

    if type_name.startswith("dict("):
        inner = type_name.split(", ", 1)[1][:-1]
        if not isinstance(data, dict):
            return [f"{path}: expected object"]
        return [p for k, v in data.items() for p in problems(v, inner, f"{path}.{k}")]

    if type_name in SCALARS:
        expected = SCALARS[type_name]
        ok = isinstance(data, expected) and not (expected is int and isinstance(data, bool))
        return [] if ok else [f"{path}: expected {type_name}, got {type(data).__name__}"]

    model = _model(type_name)
    if model is None:
        return []
    if not isinstance(data, dict):
        return [f"{path}: expected object for {type_name}"]

    json_to_attr = {json: attr for attr, json in model.attribute_map.items()}
    found = []
    for key, value in data.items():
        if key not in json_to_attr:
            found.append(f"{path}.{key}: unknown field for {type_name}")
            continue
        found += problems(value, model.openapi_types[json_to_attr[key]], f"{path}.{key}")
    return found
