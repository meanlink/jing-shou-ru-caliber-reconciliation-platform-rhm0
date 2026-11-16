import time, json
from sqlalchemy import select, func
from src.config.database import Session
from migrations.initial import migrate
from src.models import User, Dataset, Job, Report, Delivery, Audit, MetricDefinition
from src.services.business import metadata, seed_records, analyze
from src.services import metrics as metric_registry
from src.schemas.metric import MetricInput
from src.services.reports import deliver
from src.utils.security import hash_password
from src.utils.serialization import checksum
def seed():
    migrate()
    with Session() as db:
        for role,name in [('admin','林知 · 业务负责人'),('analyst','陈舟 · 分析员'),('viewer','周禾 · 观察员')]:
            email=role+'@demo.local'
            if not db.scalar(select(User).where(User.email==email)):
                db.add(User(email=email,name=name,role=role,password=hash_password('Demo123!')))
        db.flush()
        m=metadata()
        d=db.scalar(select(Dataset).where(Dataset.name==m['dataset_name']))
        if not d:
            rows=seed_records()
            d=Dataset(name=m['dataset_name'],source=m['short']+'-202611.csv',records=rows,checksum=checksum(rows),created_at=time.time())
            db.add(d); db.flush()
        admin=db.scalar(select(User).where(User.role=='admin'))
        catalogue=[('deductions','退款与折扣','sum(refunds)+sum(discount)','退款与折扣的合并扣除，供多个净收入口径共用'),
                   ('net_revenue','净收入','sum(gross)-ref(deductions)','毛额扣除退款与折扣后的统一口径'),
                   ('net_revenue_per_visit','单次访问净收入','ratio(ref(net_revenue),sum(visits))','复用净收入子口径再除以总访问量')]
        published={}
        for code,name,expression,description in catalogue:
            metric=metric_registry.get_by_code(db,code)
            if not metric:
                metric=metric_registry.create(db,MetricInput(code=code,name=name,expression=expression,description=description),admin)
                db.flush()
            version=db.get(metric_registry.MetricVersion,metric.current_version_id) if metric.current_version_id else None
            if not version:
                version=metric_registry.publish(db,metric,admin,f'演示基线 {name}')
            published[code]=(metric,version)
        net_metric,net_version=published['net_revenue']
        if not db.scalar(select(Job).where(Job.name=='基线复核 · '+m['name'])):
            params={p['key']:p['default'] for p in m.get('parameters',[])}
            params.update({'metric_id':net_metric.id,'metric_version_id':net_version.id,'expression':net_version.expression})
            snapshot=metric_registry.published_snapshot(db,net_metric)
            result=analyze(d.records,params,metric=snapshot)
            json.dumps(result,ensure_ascii=False,allow_nan=False)
            j=Job(name='基线复核 · '+m['name'],dataset_id=d.id,params=params,status='completed',result=result,created_by=admin.id,created_at=time.time()-3600,finished_at=time.time()-3595,approved=True,metric_id=net_metric.id,metric_version_id=net_version.id,metric_snapshot=snapshot)
            db.add(j);db.flush()
            r=Report(name='每日业务复核简报',job_id=j.id,destination='业务复核组 · 本地收件箱',interval_seconds=86400,next_run=time.time()+86400,enabled=True)
            db.add(r);db.flush();deliver(db,r)
            db.add(Audit(actor='seed',action='demo.baseline',detail={'dataset':d.id,'metric_version_id':net_version.id},at=time.time()))
        db.commit()
        counts={model.__tablename__:db.scalar(select(func.count()).select_from(model)) for model in [User,Dataset,Job,Report,Delivery,Audit,MetricDefinition]}
        print(json.dumps(counts,ensure_ascii=False))
        return counts
if __name__=='__main__':seed()
