import React from 'react';
import {AbsoluteFill, CanvasImage, Easing, Interactive, interpolate, Sequence, staticFile, Track, useCurrentFrame, useVideoConfig} from 'remotion';
import {Audio} from '@remotion/media';
import type {Caption} from '@remotion/captions';
import {ArrowRight, ArrowUpRight, Boxes, CalendarClock, Check, CheckCheck, ClipboardList, Cog, Factory, GitBranch, Layers3, PackageCheck, ShieldCheck, Timer, Truck, Users, Workflow, Zap} from 'lucide-react';
import timing from './timing.json';
import './style.css';

const C={bg:'#0c1015',surface:'#171d25',line:'#303945',white:'#f4f6f8',muted:'#9aa7b6',red:'#ff4055',deep:'#c8102e',green:'#7bdfb2'};
const clamp={extrapolateLeft:'clamp',extrapolateRight:'clamp'} as const;
const ease=Easing.bezier(.16,1,.3,1);
const scenes=timing.scenes;
type SceneData=typeof scenes[number];
type Rect={x:number;y:number;w:number};

function enter(frame:number,start=0,duration=25){return interpolate(frame,[start,start+duration],[0,1],{...clamp,easing:ease});}

function Backdrop({hero=false}:{hero?:boolean}){
  const f=useCurrentFrame();
  return <AbsoluteFill style={{background:C.bg,overflow:'hidden'}}>
    <AbsoluteFill style={{background:hero?'radial-gradient(ellipse at 83% 35%, #311923 0%, transparent 58%)':'radial-gradient(ellipse at 15% 0%, #251820 0%, transparent 58%)'}}/>
    <div style={{position:'absolute',inset:0,opacity:.11,backgroundImage:'linear-gradient(#637488 1px, transparent 1px),linear-gradient(90deg,#637488 1px, transparent 1px)',backgroundSize:'80px 80px',maskImage:'linear-gradient(90deg, transparent, black)'}}/>
    <svg width="1920" height="960" style={{position:'absolute',opacity:.27}}>
      {[0,1,2,3].map(i=><circle key={i} cx="1580" cy="470" r={240+i*110} fill="none" stroke={i===1?C.red:'#445264'} strokeWidth={i===1?1.5:.7} strokeDasharray={i===1?'2 16':undefined} strokeDashoffset={-f*.17}/>)
      }
    </svg>
    <div style={{position:'absolute',left:0,bottom:0,width:'100%',height:116,background:C.bg}}/>
  </AbsoluteFill>;
}

function Brand({large=false}:{large?:boolean}){
  return <div style={{display:'flex',alignItems:'center',gap:22}}>
    <div style={{background:'#fff',padding:large?'14px 23px':'8px 15px',borderRadius:8,display:'flex'}}>
      <CanvasImage src={staticFile('cri-logo.png')} style={{width:large?285:191,height:'auto'}}/>
    </div>
    {!large&&<div style={{height:30,width:1,background:C.red}}/>}
    {!large&&<div style={{fontSize:15,fontWeight:650,letterSpacing:2.4,lineHeight:1.6,color:C.muted}}>PRODUCTION<br/>PLANNING</div>}
  </div>;
}

function FilmHeader({label}:{label:string}){
  return <div style={{position:'absolute',left:84,right:84,top:36,display:'flex',alignItems:'center',justifyContent:'space-between'}}>
    <Brand/>
    <div style={{color:C.muted,fontSize:18,letterSpacing:.5,display:'flex',alignItems:'center',gap:12}}><span style={{width:6,height:6,borderRadius:'50%',background:C.red}}/>{label}</div>
  </div>;
}

function Captions({captions}:{captions:Caption[]}){
  const f=useCurrentFrame();const {fps}=useVideoConfig();
  const ms=f/fps*1000;
  const line=captions.find(c=>ms>=c.startMs&&ms<c.endMs);
  if(!line)return null;
  return <div data-caption="true" style={{position:'absolute',bottom:27,left:100,right:100,height:58,display:'flex',alignItems:'center',justifyContent:'center',zIndex:30}}>
    <div style={{display:'flex',alignItems:'center',gap:17,padding:'11px 24px',borderRadius:7,background:'#141a22',border:'1px solid #2c3540',maxWidth:1560,opacity:interpolate(ms,[line.startMs,line.startMs+80],[0,1],clamp)}}>
      <span style={{width:3,height:25,background:C.red,borderRadius:4,flexShrink:0}}/>
      <span style={{fontSize:32,fontWeight:500,lineHeight:1.25,whiteSpace:'nowrap',color:'#f3f5f8'}}>{line.text}</span>
    </div>
  </div>;
}

