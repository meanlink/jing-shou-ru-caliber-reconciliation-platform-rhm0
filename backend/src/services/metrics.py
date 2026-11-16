"""Versioned metric registry: drafts, references, trial runs and publishing.

Semantics
---------
* A metric is referenced by its stable ``code`` through ``ref(code)`` in the DSL.
* Every metric owns one editable ``MetricDraft``. Drafts may reference other
  metrics; a trial run follows the *linked* draft chain (a referenced metric's
  unpublished draft is used when it has one, otherwise its published version).
* Publishing freezes an immutable ``MetricVersion``. Its ``resolved`` snapshot
  pins every transitive dependency to an exact published version id, so later
  child edits never change an already published metric.
* The release gate walks the draft-aware graph: cycles, missing references and
  unit conflicts are rejected with the full dependency chain.
* Publication is serialized (DB row locks plus an in-process lock) and the gate
  is re-run inside the critical section, so two people publishing drafts that
  reference each other cannot both pass and commit a cycle.
"""
import re
import threading
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.models import MetricDefinition, MetricDraft, MetricVersion
from src.services.business import infer_unit, parse_expression, ref_codes

CODE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_IN_PROCESS_LOCK = threading.Lock()


class MetricError(Exception):
    """Domain error carrying a machine code, HTTP status and dependency chain."""

    def __init__(self, message, code, chain=None, status=422):
        super().__init__(message)
        self.message = message
        self.code = code
        self.chain = list(chain or [])
        self.status = status

    def payload(self):
        return {
            "detail": self.message,
            "code": self.code,
            "chain": self.chain,
            "chain_text": " → ".join(self.chain),
        }


def _chain_text(chain):
    return " → ".join(chain) if chain else "（当前公式）"


def _parse_or_error(expression):
    try:
        return parse_expression(expression)
    except ValueError as exc:
        raise MetricError(str(exc), "invalid_expression") from None


def get_metric(db, metric_id):
    """Load a metric or raise the chain-carrying not_found error."""
    metric = db.get(MetricDefinition, metric_id)
    if not metric:
        raise MetricError("口径不存在", "not_found", status=404)
    return metric


def get_by_code(db, code):
    return db.scalar(select(MetricDefinition).where(MetricDefinition.code == code))


def _draft(db, metric):
    return db.scalar(select(MetricDraft).where(MetricDraft.metric_id == metric.id))


def current_version(db, metric):
    if not metric.current_version_id:
        return None
    return db.get(MetricVersion, metric.current_version_id)


def _is_dirty(draft, version):
    if not draft:
        return False
    return not version or draft.expression != version.expression


def _version_of(db, metric):
    return current_version(db, metric)


def _node(metric, expression, key, path, version=None):
    return {
        "key": key,
        "metric_id": metric.id,
        "code": metric.code,
        "name": metric.name,
        "expression": expression,
        "version_id": version.id if version else None,
        "version_no": version.version_no if version else None,
        "deps": {},
        "unit": None,
        "path": list(path),
    }


def _check_reference_graph(db, metric, expression):
    """Cycle / missing-reference check over drafts (falling back to published).

    Units are checked separately against the exact published versions that will
    be pinned, so an unrelated draft conflict does not block a safe release.
    """
    done = set()

    def walk(current, current_expression, path_metrics, path_codes):
        if current.id in done:
            return
        tree = _parse_or_error(current_expression)
        for code in ref_codes(tree):
            child = get_by_code(db, code)
            chain = path_codes + [code]
            if not child:
                raise MetricError(
                    f"引用口径 {code} 不存在；依赖链：{_chain_text(chain)}",
                    "missing_reference", chain,
                )
            if child.id in path_metrics:
                start = path_metrics.index(child.id)
                cycle = path_codes[start:] + [child.code]
                raise MetricError(
                    f"口径引用形成循环：{_chain_text(cycle)}；请拆开循环后再发布",
                    "circular_dependency", cycle, status=409,
                )
            child_version = _version_of(db, child)
            child_draft = _draft(db, child)
            if _is_dirty(child_draft, child_version) and child_draft and child_draft.expression:
                child_expression = child_draft.expression
            elif child_version:
                child_expression = child_version.expression
            elif child_draft and child_draft.expression:
                child_expression = child_draft.expression
            else:
                raise MetricError(
                    f"引用口径 {child.name}（{code}）既无草稿也无已发布版本；依赖链：{_chain_text(chain)}",
                    "missing_reference", chain,
                )
            walk(child, child_expression, path_metrics + [current.id], chain)
        done.add(current.id)

    walk(metric, expression, [metric.id], [metric.code])


