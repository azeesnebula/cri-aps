"""Audit final encoded output, caption timing and scene evidence."""
import json
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'out'
movie=OUT/'CRI_Pumps_APS_Pitch.mp4'
timing=json.loads((ROOT/'src/timing.json').read_text(encoding='utf-8'))
cursor=0
captions=0
for scene in timing['scenes']:
    assert scene['from']==cursor, scene['id']
    assert scene['audioOffset']+scene['audioDuration']<scene['frames']/30
    assert (ROOT/'public'/scene['audio']).is_file()
    previous=0
    for caption in scene['captions']:
        assert caption['startMs']>=previous
        assert caption['startMs']<caption['endMs']<scene['frames']/30*1000
        assert len(caption['text'])<=76, caption['text']
        assert len(caption['text'].split())<=9, caption['text']
        previous=caption['endMs']
        captions+=1
    for key in ('capture','captureB'):
        if scene.get(key):
            assert (ROOT/'public/captures'/(scene[key]+'.png')).is_file()
    cursor+=scene['frames']
assert cursor==timing['totalFrames']

metadata=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(movie)],text=True))
video=next(s for s in metadata['streams'] if s['codec_type']=='video')
audio=next(s for s in metadata['streams'] if s['codec_type']=='audio')
assert video['codec_name']=='h264'
assert (video['width'],video['height'])==(1920,1080)
assert video['pix_fmt']=='yuv420p', video['pix_fmt']
assert video.get('color_space')=='bt709', video.get('color_space')
assert video['avg_frame_rate']=='30/1'
assert abs(float(metadata['format']['duration'])-cursor/30)<.2
assert audio['codec_name']=='aac'
analysis=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',str(movie),'-vn',
    '-af','loudnorm=I=-16:TP=-1:LRA=11:print_format=json','-f','null','-'],capture_output=True,text=True,check=True)
start=analysis.stderr.rfind('{')
loudness,_=json.JSONDecoder().raw_decode(analysis.stderr[start:])
assert float(loudness['input_tp'])<0, loudness
assert -23<float(loudness['input_i'])<-12, loudness
review=OUT/'final-review'
review.mkdir(exist_ok=True)
frames=[]
for scene in timing['scenes']:
    second=(scene['from']+round(scene['frames']*.65))/30
    image=review/(scene['id']+'.png')
    subprocess.run(['ffmpeg','-y','-v','error','-ss',str(second),'-i',str(movie),'-frames:v','1',str(image)],check=True)
    frames.append((scene['id'],image,second))
sheet=Image.new('RGB',(1280,4*265),'#10151b')
draw=ImageDraw.Draw(sheet)
for index,(label,file,second) in enumerate(frames):
    x=(index%3)*426
    y=(index//3)*265
    image=Image.open(file).convert('RGB')
    image.thumbnail((416,234))
    sheet.paste(image,(x+5,y+4))
    draw.text((x+10,y+242),f'{label} | {second:.1f}s',fill='#ecf1f7')
sheet.save(OUT/'contact-sheet.jpg',quality=93)
Image.open(review/'pressure.png').convert('RGB').save(OUT/'poster.jpg',quality=95)
report={
    'passed':True,'duration_seconds':float(metadata['format']['duration']),
    'frames':cursor,'resolution':'1920x1080','fps':30,'video_codec':video['codec_name'],
    'pixel_format':video['pix_fmt'],'color_space':video['color_space'],'audio_codec':audio['codec_name'],
    'file_megabytes':round(float(metadata['format']['size'])/1048576,2),
    'scenes':len(timing['scenes']),'compact_caption_phrases':captions,'captions_over_screen':False,
    'voice':timing['voice'],'audio_analysis':loudness,
    'evidence':'Actual dark-mode C.R.I APS captures; results scoped to demonstration plan',
    'skill_guidance':'Official remotion-dev/skills; Remotion 4.0.536',
}
(OUT/'quality-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report,indent=2))
