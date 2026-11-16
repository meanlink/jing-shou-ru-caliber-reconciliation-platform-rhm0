"""Safe, dimension-aware aggregate metric DSL and source-ledger reconciliation."""
import ast
from datetime import date, timedelta
import math
import random


MONEY = {"gross", "refunds", "discount", "recognized_net"}
FIELDS = MONEY | {"visits"}
FINAL_UNITS = ("CNY", "CNY/visit")
DEFAULT_EXPRESSION = "sum(gross)-sum(refunds)-sum(discount)"
PER_VISIT = "ratio(sum(gross)-sum(refunds)-sum(discount),sum(visits))"


def metadata():
    return {
        "name": "净收入口径对账台", "short": "metric-contract", "tagline": "给口径加上单位与分母约束，再把源账差额追到渠道",
        "client": "模拟客户：星禾数字内容财务与经营分析组，4 名财务、3 名数据分析师。",
        "workflow": "渠道日报使用各自的净收入公式，财务每周手工对照账面认可额，经营分析再拼成例会报表。",
        "pains": ["退款和折扣的扣除时点不一致", "人均指标先取平均造成分母偏差", "自由公式缺少字段白名单、单位和零分母校验"],
        "upstream": "渠道日汇总账和财务认可净额（模拟数据，统一CNY）", "downstream": "财务口径复核清单与经营周报。",
        "entities": ["源账切片", "口径对账运行", "财务分发计划"], "dataset_name": "星禾_渠道日汇总_净收入口径.csv",
        "columns": [{"key": "day", "label": "日期", "type": "string"}, {"key": "channel", "label": "渠道", "type": "string"}, {"key": "currency", "label": "币种", "type": "string"}, {"key": "gross", "label": "含折扣前收入", "type": "number"}, {"key": "refunds", "label": "退款", "type": "number"}, {"key": "discount", "label": "折扣", "type": "number"}, {"key": "recognized_net", "label": "源账认可净额", "type": "number"}, {"key": "visits", "label": "有效访问", "type": "number"}],
        "parameters": [{"key": "expression", "label": "指标DSL", "type": "select", "default": DEFAULT_EXPRESSION, "options": [{"value": DEFAULT_EXPRESSION, "label": "净收入 = 毛额−退款−折扣"}, {"value": PER_VISIT, "label": "单次访问净收入 = 净收入/访问量"}]}, {"key": "group_by", "label": "对账维度", "type": "select", "default": "channel", "options": [{"value": "channel", "label": "按渠道"}, {"value": "week", "label": "按自然周"}]}, {"key": "zero_policy", "label": "零分母策略", "type": "select", "default": "reject", "options": [{"value": "reject", "label": "阻止计算并修复"}, {"value": "zero", "label": "明确记0并警告"}]}],
        "workbench_title": "口径契约与维度对账", "action_label": "校验口径并执行对账", "unit": "CNY", "accent": "oklch(46% 0.08 75)", "style": "finance-ledger",
        "seed_story": ["08:30 上传星禾_渠道日汇总_净收入口径.csv，共60天×3渠道180行。", "08:45 确认所有金额已统一为CNY，gross为扣折扣前金额。", "09:00 选择净收入DSL，按渠道汇总。", "09:10 比较源账认可净额与统一口径差额。", "09:30 追到合作渠道额外扣减的服务费，保留复核证据。", "10:00 切换单次访问净收入，使用总净额除以总访问量。", "15:00 输出差异报表并设置财务例会本地分发计划。"],
        "method": "只解析AST白名单：sum(已注册字段)、ratio、ref(口径代码)、四则运算和数值常量；不执行eval。口径以稳定代码引用子口径，草稿可联动试算；发布时整条依赖固定到具体版本。加减必须同单位，乘法仅允许无量纲比例，分母为0依策略拒绝或显式记0。先汇总分子分母再计算比率。",
        "limitations": ["仅支持统一币种的日汇总账，不连接真实财务账或自动记账。", "差额用于发现口径或源账差异，不代表审计确认结论；净额不包含税费与汇兑。", "口径发布后固定整条依赖的版本；子口径再修改只影响新发布与新任务，不改写历史结果。"],
    }