function Title({text,kicker,wide=false}:{text:string;kicker?:string;wide?:boolean}){
  const f=useCurrentFrame();
  return <div style={{position:'absolute',left:84,top:119,right:84,opacity:enter(f,3,20),translate:`0 ${interpolate(f,[3,24],[15,0],{...clamp,easing:ease})}px`}}>
    {kicker&&<div style={{fontSize:16,letterSpacing:2.7,fontWeight:700,color:C.red,marginBottom:14,textTransform:'uppercase'}}>{kicker}</div>}
    <h1 style={{margin:0,fontFamily:'Manrope',fontSize:wide?68:57,fontWeight:750,lineHeight:1.12,letterSpacing:-2.2,whiteSpace:'pre-line'}}>{text}</h1>
  </div>;
}

function Screen({capture,rect,to,progress=0,left=84,top=209,width=1752,height=736,opacity=1}:{capture:string;rect:Rect;to?:Rect;progress?:number;left?:number;top?:number;width?:number;height?:number;opacity?:number}){
  const dest=to||rect;
  const x=interpolate(progress,[0,1],[rect.x,dest.x],clamp);
  const y=interpolate(progress,[0,1],[rect.y,dest.y],clamp);
  const w=interpolate(progress,[0,1],[rect.w,dest.w],clamp);
  const scale=width/w;
  return <div data-screen="true" style={{position:'absolute',left,top,width,height,overflow:'hidden',borderRadius:13,border:'1px solid #414a57',background:'#14171b',boxShadow:'0 26px 70px #0007',opacity}}>
    <CanvasImage src={staticFile(`captures/${capture}.png`)} style={{position:'absolute',width:1600*scale,height:1000*scale,maxWidth:'none',left:-x*scale,top:-y*scale}}/>
    <div style={{position:'absolute',inset:0,borderRadius:13,boxShadow:'inset 0 0 0 1px #ffffff08',pointerEvents:'none'}}/>
  </div>;
}

function ActionTag({text,show=true}:{text:string;show?:boolean}){
  const f=useCurrentFrame();
  if(!show)return null;
  return <div style={{position:'absolute',right:91,top:145,display:'flex',alignItems:'center',gap:9,color:C.muted,fontSize:18,opacity:enter(f,25,15)}}><span style={{width:5,height:5,borderRadius:'50%',background:C.green}}/>{text}</div>;
}

const demoRects:Record<string,{a:Rect;focus:Rect;b?:Rect;bf?:Rect;switchAt?:number}>={
  overview:{a:{x:0,y:120,w:1600},focus:{x:12,y:133,w:1570}},
  orders:{a:{x:0,y:145,w:1600},focus:{x:0,y:230,w:1600},b:{x:0,y:260,w:1600},bf:{x:720,y:410,w:850},switchAt:.33},
  schedule:{a:{x:0,y:165,w:1600},focus:{x:0,y:340,w:1300},b:{x:390,y:30,w:820},bf:{x:390,y:180,w:820},switchAt:.73},
  materials:{a:{x:35,y:350,w:1530},focus:{x:60,y:410,w:1400},b:{x:90,y:190,w:1500},bf:{x:90,y:200,w:1500},switchAt:.59},
  capacity:{a:{x:0,y:340,w:1250},focus:{x:0,y:433,w:920}},
  release:{a:{x:0,y:160,w:1400},focus:{x:0,y:340,w:1140}},
};