def _resolve_pending(db, metric, root_expression):
    """Draft-aware graph: dirty referenced drafts are followed, published ones pinned.

    Returns ``(nodes, root_key)``. Used for trial runs and as the publish gate.
    """
    nodes = {}
    root_key = f"draft:{metric.id}"
    root_tree = _parse_or_error(root_expression)

    def visit(current, expression, key, path_metrics, path_codes, pinned=None):
        if key in nodes:
            return key
        node = _node(current, expression, key, path_codes, pinned)
        nodes[key] = node
        tree = _parse_or_error(expression)
        units = {}
        for code in ref_codes(tree):
            child = get_by_code(db, code)
            if not child:
                chain = path_codes + [code]
                raise MetricError(
                    f"引用口径 {code} 不存在；依赖链：{_chain_text(chain)}",
                    "missing_reference", chain,
                )
            if child.id in path_metrics:
                start = path_metrics.index(child.id)
                chain = path_codes[start:] + [child.code]
                raise MetricError(
                    f"口径引用形成循环：{_chain_text(chain)}；请拆开循环后再发布",
                    "circular_dependency", chain, status=409,
                )
            child_version = _version_of(db, child)
            child_draft = _draft(db, child)
            if _is_dirty(child_draft, child_version):
                child_key = f"draft:{child.id}"
                child_expression = child_draft.expression
            elif child_version:
                child_key = f"version:{child_version.id}"
                child_expression = child_version.expression
            else:
                chain = path_codes + [code]
                raise MetricError(
                    f"引用口径 {child.name}（{code}）既无草稿也无已发布版本；依赖链：{_chain_text(chain)}",
                    "missing_reference", chain,
                )
            child_path = path_metrics + [current.id]
            pinned = child_version if child_key.startswith("version:") else None
            visit(child, child_expression, child_key, child_path, path_codes + [child.code], pinned)
            if child_key in nodes and nodes[child_key]["unit"] is None:
                raise MetricError(
                    f"口径 {child.name}（{child.code}）的单位无法确定；依赖链：{_chain_text(path_codes + [child.code])}",
                    "unit_conflict", path_codes + [child.code],
                )
            node["deps"][code] = child_key
            units[code] = nodes[child_key]["unit"]
        try:
            node["unit"] = infer_unit(tree, units)
        except ValueError as exc:
            message = str(exc)
            code = "unit_conflict" if ("单位" in message or "乘法" in message) else "invalid_expression"
            chain = path_codes or [current.code]
            raise MetricError(
                f"口径 {current.name}（{current.code}）校验失败：{message}；依赖链：{_chain_text(chain)}",
                code, chain,
            ) from None
        if pinned is not None and pinned.unit != node["unit"]:
            raise MetricError(
                f"引用口径 {current.name}（{current.code}）v{pinned.version_no} 记录的发布单位 {pinned.unit} 与公式推导 {node['unit']} 冲突；依赖链：{_chain_text(path_codes)}",
                "unit_conflict", path_codes,
            )
        return key

    visit(metric, root_expression, root_key, [metric.id], [metric.code])
    _ = root_tree
    return nodes, root_key


