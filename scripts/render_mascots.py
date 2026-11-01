"""Render authoritative SVG masters with transparency, not opaque previews.
Requires librsvg's rsvg-convert and Pillow. Run from any directory.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'resource/imoticon/svg'
DEST = ROOT / 'android/app/src/main/res/drawable'

def verify(path):
    with Image.open(path) as image:
        assert image.mode == 'RGBA', f'{path.name}: no alpha channel'
        assert image.size == (512, 512), f'{path.name}: incorrect size'
        alpha = image.getchannel('A')
        assert alpha.getpixel((0, 0)) == 0, f'{path.name}: opaque corner'
        bounds = alpha.getbbox()
        assert bounds is not None, f'{path.name}: empty image'
        x0, y0, x1, y1 = bounds
        assert x1 - x0 > 300 and y1 - y0 > 300, f'{path.name}: SVG was not scaled to canvas'
        assert abs((x0 + x1) / 2 - 256) < 40, f'{path.name}: off-center'
        for box in ((0, 0, 512, 4), (0, 508, 512, 512), (0, 0, 4, 512), (508, 0, 512, 512)):
            assert alpha.crop(box).getextrema() == (0, 0), f'{path.name}: opaque edge'

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    sources = sorted(SOURCE.glob('*.svg'))
    assert len(sources) == 16, 'Expected exactly 16 expression masters'
    records = []
    for source in sources:
        target = DEST / ('mascot_' + source.stem.replace('-', '_') + '.png')
        if not args.check:
            subprocess.run(['rsvg-convert', '-w', '512', '-h', '512', '-o', str(target), str(source)], check=True)
        verify(target)
        records.append({'source': str(source.relative_to(ROOT)), 'target': str(target.relative_to(ROOT)), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'target_sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
    if not args.check:
        (ROOT / 'docs/mascot-assets.json').write_text(json.dumps(records, indent=2) + '\n')
    print(f'PASS: {len(records)} SVG-derived assets; RGBA, centered 512px, transparent edges')

if __name__ == '__main__':
    main()
