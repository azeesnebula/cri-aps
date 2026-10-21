import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';

const out=path.resolve('out');
const movie='CRI_Pumps_APS_Pitch.mp4';
http.createServer((request,response)=>{
  const pathname=decodeURIComponent(new URL(request.url,'http://localhost').pathname);
  if(pathname==='/'){
    response.writeHead(200,{'Content-Type':'text/html; charset=utf-8'});
    response.end(`<!doctype html><html><head><meta charset="utf-8"><title>C.R.I Pumps APS — Product demo</title><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0;min-height:100vh;display:grid;place-items:center;background:#0c1015;color:#eef2f7;font:15px Arial}main{width:min(1500px,96vw)}video{display:block;width:100%;max-height:88vh;background:#000;border-radius:8px}p{display:flex;justify-content:space-between;color:#9aa7b6}a{color:#ff7e8b}</style></head><body><main><video controls preload="metadata" poster="/poster.jpg" src="/${movie}"></video><p><span>C.R.I Pumps APS · Pitch &amp; product demo</span><a href="/${movie}" download>Download MP4</a></p></main></body></html>`);
    return;
  }
  const file=path.resolve(out,'.'+pathname);
  if(!file.startsWith(out+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){
    response.writeHead(404);response.end('Not found');return;
  }
  const size=fs.statSync(file).size;
  const type=file.endsWith('.mp4')?'video/mp4':file.endsWith('.jpg')?'image/jpeg':'application/octet-stream';
  const range=request.headers.range?.match(/^bytes=(\d+)-(\d*)$/);
  const start=range?Number(range[1]):0;
  const end=range&&range[2]?Math.min(Number(range[2]),size-1):size-1;
  if(start>size-1||end<start){response.writeHead(416,{'Content-Range':`bytes */${size}`});response.end();return;}
  response.writeHead(range?206:200,{'Content-Type':type,'Accept-Ranges':'bytes','Content-Length':end-start+1,...(range?{'Content-Range':`bytes ${start}-${end}/${size}`}:{})});
  if(request.method==='HEAD')response.end();else fs.createReadStream(file,{start,end}).pipe(response);
}).listen(3012,'127.0.0.1',()=>console.log('Video player: http://localhost:3012'));
