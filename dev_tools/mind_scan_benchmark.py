"""固定输入的心智扫描离线基准；不连接设备、不读取实例配置。

阶段是包含计时：debug_io/model_inference/queue_execution 属于 OCR 调用，
ocr_queue_wall 又属于 name_first/name_retry/level_fallback，不能重复相加。
合成滚动只测 CPU 位移与合并；实机截图、拖动、稳定等待和端到端耗时不可据此推断。
"""
import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import platform
import statistics
import subprocess
import textwrap
from time import perf_counter
from unittest.mock import patch

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('module/ocr/al_ocr.py', 'module/runtime/mind_recognition.py', 'module/retire/mind_scan.py')


class Measure:
    def __init__(self):
        self.seconds = defaultdict(float)
        self.calls = Counter()

    def wrap(self, key, func):
        def measured(*args, **kwargs):
            start = perf_counter()
            self.calls[key] += 1
            try:
                return func(*args, **kwargs)
            finally:
                self.seconds[key] += perf_counter() - start
        return measured

    def result(self):
        return {'seconds_inclusive': dict(self.seconds), 'counts': dict(self.calls)}


def load_baseline(folder):
    # 只替换快照中的目标模块，资料、依赖及其余功能沿用同一工作区。
    for rel in SOURCES:
        name = rel[:-3].replace('/', '.')
        spec = importlib.util.spec_from_file_location(name, folder / rel)
        module = importlib.util.module_from_spec(spec)
        import sys
        sys.modules[name] = module
        spec.loader.exec_module(module)
        # 快照保存源码，不另复制模型和模板；资源仍来自同一被测工作区。
        module.__file__ = str(ROOT / rel)
        if hasattr(module, 'REPO_ROOT'):
            module.REPO_ROOT = ROOT
        setattr(importlib.import_module(name.rsplit('.', 1)[0]), name.rsplit('.', 1)[1], module)


def instrument(stack, measure, al, rec, debug_dir, debug_off):
    from module.runtime.mind_level import DockLevelReader
    from rapidocr.inference_engine.onnxruntime.main import OrtInferSession
    # 保留原例程的编码、枚举与清理，仅把输出位置隔离到本次产物目录。
    code = textwrap.dedent(inspect.getsource(al.AlOcr._save_debug_image))
    if 'folder = "ocr_debug"' not in code:
        raise ValueError('诊断图例程已变化，请核对输出隔离后再运行基准')
    code = code.replace('folder = "ocr_debug"', f'folder = {str(debug_dir)!r}')
    namespace = {}
    exec(code, vars(al), namespace)
    save = namespace['_save_debug_image']
    if debug_off:
        save = lambda *args, **kwargs: None
    stack.enter_context(patch.object(al.AlOcr, '_save_debug_image', measure.wrap('debug_io', save)))
    for target, name, key in ((al.cv2, 'imwrite', 'debug_write'), (al.os, 'listdir', 'debug_listdir'),
                              (al.os.path, 'getmtime', 'debug_stat'), (al.os, 'remove', 'debug_remove')):
        original = getattr(target, name)
        def tracked(path, *args, _original=original, _key=key, **kwargs):
            if str(path).startswith(str(debug_dir)):
                return measure.wrap(_key, _original)(path, *args, **kwargs)
            return _original(path, *args, **kwargs)
        stack.enter_context(patch.object(target, name, tracked))
    stack.enter_context(patch.object(al, '_run_ocr_queued', measure.wrap('ocr_queue_wall', al._run_ocr_queued)))
    stack.enter_context(patch.object(al._OcrJob, 'run', measure.wrap('queue_execution', al._OcrJob.run)))
    stack.enter_context(patch.object(OrtInferSession, '__call__', measure.wrap('model_inference', OrtInferSession.__call__)))
    for name in ('normalize_screenshot', 'grid_rows', 'estimate_scroll', 'fleet_status', '_match_name'):
        stack.enter_context(patch.object(rec, name, measure.wrap(name, getattr(rec, name))))
    for name in ('prepare', 'read'):
        stack.enter_context(patch.object(DockLevelReader, name, measure.wrap('level_' + name, getattr(DockLevelReader, name))))
    read = DockLevelReader.read
    def count_hits(*args):
        value = read(*args)
        measure.calls['level_template_hit' if value else 'level_template_miss'] += 1
        return value
    stack.enter_context(patch.object(DockLevelReader, 'read', count_hits))
    for name in ('add', 'ships'):
        stack.enter_context(patch.object(rec.ScanMerger, name, measure.wrap('merge_' + name, getattr(rec.ScanMerger, name))))
    stack.enter_context(patch.object(rec.ScanPolicy, 'can_stop', measure.wrap('sort_stop', rec.ScanPolicy.can_stop)))


