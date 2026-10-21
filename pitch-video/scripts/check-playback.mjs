import fs from 'node:fs';
import {chromium} from '@playwright/test';

const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:1000}});
const errors=[];page.on('pageerror',e=>errors.push(String(e)));
await page.goto('http://localhost:3012/',{waitUntil:'networkidle'});
await page.locator('video').evaluate(async v=>{
  if(v.readyState<1)await new Promise(resolve=>v.addEventListener('loadedmetadata',resolve,{once:true}));
  v.muted=true;v.currentTime=30;await v.play();
});
await page.waitForTimeout(1300);
const playback=await page.locator('video').evaluate(v=>({duration:v.duration,currentTime:v.currentTime,width:v.videoWidth,height:v.videoHeight,error:v.error?.message||null,readyState:v.readyState}));
await page.locator('video').evaluate(v=>v.pause());
if(playback.error||playback.currentTime<=30||playback.width!==1920)throw new Error(JSON.stringify(playback));
const result={passed:errors.length===0,playback,errors};
fs.writeFileSync('out/playback-check.json',JSON.stringify(result,null,2));
await browser.close();
console.log(result);
