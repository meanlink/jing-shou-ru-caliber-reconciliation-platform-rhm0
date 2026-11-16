import os, logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import text
from redis import Redis
from src.config.database import Session
from src.models import User
from src.utils.security import decode_token
from src.config.settings import REDIS_URL
from src.middlewares.requests import RequestControl
from src.routes import auth, datasets, jobs, metrics as metric_routes, reports
from src.controllers import catalog
from migrations.initial import migrate
@asynccontextmanager
async def lifespan(app):
    migrate()
    if os.getenv('SEED_DEMO','true').lower()=='true':
        try:
            from scripts.seed_demo import seed
            seed()
        except Exception:logging.exception('Demo seed failed; backend remains available')
    yield
app=FastAPI(title='业务分析工作室',lifespan=lifespan)
app.add_middleware(RequestControl)
@app.exception_handler(StarletteHTTPException)
async def http_error(request:Request,exc:StarletteHTTPException):
    # Dependency-chain errors are returned flat so the page can show code + chain.
    if isinstance(exc.detail,dict) and 'chain' in exc.detail:
        return JSONResponse(exc.detail,status_code=exc.status_code)
    return JSONResponse({'detail':exc.detail},status_code=exc.status_code,headers=exc.headers)
@app.exception_handler(Exception)
async def unhandled(request:Request,exc:Exception):
    logging.exception('Request failed')
    return JSONResponse({'detail':'服务暂不可用，请稍后重试'},status_code=500)
PUBLIC={'/auth/login','/meta','/health'}
PROTECTED_PREFIXES=('/datasets','/jobs','/reports','/deliveries','/metrics','/overview','/audits')

def _is_protected(request:Request):
    path=request.url.path
    return path not in PUBLIC and path.startswith(PROTECTED_PREFIXES)

@app.middleware('http')
async def authorize(request:Request,call_next):
    if not _is_protected(request) or request.method=='OPTIONS':
        return await call_next(request)
    # Fail closed before routing: every protected endpoint needs a valid token,
    # even if a route forgets its explicit dependency.
    header=request.headers.get('Authorization','')
    try:
        token=header.split(' ',1)[1] if header.lower().startswith('bearer ') else ''
        with Session() as db:
            user=db.get(User,decode_token(token))
            if user is None:raise ValueError()
    except Exception:
        return JSONResponse({'detail':'登录已过期，请重新登录'},status_code=401)
    if request.method not in ('GET','HEAD') and request.url.path.startswith('/metrics') and user.role not in ('admin','analyst'):
        return JSONResponse({'detail':'观察员只有读取权限'},status_code=403)
    return await call_next(request)

@app.get('/health')
def health():
    with Session() as db:db.execute(text('SELECT 1'))
    r=Redis.from_url(REDIS_URL,socket_timeout=2);r.ping();r.close()
    return {'status':'healthy'}
for router in [auth.router,datasets.router,jobs.router,metric_routes.router,reports.router,catalog.router]:app.include_router(router)