class Reader:
    def __init__(self, reader, measure, kind):
        self.reader, self.measure, self.kind = reader, measure, kind
        self.first = True

    def ocr_for_single_lines(self, images):
        key = ('name_first' if self.first else 'name_retry') if self.kind == 'name' else 'level_fallback'
        self.first = False
        self.measure.calls[key + '_images'] += len(images)
        return self.measure.wrap(key, self.reader.ocr_for_single_lines)(images)

    def det(self, image):
        return self.measure.wrap('row_detection', self.reader.det)(image)


def recognize(image, al, rec, measure, settings, source):
    options = {'save_debug_images': False} if 'save_debug_images' in inspect.signature(al.AlOcr).parameters else {}
    names = Reader(al.AlOcr(name='ppocr_v6', settings=settings, **options), measure, 'name')
    levels = Reader(al.AlOcr(name='azur_lane', settings=settings, **options), measure, 'level')
    start = perf_counter()
    cards = rec.recognize_cards(image, source, name_ocr=names, level_ocr=levels)
    measure.seconds['recognition_wall'] += perf_counter() - start
    measure.calls['cards'] += len(cards)
    measure.calls['name_corrected'] += sum('名单校正' in c.ship['source'] for c in cards)
    measure.calls['level_unreliable'] += sum(not c.level_reliable for c in cards)
    from module.runtime.mind_calculator import calculate
    merger = rec.ScanMerger()
    merger.add(image, cards)
    policy = rec.ScanPolicy(95, 120, sorted_desc=True)
    stopped = policy.can_stop(cards, merger.slots)
    ships = merger.ships(95, 120)
    report = measure.wrap('calculate', calculate)(ships)
    return {'cards': [asdict(c) for c in cards], 'slots': [[asdict(c), y] for c, y in merger.slots],
            'ships': ships, 'report': report, 'range_stop': stopped}


def scroll_benchmark(rec, measure):
    # 与控制流程测试同种固定纹理；不伪称实机滚动录像。
    offsets = [0, 100, 227, 454, 681, 690, 681, 908, 908]
    content = np.random.default_rng(20261010).integers(30, 230, (1483, 1140, 3), dtype=np.uint8)
    merger, shifts = rec.ScanMerger(), []
    for offset in offsets:
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[65:640, 85:1225] = content[offset:offset + 575]
        shifts.append(merger.advance(Image.fromarray(frame)))
    if shifts != [0, 100, 127, 227, 227, 9, -9, 227, 0] or merger.offset != 908:
        raise AssertionError(f'位移核验失配：{shifts}, offset={merger.offset}')
    return {'offsets': offsets, 'shifts': shifts, 'final_offset': merger.offset}


