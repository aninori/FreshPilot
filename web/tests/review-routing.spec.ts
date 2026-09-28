import {test,expect} from '@playwright/test';

// UI-9: distinct manager controls for escalated decisions and auto-approved items.
// Browser-local API fixtures avoid modifying the user's current order run.
for(const width of [1280,375]){
  test(`reference control placement at ${width}px`,async({page})=>{
    await page.setViewportSize({width,height:900});
    let status='pending';
    let override=false;
    let note='';
    const actions:{path:string,body:any}[]=[];
    const errors:string[]=[];
    page.on('pageerror',e=>errors.push(e.message));
    const detail=(id:string,name:string)=>({
      product:{product_id:id,name,dept_id:'produce',sell_unit:'lb'},
      calculation:{calc_id:'CALC-'+id,calc_type:'order',order_cases:4,case_pack:6,
        order_units:24,target_qty:24,estimated_on_hand:0,breakdown:{cr:.6,book_on_hand:0}},
      reason:'Recorded forecast supports the quantity.',sales:[],forecast:[],checks:[],
    });
    await page.route('**/api/**',async route=>{
      const req=route.request(),url=new URL(req.url()),path=url.pathname.slice(5);
      if(req.method()==='POST'){
        const body=req.postDataJSON();
        actions.push({path,body});
        if(path==='decisions/D1/reject'){status='rejected';note=body.note}
        if(path==='decisions/D1/undo')status='pending';
        if(path==='decisions/D1/approve')status='approved';
        if(path==='items/A1/override'||path==='items/A1/reject')override=true;
        if(path==='items/A1/restore')override=false;
        await route.fulfill({json:{status}});return;
      }
      let json:any;
      if(path==='auth/me')json={id:'manager',name:'Maria Reyes',email:'manager@freshpilot.demo',role:'manager',stores:['S1'],departments:[]};
      else if(path==='summary/today')json={
        date:'2025-06-30',items:2,auto_approved:1,pending:status==='pending'?1:0,
        store:{name:'Test store'},kpis:{},weekly_waste:[],
      };
      else if(path==='departments'||path==='activity')json=[];
      else if(path==='catalog/summary')json={auto_merged:0};
      else if(path==='orders')json={departments:[],batch:null};
      else if(path==='decisions')json=[{
        id:'D1',product_id:'F1',status,cases:status==='rejected'?0:4,recommended_cases:4,
        override_reason:'Shelf is already stocked',override_note:note,
        finding:{summary:'Check this shelf before ordering.',reasons:[],confidence:'high'},
        item:detail('F1','Flagged pears'),
      }];
      else if(path==='items')json={items:[
        {product_id:'F1',name:'Flagged pears',dept_id:'produce',category:'normal',status,
          cases:4,decision_id:'D1',detail:detail('F1','Flagged pears')},
        {product_id:'A1',name:'Routine apples',dept_id:'produce',category:'normal',status:'approved',
          cases:override?5:4,decision_id:null,detail:detail('A1','Routine apples'),
          override_action:override?'override':null,original_cases:4,manager_cases:5,
          override_reason:'Forecast looks too high/low'},
      ],total:2,page:1,page_size:25,counts:[]};
      else throw new Error('Unexpected API read: '+path);
      await route.fulfill({json});
    });
    await page.goto('/');
    const nav=page.getByRole('navigation');
    await nav.getByRole('button',{name:'Agent review',exact:true}).click();
    await page.getByRole('button',{name:'Flagged pears',exact:true}).click();
    await expect(page.getByRole('button',{name:'Open decision'})).toBeVisible();
    await expect(page.locator('.manager-box')).toHaveCount(0);
    await page.getByRole('button',{name:'Open decision'}).click();
    await expect(page.locator('#decision-F1')).toBeFocused();
    await expect(page.locator('.rec .big')).toContainText('24 lb');
    await expect(page.locator('.conf i.f')).toHaveCount(3);
    await page.getByText('See the math',{exact:true}).click();
    await expect(page.locator('.decision-math .tech')).toBeVisible();
    await page.getByText('See the math',{exact:true}).click();
    await expect(page.locator('.decision-math .tech')).toBeHidden();
    await page.screenshot({path:`../reports/screenshots/decisions-reference-${width}.png`,fullPage:true});
    await page.getByRole('button',{name:'Don’t order',exact:true}).click();
    const rejection=page.locator('.decision-rejection');
    await expect(rejection).toBeVisible();
    await expect(page.locator('.manager-box')).toHaveCount(0);
    await expect(rejection.getByRole('button',{name:'Confirm, don’t order'})).toBeDisabled();
    await rejection.getByRole('button',{name:'Shelf is already stocked'}).click();
    await rejection.getByLabel('Rejection note').fill('Counted 28 lb');
    await page.screenshot({path:`../reports/screenshots/rejection-${width}.png`,fullPage:true});
    await rejection.getByRole('button',{name:'Confirm, don’t order'}).click();
    await expect(page.getByText('Not ordered · Shelf is already stocked · Counted 28 lb')).toBeVisible();
    await expect(page.getByRole('heading',{name:'You’re all caught up'})).toBeVisible();
    await expect(page.getByRole('button',{name:'Go to orders',exact:true})).toBeVisible();
    await expect(page.getByRole('progressbar',{name:'Decisions completed'})).toHaveAttribute('aria-valuenow','1');
    expect(actions[0]).toEqual({path:'decisions/D1/reject',body:{
      reason:'Shelf is already stocked',note:'Counted 28 lb',
    }});
    await page.getByRole('button',{name:'Go to orders',exact:true}).click();
    await expect(page.getByRole('heading',{name:'Orders',exact:true})).toBeVisible();
    await nav.getByRole('button',{name:'Decisions',exact:true}).click();
    await page.getByRole('button',{name:'Undo',exact:true}).click();
    await expect(page.getByRole('button',{name:'Approve',exact:true})).toBeVisible();
    await page.getByRole('button',{name:'Adjust',exact:true}).click();
    await expect(page.getByLabel('Adjustment reason')).toBeVisible();
    await page.getByRole('button',{name:'Don’t order',exact:true}).click();
    await expect(page.getByLabel('Adjustment reason')).toHaveCount(0);
    await expect(rejection).toBeVisible();
    await page.getByRole('button',{name:'Approve',exact:true}).click();
    await expect(page.getByRole('button',{name:'Undo',exact:true})).toBeVisible();
    await nav.getByRole('button',{name:'Agent review',exact:true}).click();
    await page.getByRole('button',{name:'Flagged pears',exact:true}).click();
    await expect(page.getByRole('button',{name:'Open decision'})).toBeVisible();
    await expect(page.locator('.manager-box')).toHaveCount(0);
    await page.getByRole('button',{name:'Routine apples',exact:true}).click();
    await expect(page.getByRole('button',{name:'Open decision'})).toHaveCount(0);
    await expect(page.getByText('Agent got this wrong?')).toBeVisible();
    await page.getByLabel('Override reason').selectOption('Forecast looks too high/low');
    await page.getByRole('button',{name:'More cases'}).click();
    await page.getByRole('button',{name:'Save change'}).click();
    await expect(page.getByRole('button',{name:'Restore agent’s order'})).toBeVisible();
    expect(actions.at(-1)?.path).toBe('items/A1/override');
    await page.getByRole('button',{name:'Restore agent’s order'}).click();
    await expect(page.getByText('Agent got this wrong?')).toBeVisible();
    await page.getByLabel('Override reason').selectOption('Supplier issue');
    await page.getByRole('button',{name:'Don’t order',exact:true}).click();
    await expect(page.getByRole('button',{name:'Restore agent’s order'})).toBeVisible();
    expect(actions.at(-1)?.path).toBe('items/A1/reject');
    await page.screenshot({path:`../reports/screenshots/review-controls-${width}.png`,fullPage:true});
    expect(errors).toEqual([]);
  });
}