def seed_records():
    rng = random.Random(1018)
    rows = []
    start = date(2026, 9, 1)
    for index in range(60):
        for channel, base in (("直营内容", 18500), ("机构许可", 11600), ("合作渠道", 14200)):
            gross = round(base * rng.uniform(0.78, 1.25), 2)
            refunds = round(gross * rng.uniform(0.015, 0.055), 2)
            discount = round(gross * rng.uniform(0.04, 0.12), 2)
            service_fee = round(gross * 0.018, 2) if channel == "合作渠道" else 0
            rows.append({"day": str(start + timedelta(days=index)), "channel": channel, "currency": "CNY", "gross": gross, "refunds": refunds, "discount": discount, "recognized_net": round(gross-refunds-discount-service_fee, 2), "visits": rng.randint(180, 800)})
    return rows


def _parse(expression):
    if not isinstance(expression, str) or not 1 <= len(expression) <= 300:
        raise ValueError("DSL须为1至300字的公式")
    try:
        tree = ast.parse(expression, mode="eval")
    except (SyntaxError, ValueError):
        raise ValueError("指标DSL语法错误") from None
    if len(list(ast.walk(tree))) > 60:
        raise ValueError("指标DSL过于复杂")
    return tree.body


def parse_expression(expression):
    """Public parser used by the metric registry; keeps evaluation pure AST."""
    return _parse(expression)


def _ref_code(node):
    if len(node.args) != 1 or node.keywords:
        raise ValueError("ref() 只接受一个口径代码，例如 ref(service_fee)")
    argument = node.args[0]
    if isinstance(argument, ast.Name):
        return argument.id
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        code = argument.value.strip()
        if code:
            return code
    raise ValueError("ref() 只接受口径代码，例如 ref(service_fee)")


def ref_codes(tree):
    """Return referenced metric codes in first-appearance order."""
    codes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "ref":
            code = _ref_code(node)
            if code not in codes:
                codes.append(code)
    return codes


def _combined_unit(left, right):
    return "scalar" if left == right else left if right == "scalar" else f"{left}/{right}"


def infer_unit(tree, units):
    """Static unit inference for a draft. `units` maps directly referenced codes to units."""
    if isinstance(tree, ast.Constant) and type(tree.value) in (int, float):
        if not math.isfinite(float(tree.value)):
            raise ValueError("DSL常量必须为有限数值")
        return "scalar"
    if isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name) and not tree.keywords:
        if tree.func.id == "sum" and len(tree.args) == 1 and isinstance(tree.args[0], ast.Name):
            field = tree.args[0].id
            if field not in FIELDS:
                raise ValueError(f"未注册的指标字段：{field}")
            return "CNY" if field in MONEY else "visit"
        if tree.func.id == "ratio" and len(tree.args) == 2:
            return _combined_unit(infer_unit(tree.args[0], units), infer_unit(tree.args[1], units))
        if tree.func.id == "ref":
            code = _ref_code(tree)
            if code not in units:
                raise ValueError(f"引用口径 {code} 缺少可用的已发布版本，无法校验单位")
            return units[code]
        raise ValueError("DSL只允许sum(注册字段)、ratio(分子,分母)与ref(口径代码)")
    if isinstance(tree, ast.UnaryOp) and isinstance(tree.op, (ast.UAdd, ast.USub)):
        return infer_unit(tree.operand, units)
    if isinstance(tree, ast.BinOp):
        left = infer_unit(tree.left, units)
        right = infer_unit(tree.right, units)
        if isinstance(tree.op, (ast.Add, ast.Sub)):
            if left != right:
                raise ValueError(f"口径单位不一致：{left}不能与{right}加减")
            return left
        if isinstance(tree.op, ast.Div):
            return _combined_unit(left, right)
        if isinstance(tree.op, ast.Mult):
            if left != "scalar" and right != "scalar":
                raise ValueError("乘法只能用于一个无量纲比例，禁止金额×访问数")
            return right if left == "scalar" else left
    raise ValueError("DSL包含未允许的结构；禁止属性、索引、外部函数和代码执行")