def recorded_scroll(rec, paths):
    frames = [np.array(Image.open(path).convert('RGB')) for path in paths]
    def displacement(a, b):
        try:
            return rec.estimate_scroll(a, b)
        except ValueError as exc:
            return {'rejected': str(exc)}
    return {'pairs': [{'from': str(paths[i]), 'to': str(paths[i + 1]),
                       'forward': displacement(a, b), 'reverse': displacement(b, a)}
                      for i, (a, b) in enumerate(zip(frames, frames[1:]))],
            'stationary': [displacement(frame, frame) for frame in frames]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--repeat', type=int, default=5)
    parser.add_argument('--debug-off', action='store_true', help='仅基准消融：跳过单行诊断图 I/O')
    parser.add_argument('--image', type=Path, action='append')
    parser.add_argument('--scroll-image', type=Path, action='append', default=[], help='已有录像帧，按相邻顺序指定')
    parser.add_argument('--compare', type=Path, help='比较同输入基线 report.json 的全部输出及位移/拒绝结果')
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error('--repeat 至少为 1')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    imported_at = perf_counter()
    if args.baseline:
        load_baseline(args.baseline.resolve())
    import module.config.server as server
    server.server = 'cn'
    from module.ocr import al_ocr as al
    from module.runtime import mind_recognition as rec
    from module.logger import logger
    # 基线加载了任务模块；当前版本也须预先导入，避免把不同惰性 import 算入 cold。
    importlib.import_module('module.retire.mind_scan')
    import_seconds = perf_counter() - imported_at
    logger.set_file_logger(str(output / 'run.log'))
    paths = args.image or [ROOT / 'tests/fixtures/mind_dock_fleet_priority.png',
                          ROOT / 'tests/fixtures/mind_dock_duplicates.png',
                          ROOT / 'tests/fixtures/fleet_names_vanguard.png']
    images = [Image.open(p).convert('RGB') for p in paths]
    settings = al.OcrSettings('onnx', 'cpu', False, 'standard')
    debug_dir = output / 'ocr_debug'
    debug_dir.mkdir()
    for i in range(100):
        Image.new('L', (19, 148), 255).save(debug_dir / f'seed-{i}.png')
    result = {'head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
              'source_sha256': {rel: hashlib.sha256((args.baseline or ROOT).joinpath(rel).read_bytes()).hexdigest() for rel in SOURCES},
              'catalog_sha256': hashlib.sha256((ROOT / 'assets/ship/ship_data.json').read_bytes()).hexdigest(),
              'python': platform.python_version(), 'platform': platform.platform(), 'ocr': asdict(settings),
              'module_import_seconds': import_seconds,
              'debug_off_ablation': args.debug_off,
              'samples': [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in paths],
              'scroll_samples': [{'path': str(p), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                                 for p in args.scroll_image],
              'timing_semantics': '包含计时不可相加；cold 是新进程首次调用；warm 是预热后同图；scroll 为合成 CPU 配准',
              'real_device': {'end_to_end': None, 'screenshot': None, 'drag': None, 'stabilization': None,
                              'reason': '离线基准仅使用固定截图与合成位移；不替代实机端到端验收'}}
    cold = Measure()
    with ExitStack() as stack:
        instrument(stack, cold, al, rec, debug_dir, args.debug_off)
        result['cold_output'] = recognize(images[0], al, rec, cold, settings, paths[0].name)
    result['cold'] = cold.result()
    # 不同样本可能首次触发数字兜底或整屏检测，须逐张预热后再计入 warm。
    with ExitStack() as stack:
        warmup = Measure()
        instrument(stack, warmup, al, rec, debug_dir, args.debug_off)
        for image, path in zip(images, paths):
            recognize(image, al, rec, warmup, settings, path.name)
    runs, expected = [], None
    for run in range(args.repeat):
        measure = Measure()
        with ExitStack() as stack:
            instrument(stack, measure, al, rec, debug_dir, args.debug_off)
            start = perf_counter()
            outputs = [recognize(img, al, rec, measure, settings, path.name) for img, path in zip(images, paths)]
            measure.seconds['sample_set_wall'] = perf_counter() - start
            scroll = measure.wrap('synthetic_scroll_wall', scroll_benchmark)(rec, measure)
            recorded = measure.wrap('recorded_scroll_wall', recorded_scroll)(rec, args.scroll_image)
        if expected is not None and outputs != expected:
            raise AssertionError('同样输入的识别、可靠性、编队、合并或费用输出发生变化')
        expected = outputs
        runs.append(measure.result())
        print(f'warm {run + 1}/{args.repeat}: {measure.seconds["recognition_wall"]:.4f}s, '
              f'queue={measure.calls["ocr_queue_wall"]}, inference={measure.calls["model_inference"]}', flush=True)
    result.update(warm_runs=runs, outputs=expected, scroll=scroll, recorded_scroll=recorded)
    result['median_seconds'] = {key: statistics.median(r['seconds_inclusive'].get(key, 0) for r in runs)
                                for key in runs[0]['seconds_inclusive']}
    result['recognition_range_seconds'] = [min(r['seconds_inclusive']['recognition_wall'] for r in runs),
                                            max(r['seconds_inclusive']['recognition_wall'] for r in runs)]
    result['output_sha256'] = hashlib.sha256(json.dumps(expected, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    result['ocr_runtime'] = [{'name': name, 'settings': asdict(config),
                              'providers': model.text_rec.session.session.get_providers()}
                             for (name, config), model in al._model_cache.items()]
    if args.compare:
        baseline = json.loads(args.compare.read_text(encoding='utf-8'))
        keys = ('catalog_sha256', 'ocr', 'ocr_runtime', 'samples', 'scroll_samples', 'outputs', 'scroll', 'recorded_scroll')
        result['comparison'] = {key: result[key] == baseline[key] for key in keys}
        if not all(result['comparison'].values()):
            raise AssertionError(f'基线输入或逐卡/合并/费用/位移结果存在差异：{result["comparison"]}')
    (output / 'report.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'median': result['median_seconds'], 'range': result['recognition_range_seconds'],
                      'counts': runs[0]['counts'], 'output_sha256': result['output_sha256']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
