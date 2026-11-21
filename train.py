"""Single-node, single-NVIDIA-GPU ModelArts entry point (also supports CPU checks)."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import traceback
import uuid

import yaml


def obs_copy(source, target, directory=True):
    try:
        import moxing as mox
    except ImportError as exc:
        raise RuntimeError('obs:// paths require the ModelArts moxing SDK; alternatively use platform-mapped local paths') from exc
    if directory:
        mox.file.copy_parallel(str(source), str(target))
    else:
        mox.file.copy(str(source), str(target))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data_url', '--data-url', default=os.getenv('data_url') or os.getenv('DATA_URL'))
    parser.add_argument('--train_url', '--train-url', default=os.getenv('train_url') or os.getenv('TRAIN_URL'))
    parser.add_argument('--model', default=str(Path(__file__).parent / 'weights/yolo11n.pt'))
    parser.add_argument('--config', default=str(Path(__file__).parent / 'configs/train.yaml'))
    parser.add_argument('--work-dir', default=str(Path(tempfile.gettempdir()) / 'roadsense'))
    parser.add_argument('--device', default='0', choices=['0', 'cpu'])
    parser.add_argument('--epochs', type=int)
    parser.add_argument('--batch', type=int)
    parser.add_argument('--imgsz', type=int)
    parser.add_argument('--workers', type=int)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--init_method')
    parser.add_argument('--rank', type=int, default=0)
    parser.add_argument('--world_size', type=int, default=1)
    args = parser.parse_args()
    if args.world_size != 1 or args.rank != 0 or int(os.getenv('WORLD_SIZE', '1')) != 1:
        parser.error('This entry supports one instance and one GPU only')
    if not args.data_url or not args.train_url:
        parser.error('Set --data_url and --train_url (or corresponding environment variables)')
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    remote_output = args.train_url.startswith('obs://')
    work = Path(args.work_dir).resolve() / run_id
    work.mkdir(parents=True)
    output = work / 'output' if remote_output else Path(args.train_url).resolve() / run_id
    output.mkdir(parents=True)
    os.environ['YOLO_CONFIG_DIR'] = str(work / 'ultralytics')
    os.environ['MPLCONFIGDIR'] = str(work / 'matplotlib')
    os.environ.setdefault('YOLO_OFFLINE', 'true')
    status = {'run_id': run_id, 'status': 'starting'}
    failure = None
    try:
        if args.data_url.startswith('obs://'):
            data = work / 'data'
            obs_copy(args.data_url, data)
        else:
            data = Path(args.data_url).resolve()
        if not (data / 'READY').is_file():
            raise ValueError(f'Dataset not prepared: missing {data}/READY. Run python -m training.prepare first.')
        report = json.loads((data / 'report.json').read_text(encoding='utf-8'))
        if report['classes'] != ['D00', 'D10', 'D20', 'D40'] or report.get('missing_classes'):
            raise ValueError('Invalid class mapping or missing classes')
        for split in ['train', 'val']:
            images = list((data / 'images' / split).glob('*.jpg'))
            if not images or any(not (data / 'labels' / split / (p.stem + '.txt')).is_file() for p in images):
                raise ValueError(f'Images/labels incomplete: {split}')
        runtime_yaml = output / 'dataset.runtime.yaml'
        runtime_yaml.write_text(yaml.safe_dump({'path': data.as_posix(), 'train': 'images/train', 'val': 'images/val', 'names': dict(enumerate(report['classes']))}, sort_keys=False), encoding='utf-8')
        config = yaml.safe_load(Path(args.config).read_text(encoding='utf-8'))
        for key in ['epochs', 'batch', 'imgsz', 'workers']:
            if getattr(args, key) is not None:
                config[key] = getattr(args, key)
        model = args.model
        if model.startswith('obs://'):
            target = work / 'initial.pt'
            obs_copy(model, target, directory=False)
            model = str(target)
        elif Path(model).suffix == '.pt':
            if not Path(model).is_file():
                raise FileNotFoundError(f'Pretrained weights missing: {model}; provide a local weight file or OBS input')
        else:
            raise ValueError('Use a pretrained .pt weight file')
        import torch
        import torchvision
        import ultralytics
        from ultralytics import YOLO
        from ultralytics import settings
        settings.update({'sync': False})
        if args.device != 'cpu':
            if not torch.cuda.is_available():
                raise RuntimeError('CUDA unavailable. Select a GPU image with compatible PyTorch; Ascend NPU is not supported by this entry.')
            tensor = torch.ones(2, 2, device='cuda:0')
            assert (tensor @ tensor).sum().item() == 8
        check_device = 'cuda:0' if args.device == '0' else 'cpu'
        torchvision.ops.nms(torch.tensor([[0., 0., 1., 1.]], device=check_device), torch.tensor([1.], device=check_device), 0.5)
        status.update({'python': __import__('sys').version, 'torch': torch.__version__,
                       'torchvision': torchvision.__version__, 'ultralytics': ultralytics.__version__,
                       'cuda_build': torch.version.cuda, 'device': args.device,
                       'gpu': torch.cuda.get_device_name(0) if args.device == '0' else None,
                       'config': config, 'dataset': report,
                       'initial_weights_sha256': __import__('hashlib').sha256(Path(model).read_bytes()).hexdigest() if Path(model).is_file() else None,
                       'manifest_sha256': __import__('hashlib').sha256((data / 'manifest.jsonl').read_bytes()).hexdigest()})
        (output / 'effective-config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
        print(json.dumps(status, indent=2), flush=True)
        if args.dry_run:
            status['status'] = 'dry_run_passed'
        else:
            model = YOLO(model)
            model.train(data=str(runtime_yaml), device=args.device, project=str(output), name='detect', exist_ok=False, **config)
            if not (output / 'detect/weights/best.pt').is_file():
                raise RuntimeError('Training did not produce best.pt')
            status['status'] = 'completed'
    except Exception:
        failure = traceback.format_exc()
        status['status'] = 'failed'
        (output / 'error.log').write_text(failure, encoding='utf-8')
    finally:
        (output / 'run.json').write_text(json.dumps(status, indent=2), encoding='utf-8')
        if remote_output:
            try:
                obs_copy(output, args.train_url.rstrip('/') + '/' + run_id)
            except Exception:
                if failure is None:
                    raise
                print('Output upload also failed:\n' + traceback.format_exc(), flush=True)
    if failure:
        raise RuntimeError(failure)
    print(f'Finished: {status["status"]}; output: {output}', flush=True)


if __name__ == '__main__':
    main()
