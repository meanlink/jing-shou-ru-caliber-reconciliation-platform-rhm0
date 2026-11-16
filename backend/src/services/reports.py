import time, os, csv, io, hashlib, json
from fastapi import HTTPException
from src.models import Job, Report, Delivery
from src.config.settings import ARTIFACT_DIR
def create(db, data):
    j=db.get(Job,data.job_id)
    if not j or j.status!='completed' or not j.approved:
        raise HTTPException(409,'请先选择已完成且获批准的分析结果')
    r=Report(name=data.name, job_id=j.id, destination=data.destination, interval_seconds=data.interval_seconds, next_run=time.time()+data.delay_seconds, enabled=True)
    db.add(r); db.flush()
    return r
def deliver(db, report):
    j=db.get(Job,report.job_id)
    if not j or not j.approved: return
    # The job's frozen snapshot is copied onto every delivery, so an archive can
    # always be traced to the exact formula and dependency versions used then.
    snapshot=j.metric_snapshot or {}
    if snapshot.get('version'):
        line='口径版本：'+str(snapshot['metric']['name'])+'（'+snapshot['metric']['code']+'）v'+str(snapshot['version']['version_no'])+' · '+str(snapshot['version']['expression'])
        if snapshot.get('dependencies'):
            line+=' · 依赖：'+'；'.join(item['name']+'（'+item['code']+'）v'+str(item['version_no']) for item in snapshot['dependencies'])
        report_meta=line
    else:
        report_meta='公式（旧任务，无版本绑定）：'+str(j.params.get('expression', ''))
    out=io.StringIO()
    out.write('\ufeff')
    table=j.result.get('table',{})
    cols=table.get('columns',[])
    writer=csv.writer(out)
    def safe(v):
        if isinstance(v,(int,float)) and not isinstance(v,bool): return v
        text=str(v)
        return "'"+text if text.startswith(('=','+','-','@','\t','\r')) else text
    writer.writerow([safe(c['label']) for c in cols])
    for row in table.get('rows',[]): writer.writerow([safe(row.get(c['key'],'')) for c in cols])
    data=out.getvalue().encode('utf-8')
    delivery=Delivery(report_id=report.id, created_at=time.time(), checksum=hashlib.sha256(data).hexdigest(), filename='', destination=report.destination, metric_snapshot=j.metric_snapshot)
    db.add(delivery); db.flush()
    delivery.filename=f'report-{report.id}-delivery-{delivery.id}.csv'
    os.makedirs(ARTIFACT_DIR,exist_ok=True)
    with open(os.path.join(ARTIFACT_DIR,delivery.filename),'wb') as f:f.write(data)
    with open(os.path.join(ARTIFACT_DIR,delivery.filename+'.json'),'w') as f: json.dump({'job':j.name,'params':j.params,'result':j.result,'checksum':delivery.checksum,'formula_provenance':report_meta,'metric_version':(j.metric_snapshot or {}).get('version'),'metric_dependencies':(j.metric_snapshot or {}).get('dependencies')},f,ensure_ascii=False,allow_nan=False)
    report.next_run=time.time()+report.interval_seconds
    return delivery
