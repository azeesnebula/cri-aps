"""Assemble the corrected overview with the existing surrounding edit."""
import json
import shutil
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
out=ROOT/'out'
timing=json.loads((ROOT/'src/timing.json').read_text(encoding='utf-8'))
scene=next(s for s in timing['scenes'] if s['id']=='overview')
start=scene['from']/timing['fps']
duration=scene['frames']/timing['fps']
end=start+duration
total=timing['totalFrames']/timing['fps']
movie=out/'CRI_Pumps_APS_Pitch.mp4'
corrected=out/'CRI_Pumps_APS_Pitch_Corrected.mp4'
filters=(f'[0:v]trim=end_frame={scene["from"]},setpts=PTS-STARTPTS[v0];'
         f'[0:a]atrim=end={start},asetpts=PTS-STARTPTS[a0];'
         f'[1:v]trim=end_frame={scene["frames"]},setpts=PTS-STARTPTS[v1];'
         f'[1:a]atrim=end={duration},asetpts=PTS-STARTPTS[a1];'
         f'[0:v]trim=start_frame={scene["from"]+scene["frames"]},setpts=PTS-STARTPTS[v2];'
         f'[0:a]atrim=start={end}:end={total},asetpts=PTS-STARTPTS[a2];'
         '[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[v][a]')
subprocess.run(['ffmpeg','-y','-v','error','-i',str(movie),'-i',str(out/'overview-corrected.mp4'),
                '-filter_complex',filters,'-map','[v]','-map','[a]',
                '-c:v','libx264','-preset','fast','-crf','18','-pix_fmt','yuv420p',
                '-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709',
                '-c:a','aac','-b:a','192k','-movflags','+faststart',str(corrected)],check=True)
info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration',
                                        '-of','json',str(corrected)],text=True))
assert abs(float(info['format']['duration'])-total)<.1
backup=out/'CRI_Pumps_APS_Pitch_Previous.mp4'
if not backup.exists():
    shutil.copy2(movie,backup)
shutil.copy2(corrected,movie)
shutil.copy2(ROOT/'public/captions.srt',out/'CRI_Pumps_APS_Pitch.srt')
print('Corrected complete video:',corrected)