function Demo({scene}:{scene:SceneData}){
  const f=useCurrentFrame(); const length=scene.frames;const config=demoRects[scene.id];
  const switchFrame=Math.round(length*(config.switchAt||.6));
  const hasB=Boolean(scene.captureB);
  const bOpacity=hasB?interpolate(f,[switchFrame,switchFrame+9],[0,1],clamp):0;
  const progress=interpolate(f,[length*.2,hasB?switchFrame-15:length*.78],[0,1],{...clamp,easing:Easing.inOut(Easing.cubic)});
  const bProgress=interpolate(f,[switchFrame+25,length*.86],[0,1],{...clamp,easing:Easing.inOut(Easing.cubic)});
  return <>
    <Title text={scene.headline}/>
    <ActionTag text={scene.id==='orders'?'Select a shipment → inspect its orders':scene.id==='schedule'?'Machine board → operation detail':scene.id==='materials'?'Supply → build dependency':'Actual application · dark mode'}/>
    <div style={{opacity:enter(f,8,16),translate:`0 ${interpolate(f,[8,28],[12,0],{...clamp,easing:ease})}px`}}>
      <Screen capture={scene.capture!} rect={config.a} to={config.focus} progress={progress}/>
      {hasB&&<Screen capture={scene.captureB!} rect={config.b!} to={config.bf} progress={bProgress} opacity={bOpacity}/>}
    </div>
    {scene.id==='orders'&&f>20&&f<switchFrame+4&&<svg width="1920" height="960" style={{position:'absolute',pointerEvents:'none'}}>
      <g transform={`translate(${interpolate(f,[25,70],[1350,355],{...clamp,easing:ease})} ${interpolate(f,[25,70],[850,424],{...clamp,easing:ease})})`} opacity={interpolate(f,[20,28,switchFrame-5,switchFrame+4],[0,1,1,0],clamp)}>
        <circle r={interpolate(f,[72,94],[0,35],clamp)} fill="none" stroke={C.red} strokeWidth="3" opacity={interpolate(f,[72,94],[1,0],clamp)}/>
        <path d="M0 0 L1 31 L10 22 L18 38 L25 34 L17 19 L30 18 Z" fill="#fff" stroke="#111820" strokeWidth="2"/>
      </g>
    </svg>}
  </>;
}

function Pain({scene}:{scene:SceneData}){
  const f=useCurrentFrame(); const {fps}=useVideoConfig();
  const cards=[{Icon:Boxes,title:'A component is late',sub:'Assembly cannot start.',at:.8},
    {Icon:Cog,title:'A machine is full',sub:'The next job has nowhere to go.',at:2.9},
    {Icon:Zap,title:'An urgent order arrives',sub:'Other delivery dates move.',at:5}];
  return <>
    <div style={{position:'absolute',left:100,top:236,width:910,opacity:enter(f,5,24)}}>
      <div className="eyebrow">THE SCHEDULING CHALLENGE</div>
      <h1 className="hero-heading">One change.<br/><span style={{color:C.muted}}>A factory-wide<br/>ripple.</span></h1>
      <div style={{width:110,height:5,background:C.red,marginTop:37,scale:`${enter(f,25,30)} 1`,transformOrigin:'left'}}/>
    </div>
    <svg width="1920" height="960" style={{position:'absolute'}}>
      <path d="M1025 320 L1070 320 L1070 670 L1120 670" fill="none" stroke={C.red} strokeWidth="2" strokeDasharray="480" strokeDashoffset={480*(1-enter(f,30,140))}/>
      <path d="M1070 495 L1120 495" stroke={C.red} strokeWidth="2" opacity={enter(f,85,30)}/>
    </svg>
    {cards.map(({Icon,title,sub,at},i)=><div key={title} style={{position:'absolute',left:1120,top:235+i*178,width:670,height:145,borderRadius:18,border:'1px solid #4a303c',background:'linear-gradient(115deg,#211b25,#161c24)',display:'flex',alignItems:'center',gap:25,padding:'26px 30px',opacity:enter(f,at*fps,20),translate:`${36*(1-enter(f,at*fps,28))}px 0`}}>
      <div style={{width:67,height:67,borderRadius:16,background:'#ff40551b',display:'grid',placeItems:'center',flexShrink:0}}><Icon size={34} strokeWidth={1.7} color={C.red}/></div>
      <div><div style={{fontFamily:'Manrope',fontSize:29,fontWeight:700,letterSpacing:-.5}}>{title}</div><div style={{fontSize:21,color:C.muted,marginTop:10}}>{sub}</div></div>
    </div>)}
    <div style={{position:'absolute',left:1124,top:814,fontSize:34,fontFamily:'Manrope',fontWeight:650,color:C.white,opacity:enter(f,8.5*fps,24)}}>Which commitment is now at risk?</div>
  </>;
}