def _referenced_dirty_drafts(db, expression):
    """Referenced metrics whose unpublished draft is ignored by this release."""
    warnings, seen = [], set()

    def walk(expression):
        tree = _parse_or_error(expression)
        for code in ref_codes(tree):
            child = get_by_code(db, code)
            if not child or child.id in seen:
                continue
            seen.add(child.id)
            version = _version_of(db, child)
            child_draft = _draft(db, child)
            if _is_dirty(child_draft, version):
                warnings.append({
                    "code": child.code, "name": child.name,
                    "published_version_no": version.version_no if version else None,
                    "draft_revision": child_draft.revision if child_draft else None,
                    "message": f"{child.name}（{child.code}）存在未发布草稿，本次发布固定其已发布版本"
                               + (f" v{version.version_no}" if version else "（无）"),
                })
            if version:
                walk(version.expression)

    walk(expression)
    return warnings


def _dependency_list(nodes, root_key):
    items = []
    for key, node in nodes.items():
        if key == root_key or not node.get("version_id"):
            continue
        items.append({
            "metric_id": node["metric_id"],
            "code": node["code"],
            "name": node["name"],
            "version_id": node["version_id"],
            "version_no": node["version_no"],
            "expression": node["expression"],
            "unit": node["unit"],
        })
    return sorted(items, key=lambda item: (item["metric_id"], item["version_id"]))


def _chain(nodes, root_key):
    seen, order = set(), []

    def walk(key):
        node = nodes.get(key)
        if not node or key in seen:
            return
        seen.add(key)
        order.append({
            "key": key,
            "metric_id": node["metric_id"],
            "code": node["code"],
            "name": node["name"],
            "version_id": node.get("version_id"),
            "version_no": node.get("version_no"),
            "expression": node.get("expression"),
            "unit": node.get("unit"),
            "depth": len(node.get("path") or []),
            "is_draft": str(key).startswith("draft:"),
        })
        for child in (node.get("deps") or {}).values():
            walk(child)

    walk(root_key)
    return order


def _resolve_frozen(db, metric, expression):
    """Published-only graph used to freeze a version. Every ref must have a version.

    Only immutable published versions are composed, so a cycle cannot be created
    by two concurrent draft releases: a new version may point at versions that
    already exist, and those never point back at it.
    """
    nodes = {}
    root_key = f"pending:{metric.id}"
    _parse_or_error(expression)
    in_progress = []

    def visit(current, current_expression, key, path_codes, version=None):
        if key in in_progress:
            start = in_progress.index(key)
            cycle = path_codes[start:] + [current.code]
            raise MetricError(
                f"已发布版本之间存在循环引用：{_chain_text(cycle)}",
                "circular_dependency", cycle, status=409,
            )
        if key in nodes:
            return key
        in_progress.append(key)
        node = _node(current, current_expression, key, path_codes, version)
        nodes[key] = node
        tree = _parse_or_error(current_expression)
        units = {}
        for code in ref_codes(tree):
            child = get_by_code(db, code)
            chain = path_codes + [code]
            if not child:
                raise MetricError(
                    f"引用口径 {code} 不存在；依赖链：{_chain_text(chain)}",
                    "missing_reference", chain,
                )
            child_version = _version_of(db, child)
            if not child_version:
                raise MetricError(
                    f"引用口径 {child.name}（{code}）尚无已发布版本，不能固定到本次发布；依赖链：{_chain_text(chain)}",
                    "missing_reference", chain,
                )
            child_key = f"version:{child_version.id}"
            visit(child, child_version.expression, child_key, chain, child_version)
            node["deps"][code] = child_key
            units[code] = nodes[child_key]["unit"]
        try:
            inferred = infer_unit(tree, units)
        except ValueError as exc:
            message = str(exc)
            code = "unit_conflict" if ("单位" in message or "乘法" in message) else "invalid_expression"
            # Append directly referenced codes that are not already on the path,
            # so a unit mismatch shows which dependency is involved.
            chain = list(path_codes)
            for referenced in ref_codes(tree):
                if referenced not in chain:
                    chain.append(referenced)
            raise MetricError(
                f"口径 {current.name}（{current.code}）校验失败：{message}；依赖链：{_chain_text(chain)}",
                code, chain,
            ) from None
        if version and version.unit != inferred:
            raise MetricError(
                f"口径 {current.name}（{current.code}）已发布版本 v{version.version_no} 的单位与公式不一致；依赖链：{_chain_text(path_codes)}",
                "unit_conflict", path_codes,
            )
        node["unit"] = inferred
        in_progress.remove(key)
        return key

    visit(metric, expression, root_key, [metric.code])
    return nodes, root_key