def _evaluate(node, rows, zero_policy, warnings, context=None, node_key=None, stack=()):
    if isinstance(node, ast.Constant) and type(node.value) in (int, float):
        value = float(node.value)
        if not math.isfinite(value):
            raise ValueError("DSL常量必须为有限数值")
        return value, "scalar"
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        if node.func.id == "sum" and len(node.args) == 1 and isinstance(node.args[0], ast.Name):
            field = node.args[0].id
            if field not in FIELDS:
                raise ValueError(f"未注册的指标字段：{field}")
            return sum(row[field] for row in rows), "CNY" if field in MONEY else "visit"
        if node.func.id == "ratio" and len(node.args) == 2:
            return _divide(_evaluate(node.args[0], rows, zero_policy, warnings, context, node_key, stack), _evaluate(node.args[1], rows, zero_policy, warnings, context, node_key, stack), zero_policy, warnings)
        if node.func.id == "ref":
            code = _ref_code(node)
            if not context:
                raise ValueError(f"公式引用口径 {code}，但任务未绑定已发布口径版本")
            current = (context.get("nodes") or {}).get(node_key)
            if not current:
                raise ValueError("口径版本上下文缺失，无法解析引用")
            child_key = (current.get("deps") or {}).get(code)
            child = (context.get("nodes") or {}).get(child_key)
            if not child:
                raise ValueError(f"引用口径 {code} 未绑定到具体版本")
            if child_key in stack:
                chain = " → ".join((context.get("nodes") or {}).get(key, {}).get("code", str(key)) for key in stack + (child_key,))
                raise ValueError(f"口径引用形成循环：{chain}")
            value, unit = _evaluate(_parse(child["expression"]), rows, zero_policy, warnings, context, child_key, stack + (child_key,))
            if child.get("unit") not in (None, unit):
                raise ValueError(f"引用口径 {code} 的版本单位与公式不一致，请重新发布该口径")
            return value, child.get("unit") or unit
        raise ValueError("DSL只允许sum(注册字段)、ratio(分子,分母)与ref(口径代码)")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value, unit = _evaluate(node.operand, rows, zero_policy, warnings, context, node_key, stack)
        return (-value if isinstance(node.op, ast.USub) else value), unit
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left, rows, zero_policy, warnings, context, node_key, stack)
        right = _evaluate(node.right, rows, zero_policy, warnings, context, node_key, stack)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left[1] != right[1]:
                raise ValueError(f"口径单位不一致：{left[1]}不能与{right[1]}加减")
            return (left[0] + right[0] if isinstance(node.op, ast.Add) else left[0] - right[0]), left[1]
        if isinstance(node.op, ast.Div):
            return _divide(left, right, zero_policy, warnings)
        if isinstance(node.op, ast.Mult):
            if left[1] != "scalar" and right[1] != "scalar":
                raise ValueError("乘法只能用于一个无量纲比例，禁止金额×访问数")
            return left[0] * right[0], right[1] if left[1] == "scalar" else left[1]
    raise ValueError("DSL包含未允许的结构；禁止属性、索引、外部函数和代码执行")


def _divide(left, right, policy, warnings):
    if right[0] == 0:
        if policy == "reject":
            raise ValueError("指标分母为0，请修复源账或明确选择记0策略")
        warnings.append("至少一个分组分母为0；按显式策略记0，不能据此判断经营表现。")
        value = 0.0
    else:
        value = left[0] / right[0]
    return value, _combined_unit(left[1], right[1])


