import {test,expect} from '@playwright/test';

test('M9: approve decisions, send orders, inspect a trace; mobile and dark screens',async({page,request})=>{
  const login=await request.post('http://127.0.0.1:8000/api/auth/login',{headers:{'X-FreshPilot':'1'},data:{email:'admin@freshpilot.demo',password:'FreshPilot-demo!'}});
  expect(login.ok()).toBeTruthy();
  const reset=await request.post('http://127.0.0.1:8000/api/pipeline/run',{headers:{'X-FreshPilot':'1'},data:{date:'2025-06-30'},timeout:120000});
  expect(reset.ok()).toBeTruthy();
  const errors:string[]=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('/');
  await page.getByLabel('Work email').fill('admin@freshpilot.demo');
  await page.getByLabel('Password',{exact:true}).fill('FreshPilot-demo!');
  await page.getByRole('button',{name:'Sign in',exact:true}).click();
  await expect(page.getByRole('heading',{name:'Store overview'})).toBeVisible();
  await expect(page.locator('.hero')).toContainText('1,280');
  await page.screenshot({path:'../reports/screenshots/today-light.png',fullPage:true});
  await page.getByRole('navigation').getByRole('button',{name:'Decisions'}).click();
  await expect(page.getByRole('button',{name:'Approve',exact:true}).first()).toBeVisible();
  await page.getByText('See the math',{exact:true}).first().click();
  await expect(page.locator('.tech').first()).toBeVisible();
  await page.screenshot({path:'../reports/screenshots/decisions.png',fullPage:true});
  const count=await page.getByRole('button',{name:'Approve',exact:true}).count();
  for(let i=0;i<count;i++){
    await page.getByRole('button',{name:'Approve',exact:true}).first().click();
    await expect(page.getByRole('button',{name:'Approve',exact:true})).toHaveCount(count-i-1);
  }
  await expect(page.getByRole('heading',{name:'You’re all caught up'})).toBeVisible();
  await page.getByRole('navigation').getByRole('button',{name:'Orders',exact:true}).click();
  await page.getByRole('button',{name:'Send active orders',exact:true}).click();
  await expect(page.getByText(/Orders sent · recall/)).toBeVisible();
  await page.getByRole('navigation').getByRole('button',{name:'Observability'}).click();
  await page.locator('.trace-button').first().click();
  await expect(page.locator('.trace-detail')).toBeVisible();
  await page.screenshot({path:'../reports/screenshots/observability.png',fullPage:true});
  await page.getByRole('navigation').getByRole('button',{name:'Agent review'}).click();
  await page.getByRole('textbox',{name:'Search items'}).fill('strawberries');
  await expect(page.locator('tbody')).toContainText(/strawberries/i);
  for(const screen of ['Catalog','Impact']){
    await page.getByRole('navigation').getByRole('button',{name:screen,exact:true}).click();
    await expect(page.getByRole('heading',{name:screen,exact:true})).toBeVisible();
    await expect(page.locator('.view')).not.toBeEmpty();
    await page.screenshot({path:`../reports/screenshots/${screen.toLowerCase()}.png`,fullPage:true});
  }
  await page.getByRole('navigation').getByRole('button',{name:'Store overview',exact:true}).click();
  await page.getByRole('button',{name:'Toggle color theme'}).click();
  await page.screenshot({path:'../reports/screenshots/today-dark.png',fullPage:true});
  await page.setViewportSize({width:375,height:812});
  await page.screenshot({path:'../reports/screenshots/mobile.png',fullPage:true});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
  await page.getByRole('main').getByRole('button',{name:'Ask FreshPilot',exact:true}).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.getByRole('textbox',{name:'Your question'}).fill('What did the agents do today?');
  await page.getByRole('button',{name:'Send question'}).click();
  await expect(page.locator('.msg.ai .src')).toContainText('Sources:');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(errors).toEqual([]);
});