def trial_snapshot(db, metric, expression):
    _check_reference_graph(db, metric, expression)
    nodes, root_key = _resolve_pending(db, metric, expression)
    return {
        "root": root_key,
        "expression": expression,
        "nodes": nodes,
        "metric": {
            "metric_id": metric.id, "code": metric.code,
            "name": metric.name, "description": metric.description,
        },
        "version": None,
        "dependencies": _dependency_list(nodes, root_key),
        "chain": _chain(nodes, root_key),
        "unit": nodes[root_key]["unit"],
        "provisional": True,
    }


def published_snapshot(db, metric):
    version = current_version(db, metric)
    if not version:
        raise MetricError(
            f"口径 {metric.name}（{metric.code}）尚无已发布版本，无法用于分析",
            "missing_reference", [metric.code],
        )
    return snapshot_for_version(db, metric, version)


def snapshot_for_version(db, metric, version):
    """Exact snapshot of an already published version (immutable, no drift)."""
    resolved = version.resolved or {}
    nodes = resolved.get("nodes") or {}
    root_key = resolved.get("root")
    return {
        "root": root_key,
        "expression": version.expression,
        "nodes": nodes,
        "metric": {
            "metric_id": metric.id, "code": metric.code,
            "name": metric.name, "description": metric.description,
        },
        "version": {
            "version_id": version.id,
            "version_no": version.version_no,
            "expression": version.expression,
            "unit": version.unit,
            "published_at": version.published_at,
            "note": version.note,
        },
        "dependencies": version.dependencies or [],
        "chain": _chain(nodes, root_key),
        "unit": version.unit,
        "provisional": False,
    }


# ---------------------------------------------------------------------------
# Registry operations
# ---------------------------------------------------------------------------

def create(db, data, user):
    code = (data.code or "").strip()
    name = (data.name or "").strip()
    expression = (data.expression or "").strip()
    if not CODE_RE.match(code):
        raise MetricError("口径代码须为 2 至 64 位小写字母、数字或下划线，且以字母开头", "invalid_code")
    if len(name) < 2:
        raise MetricError("口径名称至少需要两个字符", "invalid_metric")
    if get_by_code(db, code):
        raise MetricError("口径代码已存在，请换一个稳定的英文代码", "duplicate_code", [code], status=409)
    _parse_or_error(expression)
    now = time.time()
    metric = MetricDefinition(
        code=code, name=name, description=(data.description or "").strip()[:500],
        status="active", created_by=user.id, created_at=now, updated_at=now,
    )
    db.add(metric)
    db.flush()
    db.add(MetricDraft(
        metric_id=metric.id, expression=expression, note=(data.note or "").strip()[:500],
        revision=1, created_by=user.id, updated_by=user.id,
        created_at=now, updated_at=now, base_version_id=None,
    ))
    db.flush()
    return metric


def update_draft(db, metric, expression, note, user, expected_revision=None):
    draft = _draft(db, metric)
    if not draft:
        raise MetricError("口径草稿不存在", "not_found", [metric.code], status=404)
    if expected_revision is not None and int(expected_revision) != draft.revision:
        raise MetricError(
            "草稿已被其他人修改，请刷新后重新编辑（revision 冲突）",
            "revision_conflict", [metric.code], status=409,
        )
    expression = (expression or "").strip()
    _parse_or_error(expression)
    draft.expression = expression
    draft.note = (note or "").strip()[:500]
    draft.revision += 1
    draft.updated_by = user.id
    draft.updated_at = time.time()
    metric.updated_at = draft.updated_at
    db.flush()
    return draft


