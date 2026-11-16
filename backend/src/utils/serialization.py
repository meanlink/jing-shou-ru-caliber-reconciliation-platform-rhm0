import hashlib, json
def checksum(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()
def dataset_dict(d):
    return {'id':d.id, 'name':d.name, 'source':d.source, 'rows':len(d.records), 'checksum':d.checksum, 'created_at':d.created_at}
def job_dict(j):
    data={k:getattr(j,k) for k in ['id','name','dataset_id','params','status','result','error','approved','created_at','finished_at','metric_id','metric_version_id']}
    snapshot=j.metric_snapshot or {}
    data['metric']=snapshot.get('metric')
    data['metric_version']=snapshot.get('version')
    data['metric_chain']=snapshot.get('chain') or []
    data['metric_dependencies']=snapshot.get('dependencies') or []
    return data
def report_dict(r):
    return {k:getattr(r,k) for k in ['id','name','job_id','destination','interval_seconds','next_run','enabled']}
def delivery_dict(d):
    data={k:getattr(d,k) for k in ['id','report_id','created_at','checksum','filename','destination']}
    snapshot=d.metric_snapshot or {}
    data['metric']=snapshot.get('metric')
    data['metric_version']=snapshot.get('version')
    data['metric_dependencies']=snapshot.get('dependencies') or []
    return data
