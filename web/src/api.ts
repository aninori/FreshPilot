import {useEffect,useState} from 'react';
export async function api(path:string, body?:unknown):Promise<any>{
  const response=await fetch('/api/'+path,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json','X-FreshPilot':'1'},body:JSON.stringify(body)});
  const data=await response.json();
  if(!response.ok){if(response.status===401&&!path.startsWith('auth/'))window.dispatchEvent(new Event('session-expired'));throw new Error(typeof data.detail==='string'?data.detail:'Please check the supplied values.');}
  return data;
}
export function useData(path:string,revision=0){
  const [data,setData]=useState<any>(null),[error,setError]=useState('');
  useEffect(()=>{let current=true;setError('');api(path).then(d=>{if(current)setData(d)}).catch(e=>{if(current)setError(e.message)});return()=>{current=false}},[path,revision]);
  return {data,error};
}