def validate_draft(db, metric, expression=None):
    """Run the release gate without changing state. Raises MetricError with chain."""
    draft = _draft(db, metric)
    expression = (expression if expression is not None else (draft.expression if draft else "")).strip()
    if not expression:
        raise MetricError("当前口径没有公式可校验", "invalid_expression", [metric.code])
    _check_reference_graph(db, metric, expression)
    nodes, root_key = _resolve_pending(db, metric, expression)
    return {
        "nodes": nodes,
        "root": root_key,
        "unit": nodes[root_key]["unit"],
        "dependencies": _dependency_list(nodes, root_key),
        "chain": _chain(nodes, root_key),
    }


def publish(db, metric, user, note="", expected_revision=None):
    """Serialize publication, re-check the release gate and freeze a version."""
    with _IN_PROCESS_LOCK:
        try:
            metric_id = metric.id
            # Persist any pending draft/new-metric work and end the read snapshot
            # opened while rendering the request, then take the locks. On MySQL
            # the locking reads see the latest committed versions, so a
            # concurrent release cannot slip a new dependency past this gate.
            db.commit()
            if db.get_bind().dialect.name != "sqlite":
                # Lock every definition in a stable order so concurrent releases
                # (which may reference each other) are evaluated one at a time.
                db.execute(select(MetricDefinition).order_by(MetricDefinition.id).with_for_update()).scalars().all()
            metric = db.get(MetricDefinition, metric_id)
            draft = _draft(db, metric)
            if not draft:
                raise MetricError("口径草稿不存在", "not_found", [metric.code], status=404)
            if metric.status != "active":
                raise MetricError("口径已停用，请先启用再发布", "disabled", [metric.code], status=409)
            if expected_revision is not None and int(expected_revision) != draft.revision:
                raise MetricError(
                    "草稿已被其他人修改，请刷新后重新发布（revision 冲突）",
                    "revision_conflict", [metric.code], status=409,
                )

            # Gate 1: the current working graph (dirty drafts followed, otherwise
            # published versions) must be acyclic. Two people publishing drafts
            # that reference each other therefore cannot both pass this check.
            _check_reference_graph(db, metric, draft.expression)
            # Gate 2: pin the exact published version of every dependency. This
            # runs under the same serialized lock, so concurrent releases see each
            # other's committed versions and can never compose a version cycle.
            nodes, pending_key = _resolve_frozen(db, metric, draft.expression)

            version_no = (db.scalar(
                select(MetricVersion.version_no)
                .where(MetricVersion.metric_id == metric.id)
                .order_by(MetricVersion.version_no.desc())
                .limit(1)
            ) or 0) + 1
            now = time.time()
            version = MetricVersion(
                metric_id=metric.id, version_no=version_no, expression=draft.expression,
                unit=nodes[pending_key]["unit"], dependencies=[], resolved={},
                created_by=user.id, created_at=now, published_at=now,
                note=(note or draft.note or "").strip()[:500],
            )
            db.add(version)
            db.flush()
            version_key = f"version:{version.id}"
            root_node = nodes.pop(pending_key)
            root_node["key"] = version_key
            root_node["version_id"] = version.id
            root_node["version_no"] = version_no
            nodes[version_key] = root_node
            version.resolved = {"root": version_key, "nodes": nodes}
            version.dependencies = _dependency_list(nodes, version_key)
            metric.current_version_id = version.id
            metric.updated_at = now
            draft.base_version_id = version.id
            db.flush()
            # Commit while still holding the lock: a second publisher must see
            # this version before its own release gate runs.
            db.commit()
            return version
        except MetricError:
            db.rollback()
            raise
        except IntegrityError:
            db.rollback()
            raise MetricError("发布冲突，请重试（版本号已被并发占用）", "publish_conflict", [metric.code], status=409) from None
        except Exception:
            db.rollback()
            raise


