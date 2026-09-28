import {test,expect} from '@playwright/test';

for(const width of [1280,375]){
  test(`role workspaces and shelf count handoff at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:900});
    await page.emulateMedia({reducedMotion:'reduce'});
    const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
    let role='',completed=false,approved=false;
    const posted:{path:string,body:any}[]=[];
    const user=()=>({id:role,name:role==='staff'?'Priya Shah':role==='admin'?'Morgan Lee':'Maria Reyes',role,email:role+'@freshpilot.demo',stores:['S1'],departments:[]});
    await page.route('**/api/**',async route=>{
      const req=route.request(),path=new URL(req.url()).pathname.slice(5);
      if(path==='auth/me'&&!role){await route.fulfill({status:401,json:{detail:'Sign in'}});return}
      if(req.method()==='POST'){
        expect(req.headers()['x-freshpilot']).toBe('1');
        const body=req.postDataJSON();posted.push({path,body});
        if(path==='auth/login'){role=body.email.split('@')[0];await route.fulfill({json:user()});return}
        if(path==='auth/logout')role='';
        if(path==='tasks/T1/complete')completed=true;
        if(path==='approvals/P1')approved=true;
        await route.fulfill({json:{ok:true}});return;
      }
      let json:any;
      if(path==='auth/me')json=user();
      else if(path==='tasks')json={tasks:[{id:'T1',product_id:'P1',name:'Strawberries',dept_id:'produce',sell_unit:'lb',assignee_name:'Priya Shah',status:completed?'complete':'open',counted_qty:40,damaged_qty:10,note:'Soft berries',completed_at:'2026-09-27T12:00:00'}]};
      else if(path==='summary/today')json={store:{name:'Test store'},date:'2025-06-30',items:2,auto_approved:1,pending:1,kpis:{},weekly_waste:[]};
      else if(path==='departments'||path==='activity')json=[];
      else if(path==='catalog/summary')json={auto_merged:0};
      else if(path==='approvals')json=approved?[]:[{product_id:'P1',name:'Strawberries',cases:20,payload:JSON.stringify({calculation:{order_cases:10}}),request_context:JSON.stringify({manager_reason:'Weekend event'}),requested_by:'Maria Reyes'}];
      else throw new Error('Unexpected read '+path);
      await route.fulfill({json});
    });
    const signIn=async(r:string)=>{await page.getByLabel('Work email').fill(r+'@freshpilot.demo');await page.getByLabel('Password',{exact:true}).fill('FreshPilot-demo!');await page.getByRole('button',{name:'Sign in',exact:true}).click()};
    await page.goto('/');
    await expect(page.getByRole('heading',{name:'Welcome back'})).toBeVisible();
    await signIn('staff');
    await expect(page.getByRole('heading',{name:'My tasks',exact:true})).toBeVisible();
    const nav=page.getByRole('navigation');
    await expect(nav.getByRole('button',{name:'Orders',exact:true})).toHaveCount(0);
    await expect(nav.getByRole('button',{name:'Team & access',exact:true})).toHaveCount(0);
    await page.getByLabel('Total counted (lb)').fill('40');
    await page.getByLabel('Damaged (lb)').fill('10');
    await page.getByLabel('Notes',{exact:true}).fill('Soft berries');
    await page.screenshot({path:`../reports/screenshots/staff-tasks-${width}.png`,fullPage:true});
    await page.getByRole('button',{name:'Complete shelf check'}).click();
    await expect(page.getByText('Counted 40 lb · 10 damaged. Soft berries')).toBeVisible();
    expect(posted.find(p=>p.path==='tasks/T1/complete')?.body).toEqual({counted_qty:40,damaged_qty:10,note:'Soft berries'});
    await nav.getByRole('button',{name:'Sign out'}).click();
    await signIn('manager');
    await expect(page.getByRole('heading',{name:'Good morning, Maria'})).toBeVisible();
    await expect(nav.getByRole('button',{name:'Shelf checks',exact:true})).toBeVisible();
    await expect(nav.getByRole('button',{name:'Approvals',exact:true})).toHaveCount(0);
    await expect(nav.getByRole('button',{name:'Observability',exact:true})).toHaveCount(0);
    await nav.getByRole('button',{name:'Sign out'}).click();
    await signIn('admin');
    await expect(page.getByRole('heading',{name:'Store overview'})).toBeVisible();
    await nav.getByRole('button',{name:'Approvals',exact:true}).click();
    await expect(page.getByText('Requested by Maria Reyes: Weekend event')).toBeVisible();
    await expect(page.getByRole('button',{name:'Approve exception'})).toBeDisabled();
    await page.getByLabel('Decision reason').fill('Event verified');
    await page.screenshot({path:`../reports/screenshots/admin-approvals-${width}.png`,fullPage:true});
    await page.getByRole('button',{name:'Approve exception'}).click();
    await expect(page.getByRole('heading',{name:'No exceptions waiting'})).toBeVisible();
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
    expect(errors).toEqual([]);
  });
}