def analyze(records, params, metric=None):
    if not isinstance(records, list) or not records:
        raise ValueError("请先导入渠道日汇总账")
    grouping = params.get("group_by", "channel")
    zero_policy = params.get("zero_policy", "reject")
    if grouping not in ("channel", "week") or zero_policy not in ("reject", "zero"):
        raise ValueError("未知的分组或零分母策略")
    if metric:
        expression = metric.get("expression") or DEFAULT_EXPRESSION
        context, root_key = metric, metric.get("root")
    else:
        expression = params.get("expression", DEFAULT_EXPRESSION)
        context, root_key = None, None
    tree = _parse(expression)
    rows = []
    currencies, natural_keys = set(), set()
    for record in records:
        try:
            day = date.fromisoformat(str(record["day"]))
            channel = str(record["channel"]).strip()
            currency = str(record["currency"])
            values = {key: float(record[key]) for key in FIELDS}
        except (KeyError, TypeError, ValueError):
            raise ValueError("源账缺少日期、渠道、币种或注册数值字段") from None
        if not channel or not all(math.isfinite(value) and 0 <= value <= 1_000_000_000 for value in values.values()):
            raise ValueError("渠道不能为空，单行金额和访问数必须为0至10亿之间的有限数值")
        if not values["visits"].is_integer():
            raise ValueError("访问数必须为整数，不能提交已经平均化的分母")
        key = (day, channel)
        if key in natural_keys:
            raise ValueError("渠道日自然键重复，请先完成源账去重")
        natural_keys.add(key)
        currencies.add(currency)
        values.update({"day": day, "channel": channel})
        rows.append(values)
    if currencies != {"CNY"}:
        raise ValueError("当前口径只接受统一CNY，混合或其他币种请先换算")
    warnings = []
    stack = (root_key,) if root_key else ()
    total, unit = _evaluate(tree, rows, zero_policy, warnings, context, root_key, stack)
    if unit not in FINAL_UNITS:
        raise ValueError("当前对账契约只接受净收入CNY或单次访问净收入CNY/visit")
    groups = {}
    for row in rows:
        if grouping == "channel":
            key = row["channel"]
        else:
            year, week, _ = row["day"].isocalendar()
            key = f"{year}-W{week:02}"
        groups.setdefault(key, []).append(row)
    output = []
    for key, group in sorted(groups.items()):
        value, group_unit = _evaluate(tree, group, zero_policy, warnings, context, root_key, stack)
        if group_unit != unit:
            raise ValueError("分组间口径单位不一致")
        source = sum(row["recognized_net"] for row in group)
        if unit == "CNY/visit":
            source = _divide((source, "CNY"), (sum(row["visits"] for row in group), "visit"), zero_policy, warnings)[0]
        output.append({"dimension": key, "computed": round(value, 4), "source": round(source, 4), "difference": round(value-source, 4), "visits": int(sum(row["visits"] for row in group))})
    source_total = sum(row["recognized_net"] for row in rows)
    if unit == "CNY/visit":
        source_total = _divide((source_total, "CNY"), (sum(row["visits"] for row in rows), "visit"), zero_policy, warnings)[0]
    if not all(math.isfinite(value) for value in (total, source_total)):
        raise ValueError("指标结果非有限值，请缩小数值范围或修复口径")
    discrepancy = total - source_total
    findings = [f"执行公式：{expression}；单位契约：{unit}；零分母策略：{zero_policy}。", "差额仅表示所选口径与源账认可值不一致，须回查原账。模拟种子的合作渠道额外扣减1.8%服务费，用于展示一种可能原因。", "比率按汇总分子/汇总分母计算，汇总行不取各渠道比率的简单平均。"]
    if metric and metric.get("version"):
        version = metric["version"]
        metric_info = metric.get("metric") or {}
        findings.insert(0, f"绑定口径：{metric_info.get('name', '')}（{metric_info.get('code', '')}）v{version.get('version_no', '?')}；版本 ID {version.get('version_id')}；公式：{expression}。")
        dependencies = metric.get("dependencies") or []
        if dependencies:
            findings.insert(1, "固定依赖版本：" + "；".join(f"{item['name']}（{item['code']}）v{item['version_no']}" for item in dependencies) + "。")
    return {
        "summary": f"按{'渠道' if grouping=='channel' else '自然周'}对账{len(rows)}行；统一口径{total:,.4f}{unit}，与源账认可值差额{discrepancy:+,.4f}{unit}。",
        "metrics": [{"label": "统一口径", "value": round(total, 4), "unit": unit}, {"label": "源账认可", "value": round(source_total, 4), "unit": unit}, {"label": "口径差额", "value": round(discrepancy, 4), "unit": unit}, {"label": "差异分组", "value": sum(abs(row["difference"]) > 0.01 for row in output), "unit": "组"}],
        "table": {"columns": [{"key": "dimension", "label": "对账维度"}, {"key": "computed", "label": "统一口径值"}, {"key": "source", "label": "源账认可值"}, {"key": "difference", "label": "差额"}, {"key": "visits", "label": "访问分母"}], "rows": output},
        "chart": {"type": "bar", "labels": [row["dimension"] for row in output], "series": [{"name": "统一口径", "values": [row["computed"] for row in output]}, {"name": "源账认可", "values": [row["source"] for row in output]}]},
        "findings": findings,
        "method": metadata()["method"], "warnings": list(dict.fromkeys(warnings)),
    }