def set_status(db, metric, status):
    if status not in ("active", "disabled"):
        raise MetricError("未知的口径状态", "invalid_status", [metric.code])
    metric.status = status
    metric.updated_at = time.time()
    db.flush()
    return metric


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def _draft_payload(draft, version):
    if not draft:
        return None
    return {
        "id": draft.id,
        "expression": draft.expression,
        "note": draft.note,
        "revision": draft.revision,
        "base_version_id": draft.base_version_id,
        "differs_from_published": _is_dirty(draft, version),
        "updated_at": draft.updated_at,
    }


def version_payload(version):
    return {
        "id": version.id,
        "metric_id": version.metric_id,
        "version_no": version.version_no,
        "expression": version.expression,
        "unit": version.unit,
        "dependencies": version.dependencies or [],
        "created_at": version.created_at,
        "published_at": version.published_at,
        "note": version.note,
    }


def metric_payload(db, metric, include_chain=False):
    version = current_version(db, metric)
    draft = _draft(db, metric)
    payload = {
        "id": metric.id,
        "code": metric.code,
        "name": metric.name,
        "description": metric.description,
        "status": metric.status,
        "current_version_id": metric.current_version_id,
        "current_version": version_payload(version) if version else None,
        "draft": _draft_payload(draft, version),
        "created_at": metric.created_at,
        "updated_at": metric.updated_at,
    }
    if include_chain:
        payload["versions"] = [
            version_payload(item)
            for item in db.scalars(
                select(MetricVersion)
                .where(MetricVersion.metric_id == metric.id)
                .order_by(MetricVersion.version_no.desc())
            )
        ]
        if version:
            try:
                snapshot = published_snapshot(db, metric)
                payload["chain"] = snapshot["chain"]
                payload["dependencies"] = snapshot["dependencies"]
            except MetricError:
                payload["chain"] = []
                payload["dependencies"] = []
        else:
            payload["chain"] = []
            payload["dependencies"] = []
        if draft:
            draft_view = validate_draft_safely(db, metric, draft.expression)
            try:
                draft_view["ignored_drafts"] = _referenced_dirty_drafts(db, draft.expression)
            except MetricError:
                draft_view["ignored_drafts"] = []
            payload["draft_validation"] = draft_view
    return payload


def validate_draft_safely(db, metric, expression):
    try:
        result = validate_draft(db, metric, expression)
        return {
            "ok": True,
            "unit": result["unit"],
            "chain": result["chain"],
            "dependencies": result["dependencies"],
            "message": "",
            "code": "",
        }
    except MetricError as exc:
        return {
            "ok": False,
            "unit": None,
            "chain": [{"code": code} for code in exc.chain],
            "dependencies": [],
            "message": exc.message,
            "code": exc.code,
            "chain_text": " → ".join(exc.chain),
        }


def list_payload(db, include_disabled=True):
    metrics = list(db.scalars(select(MetricDefinition).order_by(MetricDefinition.id)))
    if not include_disabled:
        metrics = [metric for metric in metrics if metric.status == "active"]
    return [metric_payload(db, metric) for metric in metrics]


def dependency_payload(db, metric, use_draft=False):
    error = None
    if use_draft:
        draft = _draft(db, metric)
        expression = draft.expression if draft else ""
        try:
            snapshot = trial_snapshot(db, metric, expression)
        except MetricError as exc:
            # A broken draft must not hide the rest of the registry page.
            return {
                "metric": metric_payload(db, metric),
                "root": None, "nodes": [], "chain": [], "dependencies": [],
                "unit": None, "provisional": True, "version": None,
                "error": exc.payload(),
            }
    else:
        try:
            snapshot = published_snapshot(db, metric)
        except MetricError as exc:
            return {
                "metric": metric_payload(db, metric),
                "root": None, "nodes": [], "chain": [], "dependencies": [],
                "unit": None, "provisional": False, "version": None,
                "error": exc.payload(),
            }
    return {
        "metric": metric_payload(db, metric),
        "root": snapshot["root"],
        "nodes": list(snapshot["nodes"].values()),
        "chain": snapshot["chain"],
        "dependencies": snapshot["dependencies"],
        "unit": snapshot["unit"],
        "provisional": snapshot.get("provisional", False),
        "version": snapshot.get("version"),
        "error": error,
    }