function Connected({scene}:{scene:SceneData}){
  const f=useCurrentFrame();const {fps}=useVideoConfig();
  const converge=enter(f,6.4*fps,60);
  return <>
    <div style={{position:'absolute',left:100,top:190,opacity:enter(f,5,25)}}><div className="eyebrow">ONE PLANNING CONTEXT</div><h1 style={{font:'750 84px/1.13 Manrope',letterSpacing:-3,margin:'18px 0'}}>Stop chasing updates.<br/><span style={{color:C.muted}}>Start seeing the plan.</span></h1></div>
    <svg width="1920" height="960" style={{position:'absolute'}}>
      {[[420,610],[960,610],[1500,610]].map(([x,y],i)=><path key={i} d={`M${x} ${y} L${x} 815 L960 815 L960 726`} fill="none" stroke={converge>.5?C.red:'#4a5564'} strokeWidth="2" strokeDasharray="1200" strokeDashoffset={1200*(1-enter(f,(i+1)*fps,60))}/>)}
    </svg>
    {[{Icon:ClipboardList,label:'Orders',sub:'What must be delivered?'},{Icon:Boxes,label:'Materials',sub:'What is actually available?'},{Icon:Factory,label:'Capacity',sub:'What can the factory run?'}].map(({Icon,label,sub},i)=><div key={label} style={{position:'absolute',left:170+i*540,top:495,width:500,height:180,padding:'30px 35px',borderRadius:18,background:C.surface,border:`1px solid ${converge>.5?'#79404e':C.line}`,opacity:enter(f,20+i*20,28),translate:`0 ${24*(1-enter(f,20+i*20,28))}px`}}><Icon size={33} color={C.red}/><div style={{font:'700 31px Manrope',marginTop:13}}>{label}</div><div style={{fontSize:20,color:C.muted,marginTop:8}}>{sub}</div></div>)}
    <div style={{position:'absolute',left:700,top:754,width:520,height:122,background:C.deep,borderRadius:18,boxShadow:'0 15px 80px #c8102e20',display:'flex',alignItems:'center',justifyContent:'center',gap:22,opacity:enter(f,6.1*fps,25),scale:interpolate(f,[6.1*fps,7.2*fps],[.94,1],{...clamp,easing:ease})}}><Workflow size={46}/><div><strong style={{font:'750 34px Manrope'}}>One connected plan</strong><div style={{fontSize:19,opacity:.8,marginTop:6}}>Advanced Planning & Scheduling</div></div></div>
  </>;
}

function Proof({scene}:{scene:SceneData}){
  const f=useCurrentFrame(); const {fps}=useVideoConfig();
  return <>
    <div style={{position:'absolute',left:100,top:169,opacity:enter(f,5,22)}}><div className="eyebrow">DEMONSTRATION PLAN · 19 OCTOBER 2026</div><h1 style={{font:'750 66px/1.15 Manrope',letterSpacing:-2.5,margin:'16px 0'}}>One plan. Every connection accounted for.</h1></div>
    {[{value:10,label:'shipments',Icon:Truck},{value:670,label:'pumps',Icon:PackageCheck},{value:260,label:'machine operations',Icon:Cog}].map(({value,label,Icon},i)=><div key={label} style={{position:'absolute',left:100+i*575,top:327,width:540,display:'flex',alignItems:'center',gap:27,opacity:enter(f,35+i*12,22)}}><div style={{width:72,height:72,background:'#172820',borderRadius:16,display:'grid',placeItems:'center'}}><Icon size={35} color={C.green}/></div><div><div style={{font:'750 77px/1.1 Manrope',letterSpacing:-3,fontVariantNumeric:'tabular-nums'}}>{Math.round(interpolate(f,[35+i*12,65+i*12],[0,value],{...clamp,easing:ease}))}</div><div style={{fontSize:22,color:C.muted,marginTop:8}}>{label}</div></div></div>)}
    <Screen capture="overview" rect={{x:0,y:270,w:1600}} top={520} width={1752} height={424}/>
    <div style={{position:'absolute',right:111,top:467,display:'flex',alignItems:'center',gap:11,color:C.green,fontSize:21,opacity:enter(f,8.3*fps,22)}}><CheckCheck size={27}/>10 / 10 within capacity-checked due dates</div>
    <div style={{position:'absolute',left:110,top:914,fontSize:17,color:'#bcc8d5',background:'#111820ef',padding:'6px 11px',borderRadius:5}}>Results shown apply to this demonstration plan.</div>
  </>;
}

