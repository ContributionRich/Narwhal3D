"""Actual Picture/PDF host regression; fixture generated and checked by Rust tests.

Python is only Blender/SDK command, image, Undo and save orchestration. All PDF
parsing/rendering/resource/cache policy belongs to the Rust helper and Poppler.
"""
from pathlib import Path
import json
import os
import shutil
import sys
import tempfile
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
pkg = types.ModuleType('narwhal3d'); pkg.__path__ = [str(ROOT / 'narwhal3d')]
sys.modules['narwhal3d'] = pkg
sys.path.insert(0, str(ROOT))
import bpy
import rhino3dm as r3
from narwhal3d import bridge, commands, converter_process, filedialog, picture, trace_picture
from narwhal3d import cplane, narwhal_document
from narwhal3d.model import NarwhalModel
from narwhal3d.state import app_state

checks = 0

def check(ok, message):
    global checks
    checks += 1
    assert ok, message


def main():
    with tempfile.TemporaryDirectory(prefix='narwhal-picture-pdf-') as directory:
        work = Path(directory)
        source = work / 'page selection.PDF'
        shutil.copyfile(ROOT / 'native/picture_pdf/tests/fixtures/two-pages.pdf', source)
        with patch.dict(os.environ, {'XDG_DATA_HOME': str(work / 'persistent-data')}):
            image, info, notice = picture.prepare_image(str(source))
            check(info.format == 'PDF' and info.aspect == 2., 'PDF physical aspect preserved')
            check(info.print_size_inches() == (2., 1.), 'PDF physical print size preserved')
            check('page 1 of 2' in notice and '1 other page' in notice, 'multi-page policy explicit')
            check(any('*.pdf' in glob for _, glob in picture.FILTERS), 'file picker offers PDF')
            check(picture.prepare_image(str(source))[0] == image, 'stable content-addressed persistent image')
            raster = bpy.data.images.load(image, check_existing=True)
            pixel = tuple(raster.pixels[(raster.size[1]//2*raster.size[0]+raster.size[0]//2)*4:][:4])
            check(pixel[0] > .9 and pixel[1] < .1 and pixel[2] < .1, 'page1 red artwork, not page2 blue')
            bpy.data.batch_remove(list(bpy.data.objects))
            app_state.set_model(NarwhalModel()); ctx = app_state.ctx
            ctx.model.file.Settings.ModelUnitSystem = r3.UnitSystem.Millimeters
            plane = cplane.CPlane((10.,20.,30.), (0.,1.,0.), (0.,0.,1.))
            with patch.object(ctx, 'active_cplane', return_value=plane), patch.object(
                    filedialog, 'ask_open_path', return_value=(filedialog.OK, str(source))):
                cmd = commands.PictureCmd()
                message, done = cmd.begin(ctx)
                check(not done and 'page 1 of 2' in message, 'browse announces PDF policy before placement')
                ctx.begin_undo('Picture')
                cmd.on_mode('1to1', ctx)
                message, done = cmd.feed('0,0,0', ctx)
                ctx.end_undo()
            check(done and 'page 1 of 2' in message, 'placed Picture reports raster/page policy')
            objects = list(ctx.model.file.Objects)
            check(len(objects) == 1, 'one PDF produces one Picture, not vector geometry')
            obj = objects[0]; name = obj.Attributes.Name
            frame = bridge.picture_frame(obj.Geometry)
            check(isinstance(obj.Geometry, r3.Brep) and len(obj.Geometry.Faces) == 1, 'exact single planar face')
            check(frame['image'] == image and tuple(frame['origin']) == (10.,20.,30.), 'persistent texture and CPlane origin')
            check(all(abs(a-b)<1e-10 for a,b in zip(frame['u'],(0.,50.8,0.))), '1to1 exact page width on CPlane')
            check(all(abs(a-b)<1e-10 for a,b in zip(frame['v'],(0.,0.,25.4))), '1to1 exact page height on CPlane')
            check(ctx.undo_last() and not list(ctx.model.file.Objects), 'one Undo removes whole Picture')
            check(ctx.redo_last() and len(list(ctx.model.file.Objects)) == 1, 'Redo restores Picture and asset')
            source.unlink()
            check(Path(image).is_file(), 'source deletion never removes persistent raster')
            obj = list(ctx.model.file.Objects)[0]
            before = bridge.picture_frame(obj.Geometry)
            ctx.begin_undo('Move Picture');ctx.note_change()
            moved = bridge.transform_geometry(obj.Geometry, r3.Transform.Translation(3,4,5))
            ctx.model.replace_geometry(obj.Attributes.Id,moved)
            ctx.rebuild_display();ctx.end_undo()
            after = bridge.picture_frame(list(ctx.model.file.Objects)[0].Geometry)
            check(tuple(after['origin']) == (13.,24.,35.), 'supported exact transform moves image frame')
            check(after['image'] == image, 'move retains persistent raster')
            ctx.undo_last()
            check(bridge.picture_frame(list(ctx.model.file.Objects)[0].Geometry) == before, 'move Undo restores frame')
            for extension in ('3dm','narwhal'):
                saved = work / ('reference.'+extension)
                if extension == '3dm':
                    check(ctx.model.save(str(saved)), '3dm save after source removal')
                    reopened = NarwhalModel.load(str(saved))
                else:
                    check(narwhal_document.save_model(ctx.model,saved), 'native save after source removal')
                    reopened = narwhal_document.load_model(saved)
                saved_frame = bridge.picture_frame(list(reopened.file.Objects)[0].Geometry)
                check(saved_frame['image'] == image and Path(saved_frame['image']).is_file(), 'reload retains persistent PDF image')
            helper = trace_picture.helper_path()
            check(helper is not None, 'qualified existing TracePicture helper is present')
            settings = dict(offset=0., min_hole=0., inner_cuts=True, background='Auto', color='#ffffff', color_tolerance=12., smoothness=40.)
            args = trace_picture.helper_args(image,info.width,before,1/25.4,settings)
            controls = trace_picture.loop_controls(trace_picture.trace(helper,args),before)
            check(bool(controls), 'TracePicture consumes PDF raster and produces cut controls')
            # Every refusal happens before document publication or an Undo capture.
            bad = work / 'corrupt.pdf';bad.write_bytes(b'%PDF-1.4\ncorrupt')
            encrypted = work / 'encrypted.pdf'
            locked = converter_process.run(['/usr/bin/mutool','clean','-E','aes-256','-O','owner','-U','user',
                str(ROOT / 'native/picture_pdf/tests/fixtures/two-pages.pdf'),str(encrypted)],
                timeout=10,memory_mib=512)
            check(locked.returncode == 0, 'actual encrypted negative fixture created by MuPDF')
            oversized = work / 'oversized.pdf'
            with oversized.open('wb') as stream:
                stream.truncate(64*1024*1024+1)
            signature = ctx.model.file.Encode()
            for value in (str(bad), str(work/'missing.pdf'),str(encrypted),str(oversized)):
                with patch.object(filedialog,'ask_open_path',return_value=(filedialog.OK,value)):
                    message,done = commands.PictureCmd().begin(ctx)
                check(done and message.startswith('Picture:'), 'bad PDF reports named refusal')
                check(ctx.model.file.Encode() == signature, 'bad PDF does not mutate document')
            timeout = converter_process.Result(-1,'','',timed_out=True,error='timed out')
            with patch.object(converter_process,'run',return_value=timeout), patch.object(
                    filedialog,'ask_open_path',return_value=(filedialog.OK,str(bad))):
                message,done = commands.PictureCmd().begin(ctx)
            check(done and 'timed out' in message and ctx.model.file.Encode()==signature, 'timeout refuses before publication')
            with patch.object(filedialog,'ask_open_path',return_value=(filedialog.CANCEL,None)):
                message,done=commands.PictureCmd().begin(ctx)
            check(done and 'cancelled' in message and ctx.model.file.Encode()==signature, 'browse cancellation changes nothing')
            check(ctx.undo_last() and not list(ctx.model.file.Objects), 'all refusals/cancellation added no Undo record')
    print(f'PDF PICTURE PASSED ({checks} checks)')

if __name__ == '__main__':
    main()
