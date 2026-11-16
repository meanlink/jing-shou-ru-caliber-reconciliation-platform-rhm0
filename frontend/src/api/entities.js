import {api} from './client.js';
export const datasets={list:()=>api('/datasets'),get:id=>api('/datasets/'+id),create:data=>api('/datasets',{method:'POST',body:JSON.stringify(data)})};
export const jobs={list:()=>api('/jobs'),get:id=>api('/jobs/'+id),create:data=>api('/jobs',{method:'POST',body:JSON.stringify(data)}),approve:id=>api('/jobs/'+id+'/approve',{method:'POST'})};
export const metrics={
 list:()=>api('/metrics'),
 get:id=>api('/metrics/'+id),
 create:data=>api('/metrics',{method:'POST',body:JSON.stringify(data)}),
 saveDraft:(id,data)=>api('/metrics/'+id+'/draft',{method:'PUT',body:JSON.stringify(data)}),
 validate:(id,data)=>api('/metrics/'+id+'/validate',{method:'POST',body:JSON.stringify(data)}),
 trial:(id,data)=>api('/metrics/'+id+'/trial',{method:'POST',body:JSON.stringify(data)}),
 publish:(id,data)=>api('/metrics/'+id+'/publish',{method:'POST',body:JSON.stringify(data)}),
 setStatus:(id,status)=>api('/metrics/'+id+'/status',{method:'POST',body:JSON.stringify({status})}),
 dependencies:id=>api('/metrics/'+id+'/dependencies'),
 draftDependencies:id=>api('/metrics/'+id+'/dependencies?draft=true'),
 versions:id=>api('/metrics/'+id+'/versions'),
 usage:id=>api('/metrics/'+id+'/usage')
};
export const reports={list:()=>api('/reports'),create:data=>api('/reports',{method:'POST',body:JSON.stringify(data)}),toggle:id=>api('/reports/'+id+'/toggle',{method:'POST'}),deliveries:()=>api('/deliveries')};