function Value({scene}:{scene:SceneData}){
  const f=useCurrentFrame();const {fps}=useVideoConfig();
  const cards=[{Icon:ClipboardList,role:'PLANNERS',title:'Clearer priorities.',copy:'Move from disconnected updates\nto decisions in context.'},
    {Icon:Factory,role:'PRODUCTION',title:'A shared sequence.',copy:'Know the machine, the operation\nand the work that comes next.'},
    {Icon:Users,role:'CUSTOMER TEAMS',title:'Grounded promises.',copy:'Discuss delivery dates with\nthe evidence behind them.'}];
  return <>
    <div style={{position:'absolute',left:100,top:190,opacity:enter(f,5,25)}}><div className="eyebrow">FROM EXPEDITING TO INFORMED DECISIONS</div><h1 style={{font:'750 84px/1.14 Manrope',letterSpacing:-3,margin:'15px 0'}}>Clear priorities.<br/><span style={{color:C.muted}}>Stronger delivery conversations.</span></h1></div>
    {cards.map(({Icon,role,title,copy},i)=><div key={role} style={{position:'absolute',left:100+i*580,top:480,width:550,height:348,padding:35,borderRadius:20,background:'linear-gradient(135deg,#20262f,#141a22)',border:'1px solid #394451',opacity:enter(f,20+i*25,28),translate:`0 ${30*(1-enter(f,20+i*25,28))}px`}}><Icon size={42} color={i===2?C.green:C.red} strokeWidth={1.6}/><div style={{fontSize:14,letterSpacing:2.4,color:C.muted,marginTop:30,fontWeight:650}}>{role}</div><h2 style={{font:'700 34px Manrope',margin:'15px 0',letterSpacing:-1}}>{title}</h2><p style={{fontSize:23,lineHeight:1.6,color:'#abb7c6',whiteSpace:'pre-line',margin:0}}>{copy}</p></div>)}
    <div style={{position:'absolute',left:100,top:874,display:'flex',alignItems:'center',gap:16,fontSize:26,opacity:enter(f,10*fps,25)}}><span style={{width:55,height:2,background:C.red}}/>Less guesswork. More informed decisions.</div>
  </>;
}

function Close({scene}:{scene:SceneData}){
  const f=useCurrentFrame(); const {fps}=useVideoConfig();
  return <>
    <div style={{position:'absolute',left:0,right:0,top:175,display:'flex',justifyContent:'center',opacity:enter(f,8,25)}}><Brand large/></div>
    <h1 style={{position:'absolute',left:100,right:100,top:330,textAlign:'center',font:'750 102px/1.14 Manrope',letterSpacing:-4,margin:0,opacity:enter(f,16,30)}}>Let’s prove it<br/><span style={{color:'#a4b0bf'}}>on your factory data.</span></h1>
    <div style={{position:'absolute',left:450,right:450,top:644,height:86,borderRadius:13,background:C.deep,display:'flex',alignItems:'center',justifyContent:'center',gap:20,fontSize:30,fontWeight:650,opacity:enter(f,2*fps,25),scale:interpolate(f,[2*fps,3*fps],[.97,1],{...clamp,easing:ease})}}>Start a focused APS pilot<ArrowUpRight size={33}/></div>
    <div style={{position:'absolute',left:100,right:100,top:795,display:'flex',justifyContent:'center',alignItems:'center',gap:25,fontSize:23,color:C.muted}}>
      <span style={{opacity:enter(f,3*fps,20)}}>One product family</span><ArrowRight size={24} style={{opacity:enter(f,4*fps,20)}}/>
      <span style={{opacity:enter(f,5*fps,20)}}>Validated constraints</span><ArrowRight size={24} style={{opacity:enter(f,6*fps,20)}}/>
      <span style={{opacity:enter(f,7*fps,20),color:C.white}}>A scalable planning process</span>
    </div>
  </>;
}

