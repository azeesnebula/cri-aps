import fs from 'node:fs';
import path from 'node:path';
import {chromium} from '@playwright/test';

const root=process.cwd();
const out=path.join(root,'public','captures');
fs.mkdirSync(out,{recursive:true});
const browser=await chromium.launch({channel:'msedge',headless:true});
const context=await browser.newContext({viewport:{width:1600,height:1000},deviceScaleFactor:1.5,colorScheme:'dark'});
// The supplied GIF cycles through company divisions. Freeze its unmodified
// first (Pumps) frame for deterministic capture; application data/DOM stay intact.
await context.route('**/cri-logo.gif',route=>route.fulfill({contentType:'image/png',body:fs.readFileSync(path.join(root,'public','cri-logo.png'))}));
await context.addInitScript(()=>localStorage.setItem('cri-pumps-theme','dark'));
const page=await context.newPage();
const errors=[];
page.on('pageerror',e=>errors.push({url:page.url(),error:String(e)}));
await page.goto('http://localhost:8081/data/login/');
await page.locator('#id_username').fill(process.env.CRI_VIDEO_USER||'admin');
await page.locator('#id_password').fill(process.env.CRI_VIDEO_PASSWORD||'admin');
await page.locator('button[type=submit]').click();
await page.waitForURL('http://localhost:8081/');
const captures={};
async function shot(name,route,selectors={}) {
  if(route) await page.goto('http://localhost:8081'+route,{waitUntil:'networkidle'});
  await page.waitForTimeout(700);
  await page.mouse.move(1580,980);
  if(await page.locator('html').getAttribute('data-theme')!=='dark') throw new Error('Dark theme missing');
  await page.screenshot({path:path.join(out,name+'.png'),fullPage:false});
  const boxes={};
  for(const [key,selector] of Object.entries(selectors)) {
    const target=page.locator(selector).first();
    if(await target.count()) boxes[key]=await target.boundingBox();
  }
  captures[name]={file:'captures/'+name+'.png',url:page.url(),width:2400,height:1500,viewport:{width:1600,height:1000},boxes};
  console.log('Captured',name,page.url());
}
await shot('overview','/',{metrics:'.cri-summary',table:'.sg-rp-tblwrap',firstShipment:'.sg-home-row'});
await shot('orders-all','/production-orders/',{filters:'.sg-orders-filters',table:'.sg-orders-table'});
await page.locator('#production-shipment').selectOption('CRI-SHIP-002');
await page.locator('button[type=submit]').click();
await page.waitForLoadState('networkidle');
await shot('orders-filtered',null,{filters:'.sg-orders-filters',table:'.sg-orders-table'});
await shot('shipment','/shipment/CRI-SHIP-002/',{verdict:'.sg-work-status',tabs:'.sg-work-tabs'});
await page.locator('#tab-materials').click();
await shot('shipment-supply',null,{tabs:'.sg-work-tabs',supply:'#materials'});
await shot('schedule-top','/machine-schedule/?start=2026-10-19&days=45');
await page.locator('#sg-byorder').click();
await page.locator('.sg-sc-chart').scrollIntoViewIfNeeded();
await shot('schedule-board',null,{chart:'.sg-sc-chart'});
await shot('schedule-focused','/machine-schedule/?start=2026-10-19&days=14&group=CRI-GRP-WIND');
await page.locator('.sg-sc-chart').scrollIntoViewIfNeeded();
await shot('winding-board',null,{chart:'.sg-sc-chart',bar:'.sg-sc-bar'});
await page.locator('.sg-sc-bar').first().dblclick();
await page.locator('#sg-md:not([hidden])').waitFor();
await page.waitForTimeout(800);
await shot('schedule-detail',null);
await shot('bottlenecks','/bottlenecks/?pct=75',{table:'.sg-rp-heat'});
await shot('release','/release/?days=7',{checks:'.sg-rl-cards'});
await page.goto('http://localhost:8081/build-graph/CRI-SO-002-01/?render=dom',{waitUntil:'networkidle'});
await page.waitForTimeout(1200);
await page.locator('[data-act="expand"]').click();
await page.locator('[data-act="fit"]').click();
await shot('build-graph',null,{graph:'.sg-bg-stage'});
await page.locator('[data-act="reset"]').click();
await page.locator('.sg-bg-node').first().scrollIntoViewIfNeeded();
await page.locator('.sg-bg-node').first().click();
await shot('graph-detail',null,{graph:'.sg-bg-stage',panel:'#sg-bg-panel'});
fs.writeFileSync(path.join(out,'manifest.json'),JSON.stringify({capturedAt:new Date().toISOString(),theme:'dark',source:'Actual C.R.I APS application',captures,errors},null,2));
await browser.close();
if(errors.length) throw new Error(JSON.stringify(errors));
