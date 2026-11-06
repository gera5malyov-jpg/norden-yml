"""Conservative TN VED suggestions for Ozon, based on item construction.

A suggestion is never sent unless the identical code already occurs in
a filled item from the same Ozon category/type. Uncertain goods return None.
"""
import re


def _value(item, aid):
    for a in item.get("attributes") or []:
        if int(a.get("id") or 0) == aid:
            return " ".join(str(v.get("value") or "") for v in a.get("values") or []).lower().replace("ё", "е")
    return ""


def suggest(item):
    group = (int(item.get("description_category_id") or 0), int(item.get("type_id") or 0))
    name = str(item.get("name") or "").lower().replace("ё", "е")
    frame = _value(item, 6656)
    uphol = _value(item, 6654)
    top = _value(item, 6688)
    base = _value(item, 6689)
    rack_material = _value(item, 6383)

    def wood():
        return any(s in frame for s in ("дерево", "массив", "фанера", "мдф", "дсп"))
    def metal():
        return "металл" in frame or "сталь" in frame
    def padded():
        if "без обивки" in uphol: return False
        if uphol: return True
        if re.search(r"жестк|тверд(ый|ое|ая)|без обивки", name): return False
        if re.search(r"ткан|кожзам|кожа|вел(ь|ю)р|экокож|к/з|santa|v-?\d|verona|орегон|анис|agat|soro|velutto|agata|м02|м07|м08|синтепон|поролон|кожан|velvet|patchwork|\bpu\b|soft", name): return True
        return None

    u = padded()
    if group in ((80375615, 95030), (80375615, 95035)):
        if wood() and (u is True or ("качалк" in name and uphol != "без обивки")): return "9401610000"
        return None
    if group == (80375615, 95024):
        if wood() and u is True and not re.search("трансформ|расклад|кровать", name): return "9401610000"
        return None
    if group in ((17027916, 95048), (17027916, 95023), (17027916, 95053), (17027916, 95044)):
        if wood() and u is True: return "9401610000"
        if wood() and u is False: return "9401690000"
        if metal() and u is True: return "9401710009"
        if metal() and u is False: return "9401790009"
        if "пластик" in frame and u is False: return "9401800009"
        return None
    if group == (79164512, 95041):
        if re.search("гостев|посетител|приемн|переговорн|конференц|полозья|ожидан|изо", name):
            return "9401710009" if not "дерево" in frame else None
        if re.search("кресл|стул", name):
            if _value(item, 10344) == "нет" and "на колес" not in _value(item, 10131): return None
            if "массив" in frame: return None
            return "9401390000"
        return None
    if group == (17027908, 93670):
        if "дерев" in rack_material and "металл" not in rack_material: return "9403609009"
        if "металл" in rack_material: return "9403208009"
        if re.search("металл|сталь|хром|пластик.?металл|алюмин|труб|хромир|ножк", name): return "9403208009"
        return None
    if group == (17028726, 96029):
        return "9403609009" if "ширма" in name else None
    if group in ((17027918, 95015), (17027918, 95002), (17027918, 95006), (17027918, 95101)):
        if "металл" in base: return "9403208009"
        if any(x in base for x in ("дерево", "массив", "мдф", "дсп")): return "9403601009"
        if any(x in top for x in ("дерево", "мдф", "дсп", "шпон")) and "металл" not in name: return "9403601009"
        if re.search("металл|сталь|хром|подстолье", name): return "9403208009"
        return None
    if group == (80731485, 95098):
        if re.search("лдсп|дсп|мдф|дерево|орех|дуб", name): return "9403609009"
        if re.search("металл|сталь", name): return "9403208009"
        return None
    if group[0] == 17027917 and group[1] in (94995, 970615714, 970713329):
        if "кровать" in name and re.search("массив|дерев|дсп|лдсп|дуб|белый|венец", name): return "9403500009"
        if "кровать" in name and re.search("металл|сталь", name): return "9403202009"
        return None
    if group in ((17027919, 971161297), (80711064, 971162059), (80711064, 95086)):
        if re.search("металл|сталь", name): return "9403208009"
        if re.search("прикроват|кроват|спальн", name): return "9403500009"
        if re.search("дуб|лдсп|мдф|дерев|вяз|орех|ясень|белый", name): return "9403601009"
        return None
    if group == (17027919, 970679785):
        if "шкаф" in name and "белый" in name: return "9403500009"
    if group == (79164512, 970685072):
        if "кресло" in name: return "9401390000"
    if group == (80770870, 95090):
        if re.search("прихож|дуб|лдсп", name): return "9403609009"
    if group == (17029021, 93292): return "8304000000"
    if group == (86491609, 970793547): return "8716800000"
    return None
