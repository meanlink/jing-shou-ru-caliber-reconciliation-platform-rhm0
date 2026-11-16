import time

from fastapi import HTTPException
from redis import Redis
from src.config.settings import REDIS_URL
from src.models import Dataset, Job, MetricDefinition, MetricVersion
from src.services import metrics
from src.services.business import DEFAULT_EXPRESSION, FINAL_UNITS, metadata


ALLOWED_PARAMS = {'group_by', 'zero_policy'}
METRIC_PARAMS = {'metric_id', 'metric_version_id', 'expression'}


def _bind_metric(db, params):
    """Freeze the full dependency snapshot for this job at creation time."""
    metric_id = params.get('metric_id')
    if metric_id in (None, ''):
        # Legacy path: free-form DSL from the registered formula options.
        return None, None, None
    try:
        metric_id = int(metric_id)
    except (TypeError, ValueError):
        raise HTTPException(422, '口径标识无效') from None
    metric = db.get(MetricDefinition, metric_id)
    if not metric:
        raise HTTPException(404, '口径不存在')
    if metric.status != 'active':
        raise HTTPException(409, detail={
            'detail': f'口径 {metric.name}（{metric.code}）已停用，不能发起新的分析',
            'code': 'disabled', 'chain': [metric.code], 'chain_text': metric.code,
        })
    version_id = params.get('metric_version_id')
    if version_id not in (None, ''):
        try:
            version = db.get(MetricVersion, int(version_id))
        except (TypeError, ValueError):
            raise HTTPException(422, '口径版本标识无效') from None
        if not version or version.metric_id != metric.id:
            raise HTTPException(404, '所选口径版本不存在')
    else:
        version = db.get(MetricVersion, metric.current_version_id) if metric.current_version_id else None
        if not version:
            raise HTTPException(409, f'口径 {metric.name}（{metric.code}）尚无已发布版本，不能发起分析')
    # The whole pinned dependency graph must still be usable for a new analysis.
    for dependency in (version.dependencies or []):
        definition = db.get(MetricDefinition, dependency.get('metric_id'))
        if not definition or definition.status != 'active':
            chain = [metric.code, dependency.get('code')]
            raise HTTPException(409, detail={
                'detail': f'依赖口径 {dependency.get("name")}（{dependency.get("code")}）已停用，不能发起新的分析；依赖链：' + ' → '.join(chain),
                'code': 'disabled', 'chain': chain, 'chain_text': ' → '.join(chain),
            })
    # Pin exactly the requested version (defaults to the current one), so a
    # re-run of an older approved formula reproduces its original result.
    snapshot = metrics.snapshot_for_version(db, metric, version)
    return metric.id, version.id, snapshot


def create(db, data, user):
    if not db.get(Dataset, data.dataset_id):
        raise HTTPException(404, '数据批次不存在')
    allowed = {p['key']: p for p in metadata().get('parameters', [])}
    unknown = set(data.params) - set(allowed) - METRIC_PARAMS
    if unknown:
        raise HTTPException(422, '包含未知分析参数')
    params = {k: p['default'] for k, p in allowed.items()}
    params.update({k: v for k, v in data.params.items() if k in allowed})
    for key, p in allowed.items():
        value = params[key]
        if p['type'] == 'number':
            try:
                value = float(value)
                if not (p.get('min', -1e9) <= value <= p.get('max', 1e9)):
                    raise ValueError()
                params[key] = value
            except (ValueError, TypeError):
                raise HTTPException(422, f'{p["label"]} 超出范围或非数值') from None
        elif str(value) not in [str(o['value']) for o in p.get('options', [])]:
            raise HTTPException(422, f'{p["label"]} 不是有效选项')

    metric_id, version_id, snapshot = _bind_metric(db, data.params)
    if snapshot and snapshot['version']['unit'] not in FINAL_UNITS:
        label = snapshot.get('metric') or {}
        code = label.get('code', str(metric_id))
        raise HTTPException(422, detail={
            'detail': f'口径 {label.get("name", "")}（{code}）的结果单位为 {snapshot["version"]["unit"]}，分析任务只接受 CNY 或 CNY/visit',
            'code': 'unit_conflict', 'chain': [code], 'chain_text': code,
        })
    if snapshot:
        params['expression'] = snapshot['version']['expression']
        params['metric_id'] = metric_id
        params['metric_version_id'] = version_id
    else:
        # Legacy behaviour: recorded formula text must be one of the two contracts.
        options = {str(o['value']) for o in allowed['expression'].get('options', [])}
        if str(params.get('expression', DEFAULT_EXPRESSION)) not in options:
            raise HTTPException(422, '指标DSL 不是有效选项')

    job = Job(
        name=data.name, dataset_id=data.dataset_id, params=params,
        created_by=user.id, created_at=time.time(), status='queued',
        metric_id=metric_id, metric_version_id=version_id, metric_snapshot=snapshot,
    )
    db.add(job)
    db.flush()
    return job


def enqueue(job_id):
    try:
        connection = Redis.from_url(REDIS_URL)
        connection.rpush('analysis:queue', job_id)
        connection.close()
    except Exception:
        pass  # SQL queued status is the durable fallback
