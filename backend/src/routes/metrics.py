from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from src.config.database import get_db
from src.middlewares.auth import admin_only, can_write, current_user
from src.models import Dataset, Job
from src.schemas.metric import (
    MetricDraftInput,
    MetricInput,
    MetricPublishInput,
    MetricStatusInput,
    MetricTrialInput,
)
from src.services import metrics
from src.services.audit import record
from src.services.business import analyze

router = APIRouter(prefix='/metrics')


def _raise(exc: metrics.MetricError):
    raise HTTPException(exc.status, detail=exc.payload())


def _metric(db, metric_id):
    return metrics.get_metric(db, metric_id)


@router.get('')
def listing(db=Depends(get_db), user=Depends(current_user)):
    return metrics.list_payload(db)


@router.post('', status_code=201)
def create(data: MetricInput, db=Depends(get_db), user=Depends(can_write)):
    try:
        metric = metrics.create(db, data, user)
        record(db, user.email, 'metric.create', {'id': metric.id, 'code': metric.code})
        db.commit()
    except metrics.MetricError as exc:
        _raise(exc)
    return metrics.metric_payload(db, metric, include_chain=True)


@router.get('/{metric_id}')
def detail(metric_id: int, db=Depends(get_db), user=Depends(current_user)):
    try:
        metric = _metric(db, metric_id)
        return metrics.metric_payload(db, metric, include_chain=True)
    except metrics.MetricError as exc:
        _raise(exc)


@router.put('/{metric_id}/draft')
def update_draft(metric_id: int, data: MetricDraftInput, db=Depends(get_db), user=Depends(can_write)):
    try:
        metric = _metric(db, metric_id)
        draft = metrics.update_draft(db, metric, data.expression, data.note, user, data.expected_revision)
        record(db, user.email, 'metric.draft.update', {'id': metric.id, 'revision': draft.revision})
        db.commit()
        return metrics.metric_payload(db, metric, include_chain=True)
    except metrics.MetricError as exc:
        _raise(exc)


@router.post('/{metric_id}/validate')
def validate(metric_id: int, data: MetricDraftInput, db=Depends(get_db), user=Depends(can_write)):
    try:
        metric = _metric(db, metric_id)
        result = metrics.validate_draft(db, metric, data.expression)
        ignored = metrics._referenced_dirty_drafts(db, data.expression)
        db.rollback()
        return {
            'ok': True,
            'unit': result['unit'],
            'chain': result['chain'],
            'dependencies': result['dependencies'],
            'ignored_drafts': ignored,
            'message': '',
            'code': '',
        }
    except metrics.MetricError as exc:
        db.rollback()
        return {
            'ok': False,
            'unit': None,
            'chain': [{'code': code} for code in exc.chain],
            'dependencies': [],
            'message': exc.message,
            'code': exc.code,
            'chain_text': ' → '.join(exc.chain),
        }


@router.post('/{metric_id}/trial')
def trial(metric_id: int, data: MetricTrialInput, db=Depends(get_db), user=Depends(can_write)):
    dataset = db.get(Dataset, data.dataset_id)
    if not dataset:
        raise HTTPException(404, '数据批次不存在')
    try:
        metric = _metric(db, metric_id)
        expression = data.expression
        if expression is None:
            draft = metrics._draft(db, metric)
            if not draft:
                raise metrics.MetricError('口径草稿不存在', 'not_found', [metric.code], status=404)
            expression = draft.expression
        snapshot = metrics.trial_snapshot(db, metric, expression)
        result = analyze(
            dataset.records,
            {'group_by': data.group_by, 'zero_policy': data.zero_policy},
            metric=snapshot,
        )
        db.rollback()
    except metrics.MetricError as exc:
        db.rollback()
        _raise(exc)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(422, detail={'detail': str(exc), 'code': 'trial_failed', 'chain': [], 'chain_text': ''})
    return {
        'snapshot': {
            'provisional': True,
            'unit': snapshot['unit'],
            'chain': snapshot['chain'],
            'dependencies': snapshot['dependencies'],
        },
        'result': result,
    }


@router.post('/{metric_id}/publish')
def publish(metric_id: int, data: MetricPublishInput, db=Depends(get_db), user=Depends(admin_only)):
    try:
        metric = _metric(db, metric_id)
        version = metrics.publish(db, metric, user, data.note, expected_revision=data.expected_revision)
        record(db, user.email, 'metric.publish', {
            'id': metric.id, 'code': metric.code,
            'version_id': version.id, 'version_no': version.version_no,
            'dependencies': version.dependencies,
        })
        db.commit()
        return metrics.metric_payload(db, metric, include_chain=True)
    except metrics.MetricError as exc:
        _raise(exc)


@router.post('/{metric_id}/status')
def status(metric_id: int, data: MetricStatusInput, db=Depends(get_db), user=Depends(admin_only)):
    try:
        metric = _metric(db, metric_id)
        metrics.set_status(db, metric, data.status)
        record(db, user.email, 'metric.status', {'id': metric.id, 'status': data.status})
        db.commit()
        return metrics.metric_payload(db, metric, include_chain=True)
    except metrics.MetricError as exc:
        _raise(exc)


@router.get('/{metric_id}/dependencies')
def dependencies(metric_id: int, draft: bool = False, db=Depends(get_db), user=Depends(current_user)):
    try:
        metric = _metric(db, metric_id)
        return metrics.dependency_payload(db, metric, use_draft=draft)
    except metrics.MetricError as exc:
        _raise(exc)


@router.get('/{metric_id}/versions')
def versions(metric_id: int, db=Depends(get_db), user=Depends(current_user)):
    try:
        metric = _metric(db, metric_id)
        rows = db.scalars(
            select(metrics.MetricVersion)
            .where(metrics.MetricVersion.metric_id == metric.id)
            .order_by(metrics.MetricVersion.version_no.desc())
        )
        return {
            'metric': metrics.metric_payload(db, metric),
            'versions': [metrics.version_payload(item) for item in rows],
        }
    except metrics.MetricError as exc:
        _raise(exc)


@router.get('/{metric_id}/usage')
def usage(metric_id: int, db=Depends(get_db), user=Depends(current_user)):
    try:
        metric = _metric(db, metric_id)
        jobs = list(db.scalars(
            select(Job).where(Job.metric_id == metric.id).order_by(Job.id.desc()).limit(50)
        ))
        version_ids = {job.metric_version_id for job in jobs if job.metric_version_id}
        version_numbers = {
            version.id: version.version_no
            for version in db.scalars(select(metrics.MetricVersion).where(metrics.MetricVersion.id.in_(version_ids)))
        } if version_ids else {}
        return [
            {
                'id': job.id, 'name': job.name, 'status': job.status,
                'approved': job.approved,
                'metric_version_id': job.metric_version_id,
                'metric_version_no': version_numbers.get(job.metric_version_id),
                'expression': (job.metric_snapshot or {}).get('version', {}).get('expression'),
                'created_at': job.created_at,
            }
            for job in jobs
        ]
    except metrics.MetricError as exc:
        _raise(exc)