function Scene({index}:{index:number}){
  const scene=scenes[index]; const f=useCurrentFrame();const {fps}=useVideoConfig();
  const fade=interpolate(f,[0,7,scene.frames-7,scene.frames],[0,1,1,0],clamp);
  return <AbsoluteFill style={{fontFamily:'Inter',color:C.white,opacity:fade}}>
    <Backdrop hero={scene.kind!=='demo'}/>
    {scene.kind!=='close'&&<FilmHeader label={scene.kind==='demo'?scene.label:'Advanced Planning & Scheduling'}/>}
    {scene.kind==='pain'?<Pain scene={scene}/>:scene.kind==='connect'?<Connected scene={scene}/>:scene.kind==='demo'?<Demo scene={scene}/>:scene.kind==='proof'?<Proof scene={scene}/>:scene.kind==='value'?<Value scene={scene}/>:<Close scene={scene}/>}
    <Sequence from={Math.round(scene.audioOffset*fps)} layout="none"><Audio src={staticFile(scene.audio)}/></Sequence>
    <Captions captions={scene.captions}/>
  </AbsoluteFill>;
}

const Pressure=()=> <Scene index={0}/>;
const Connection=()=> <Scene index={1}/>;
const Overview=()=> <Scene index={2}/>;
const Orders=()=> <Scene index={3}/>;
const Scheduling=()=> <Scene index={4}/>;
const Materials=()=> <Scene index={5}/>;
const Capacity=()=> <Scene index={6}/>;
const Release=()=> <Scene index={7}/>;
const Evidence=()=> <Scene index={8}/>;
const Benefits=()=> <Scene index={9}/>;
const Closing=()=> <Scene index={10}/>;
export const PressureScene=Interactive.create({Component:Pressure,layout:'absolute-fill'});
export const ConnectionScene=Interactive.create({Component:Connection,layout:'absolute-fill'});
export const OverviewScene=Interactive.create({Component:Overview,layout:'absolute-fill'});
export const OrdersScene=Interactive.create({Component:Orders,layout:'absolute-fill'});
export const ScheduleScene=Interactive.create({Component:Scheduling,layout:'absolute-fill'});
export const MaterialsScene=Interactive.create({Component:Materials,layout:'absolute-fill'});
export const CapacityScene=Interactive.create({Component:Capacity,layout:'absolute-fill'});
export const ReleaseScene=Interactive.create({Component:Release,layout:'absolute-fill'});
export const ProofScene=Interactive.create({Component:Evidence,layout:'absolute-fill'});
export const ValueScene=Interactive.create({Component:Benefits,layout:'absolute-fill'});
export const CloseScene=Interactive.create({Component:Closing,layout:'absolute-fill'});

export const CRIPumpsPitch=()=> <AbsoluteFill style={{background:C.bg}}>
  <Track name="Pitch and product walkthrough">
    <PressureScene name="Scheduling pressure" from={scenes[0].from} durationInFrames={scenes[0].frames}/>
    <ConnectionScene name="Connected planning" from={scenes[1].from} durationInFrames={scenes[1].frames}/>
    <OverviewScene name="Actual shipment overview" from={scenes[2].from} durationInFrames={scenes[2].frames}/>
    <OrdersScene name="Actual order filtering" from={scenes[3].from} durationInFrames={scenes[3].frames}/>
    <ScheduleScene name="Actual machine board and job" from={scenes[4].from} durationInFrames={scenes[4].frames}/>
    <MaterialsScene name="Actual supply and dependencies" from={scenes[5].from} durationInFrames={scenes[5].frames}/>
    <CapacityScene name="Actual capacity evidence" from={scenes[6].from} durationInFrames={scenes[6].frames}/>
    <ReleaseScene name="Actual release review" from={scenes[7].from} durationInFrames={scenes[7].frames}/>
    <ProofScene name="Demonstration results" from={scenes[8].from} durationInFrames={scenes[8].frames}/>
    <ValueScene name="Operational value" from={scenes[9].from} durationInFrames={scenes[9].frames}/>
    <CloseScene name="Pilot invitation" from={scenes[10].from} durationInFrames={scenes[10].frames}/>
  </Track>
  <Audio name="Original quiet instrumental bed" src={staticFile('score.mp3')} volume={1}/>
</AbsoluteFill>;
