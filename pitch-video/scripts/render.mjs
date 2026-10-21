import fs from 'node:fs';
import path from 'node:path';
import {bundle} from '@remotion/bundler';
import {renderMedia,renderStill,selectComposition} from '@remotion/renderer';

const root=process.cwd();
const out=path.join(root,'out');fs.mkdirSync(out,{recursive:true});
const timing=JSON.parse(fs.readFileSync(path.join(root,'src/timing.json'),'utf8'));
const sceneId=process.argv.find(arg=>arg.startsWith('--scene='))?.split('=')[1];
const selectedScene=sceneId?timing.scenes.find(scene=>scene.id===sceneId):undefined;
if(sceneId&&!selectedScene)throw new Error('Unknown scene: '+sceneId);
const outputName=selectedScene?`${selectedScene.id}-corrected.mp4`:'CRI_Pumps_APS_Pitch.mp4';
const localBrowser=path.resolve(root,'../../aps-video/node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe');
const browserExecutable=process.env.REMOTION_BROWSER_EXECUTABLE||(fs.existsSync(localBrowser)?localBrowser:undefined);
const status=(data)=>fs.writeFileSync(path.join(out,'render-status.json'),JSON.stringify({...data,updated:new Date().toISOString()},null,2));
try{
  status({status:'bundling'});
  const serveUrl=await bundle({entryPoint:path.join(root,'src/index.ts')});
  const composition=await selectComposition({serveUrl,id:'CRIPumpsPitch',browserExecutable});
  if(process.argv.includes('--stills')){
    const folder=path.join(out,'review');fs.mkdirSync(folder,{recursive:true});
    for(const scene of timing.scenes){
      for(const position of [.27,.8]){
        const frame=scene.from+Math.floor(scene.frames*position);
        const output=path.join(folder,scene.id+'-'+Math.round(position*100)+'.png');
        await renderStill({serveUrl,composition,frame,output,browserExecutable,imageFormat:'png'});
        console.log(scene.id,frame,output);
      }
    }
    status({status:'stills-complete',frames:timing.scenes.length*2});
  }else{
    status({status:'rendering',totalFrames:composition.durationInFrames});
    let last=0;
    await renderMedia({serveUrl,composition,browserExecutable,codec:'h264',crf:18,
      pixelFormat:'yuv420p',colorSpace:'bt709',audioCodec:'aac',audioBitrate:'192k',
      imageFormat:'jpeg',jpegQuality:95,concurrency:4,
      ...(selectedScene?{frameRange:[selectedScene.from,selectedScene.from+selectedScene.frames-1]}:{}),
      outputLocation:path.join(out,outputName),
      onProgress:({progress,renderedFrames,encodedFrames})=>{
        if(Date.now()-last>5000||progress===1){
          status({status:'rendering',progress,renderedFrames,encodedFrames,totalFrames:composition.durationInFrames});last=Date.now();
        }
      }
    });
    status({status:'complete',file:'out/'+outputName,totalFrames:selectedScene?.frames||composition.durationInFrames,seconds:(selectedScene?.frames||composition.durationInFrames)/composition.fps});
    console.log('Video rendered:',path.join(out,outputName));
  }
}catch(error){status({status:'failed',error:String(error),stack:error.stack});throw error;}
