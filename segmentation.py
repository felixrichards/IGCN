#!/usr/bin/python -u
import os
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader
from data import get_prague_train_data, get_prague_test_data, get_prague_splits
from igcn.seg.models import UNetIGCN, UNetIGCNCmplx, RCF
from igcn.seg.metrics import RCFMetric
from igcn.seg.loss import RCFLoss
from igcn.seg.attention.models import DAFMS, DAFStackSmall
from igcn.seg.attention.metrics import DAFMetric
from igcn.seg.attention.loss import DAFLoss
from quicktorch.modules.attention.models import DAFMSPlain
from quicktorch.utils import train, evaluate, get_splits
from quicktorch.metrics import (
    DenoisingTracker, SegmentationTracker, MultiClassSegmentationTracker
)
from quicktorch.writers import LabScribeWriter
from quicktorch.data import bsd
from cirrus.data import SynthCirrusDataset
from utils import ExperimentParser, calculate_error, RotateAndCrop
from unet import UNetCmplx, UNet
import albumentations
from isbi import get_isbi_train_data, get_isbi_test_data


SIZES = {
    'stars_bad_columns_ccd': 300,
    'stars_bad_columns_ccd_saturation': 1200,
    'isbi': 30,
    'bsd': 300,
    'prague': 90,
}


def write_results(**kwargs):
    sorted_keys = sorted([key for key in kwargs.keys()])
    result = '\n' + '\t'.join([str(kwargs[key]) for key in sorted_keys])
    f = open("cirrus_results.txt", "a+")
    f.write(result)
    f.close()


def get_metrics_criterion(variant, denoise=False, binary=True):
    if 'DAF' in variant:
        MetricsClass = DAFMetric()
        criterion = DAFLoss()
    if 'RCF' in variant:
        MetricsClass = RCFMetric()
        criterion = RCFLoss()
    else:
        if denoise:
            MetricsClass = DenoisingTracker()
            criterion = nn.MSELoss()
        else:
            if binary:
                criterion = nn.BCEWithLogitsLoss()
                MetricsClass = SegmentationTracker()
            else:
                criterion = nn.CrossEntropyLoss()
                MetricsClass = MultiClassSegmentationTracker()

    return MetricsClass, criterion


def get_synth_cirrus_train_data(args, training_args, data_dir, split):
    size = 256
    train_loader = DataLoader(
        SynthCirrusDataset(
            os.path.join(data_dir, 'train'),
            indices=split[0],
            denoise=args.denoise,
            transform=albumentations.Compose([
                albumentations.RandomCrop(256, 256),
                albumentations.Resize(size, size),
                albumentations.Flip(),
                albumentations.RandomRotate90(),
                albumentations.PadIfNeeded(size + args.padding, size + args.padding, border_mode=4)
            ]),
            padding=args.padding,
        ),
        batch_size=training_args.batch_size, shuffle=True)
    val_loader = DataLoader(
        SynthCirrusDataset(
            os.path.join(data_dir, 'train'),
            indices=split[1],
            denoise=args.denoise,
            transform=albumentations.Compose([
                albumentations.RandomCrop(256, 256),
                albumentations.Resize(size, size),
                albumentations.Flip(),
                albumentations.RandomRotate90(),
                albumentations.PadIfNeeded(size + args.padding, size + args.padding, border_mode=4)
            ]),
            padding=args.padding,
        ),
        batch_size=training_args.batch_size, shuffle=True)
    return train_loader, val_loader


def get_synth_cirrus_test_data(args, training_args, data_dir, **kwargs):
    size = 256
    test_loader = DataLoader(
        SynthCirrusDataset(
            os.path.join(data_dir, 'test'),
            denoise=args.denoise,
            transform=albumentations.Compose([
                albumentations.RandomCrop(256, 256),
                albumentations.Resize(size, size),
                albumentations.Flip(),
                albumentations.PadIfNeeded(size + args.padding, size + args.padding, border_mode=4)
            ]),
            padding=args.padding,
        ),
        batch_size=training_args.batch_size, shuffle=True)
    return test_loader


def get_bsd_train_data(args, training_args, data_dir, split):
    size = 272
    train_loader, val_loader = bsd(
        transform=albumentations.Compose([
            albumentations.Resize(400, 400),
            RotateAndCrop(size, size, limit=180),
            albumentations.Flip(),
            albumentations.PadIfNeeded(size + args.padding, size + args.padding, border_mode=4)
        ]),
        split=split,
        batch_size=training_args.batch_size,
        dir=data_dir,
        padding=args.padding,
    )
    return train_loader, val_loader


def get_bsd_test_data(args, training_args, data_dir, **kwargs):
    size = 272
    test_loader = bsd(
        transform=albumentations.Compose([
            albumentations.PadIfNeeded(size + args.padding, size + args.padding, border_mode=4)
        ]),
        batch_size=training_args.batch_size,
        dir=data_dir,
        test=True,
        padding=args.padding,
    )
    return test_loader


def create_model(save_dir, variant="SFC", n_channels=1, n_classes=2,
                 bands=['g'], downscale=1, model_path='', pretrain=True,
                 dataset='cirrus', padding=0, **params):
    models = {
        'SFC': UNetIGCNCmplx,
        'SFCReal': UNetIGCN,
        'Standard': UNetCmplx,
        'StandardReal': UNet,
        'DAF': DAFStackSmall,
        'DAFMS': DAFMS,
        'DAFMSPlain': DAFMSPlain,
        'RCF': RCF,
    }
    scalings = {
        'T': True,
        'P': 'parallel'
    }
    scale = False
    if variant[-1] in scalings.keys():
        scale = scalings[variant[-1]]
        variant = variant[:-1]
    attention = True if "DAF" in variant else False
    model_name = f'{variant}-{dataset}'
    model_fn = models[variant]
    if dataset == 'cirrus':
        model_name += (
            f'_bands={bands}'
            f'-pre={bool(model_path)}'
            f'-downscale={downscale}'
        )
    params["cmplx"] = True
    model_name += (
        f'-kernel_size={params["kernel_size"]}'
        f'-no_g={params["no_g"]}'
        f'-base_channels={params["base_channels"]}'
        f'-gp={params["final_gp"]}'
        f'-relu={params["relu_type"]}'
        f'-cmplx={params["cmplx"]}'
    )
    model = model_fn(
        n_channels=n_channels,
        n_classes=n_classes,
        save_dir=save_dir,
        name=model_name,
        no_g=params["no_g"],
        kernel_size=params["kernel_size"],
        base_channels=params["base_channels"],
        pooling=params["pooling"],
        scale=scale,
        gp=params["final_gp"],
        relu_type=params["relu_type"],
        upsample_mode=params["upsample_mode"],
        dropout=params["dropout"],
        pad_to_remove=padding,
        cmplx=params["cmplx"],
        l_init=params["l_init"],
        sigma_init=params["sigma_init"],
        single_param=params["single_param"],
        morlet=params["morlet"],
    )
    if model_path:
        load(model, model_path, True, pretrain=pretrain, att=attention)
    if torch.cuda.device_count() > 1:
        model = nn.DataParallel(model)
    return model


def load(model, save_path, legacy=False, pretrain=True, att=False):
    checkpoint = torch.load(save_path)

    if pretrain:
        if att:
            scal_key = 'down1.0.weight'
        else:
            # scal_key = 'inc.conv1.weight'
            scal_key = 'inc.conv1.0.weight'
        weight = checkpoint['model_state_dict'][scal_key]
        out_c = 1
        if model.preprocess is not None:
            out_c = model.preprocess.n_scaling
        checkpoint['model_state_dict'][scal_key] = weight.repeat(1, 1, 2 * out_c, 1, 1)
        # checkpoint['model_state_dict'][scal_key] = weight.repeat(1, 2 * out_c, 1, 1)
        if att:
            checkpoint['model_state_dict'][scal_key].squeeze_(0)
    if legacy:
        for key in checkpoint['model_state_dict']:
            if key.split('.')[-1] == 'gabor_filters':# and key.split('.')[1] != 'conv1':
                checkpoint['model_state_dict'][key] = checkpoint['model_state_dict'][key].unsqueeze(1)

    model.load_state_dict(checkpoint['model_state_dict'], strict=False)


def run_segmentation_split(net_args, training_args, writer=None, device='cuda:0', split=None, split_no=0, args=None, test_idxs=None):
    if net_args.dataset == 'synth':
        n_channels = 1
        n_classes = 1
        get_train = get_synth_cirrus_train_data
        get_test = get_synth_cirrus_test_data
    elif net_args.dataset == 'isbi':
        n_channels = 1
        n_classes = 1
        get_train = get_isbi_train_data
        get_test = get_isbi_test_data
    elif net_args.dataset == 'bsd':
        n_channels = 3
        n_classes = 1
        get_train = get_bsd_train_data
        get_test = get_bsd_test_data
    elif net_args.dataset == 'prague':
        n_channels = 3
        n_classes = 10
        get_train = get_prague_train_data
        get_test = get_prague_test_data
    else:
        raise NotImplementedError()

    train_loader, val_loader = get_train(
        args,
        training_args,
        data_dir=args.dir,
        split=split
    )
    test_loader = get_test(
        args,
        training_args,
        data_dir=args.dir,
        test_idxs=test_idxs
    )

    save_dir = 'D:/seg_models/seg/' + net_args.dataset# + f'/paramtest/{net_args.l_init}-{net_args.sigma_init}-{str(net_args.single_param)}'
    os.makedirs(save_dir, exist_ok=True)
    model = create_model(
        save_dir,
        variant=args.model_variant,
        n_channels=n_channels,
        n_classes=n_classes,
        model_path=args.model_path,
        padding=args.padding,
        **vars(net_args),
    ).to(device)

    metrics_class, criterion = get_metrics_criterion(args.model_variant, args.denoise, n_classes == 1)
    metrics_class.Writer = writer         # Delete this line if metric logging not desired

    total_params = sum(p.numel()
                       for p in model.parameters() if p.requires_grad)
    total_params = total_params/1000000
    print("Total # parameter: " + str(total_params) + "M")

    optimizer = optim.Adam(model.parameters(),
                           lr=training_args.lr,
                           weight_decay=training_args.weight_decay)
    scheduler = optim.lr_scheduler.ExponentialLR(optimizer, training_args.lr_decay)
    m = train(
        model,
        [train_loader, val_loader],
        save_best=True,
        epochs=training_args.epochs,
        opt=optimizer,
        device=device,
        sch=scheduler,
        metrics=metrics_class,
        criterion=criterion,
        val_epochs=5
    )

    print('Evaluating')
    if not args.only_val:
        temp_metrics = evaluate(model, test_loader, metrics=metrics_class, device=device)
        m['PSNR'] = temp_metrics['PSNR']
        if not args.denoise:
            m['IoU'] = temp_metrics['IoU']
    del(model)
    torch.cuda.empty_cache()
    stats = {
        'total_params': total_params
    }
    if 'epoch' not in m.keys():
        m['epoch'] = training_args.epochs
    return m, stats


def run_seg_exp(net_args, training_args, device='0', exp_name=None, args=None, **kwargs):
    N = SIZES[os.path.split(args.dir)[-1]]

    metrics = []
    save_paths = []
    test_idxs = None
    if training_args.nsplits == 1:
        splits = [[None, None]]
    else:
        if net_args.dataset == 'synth':
            splits = get_splits(N // 3 * 2, max(6, training_args.nsplits))  # Divide into 6 or more blocks
        elif net_args.dataset == 'isbi':
            splits = get_splits(N, N // args.val_size)
        if net_args.dataset == 'bsd':
            splits = get_splits(N, max(3, training_args.nsplits))  # Divide into 3 or more blocks
        if net_args.dataset == 'prague':
            splits, test_idxs = get_prague_splits(N, max(3, training_args.nsplits))
            # splits = get_splits(N, max(3, training_args.nsplits))  # Divide into 3 or more blocks

    if exp_name is None:
        exp_name = (
            f'{args.model_variant}-{net_args.dataset}'
            f'-base_channels={net_args.base_channels}'
            f'-no_g={net_args.no_g}'
            f'-pool={net_args.pooling}'
            f'-gp={net_args.final_gp}'
            f'-relu={net_args.relu_type}'
        )
    exp_worksheet_name = net_args.dataset
    if net_args.dataset == 'synth':
        exp_worksheet_name = exp_worksheet_name.capitalize()
        if args.denoise:
            exp_worksheet_name += 'Den'
        else:
            exp_worksheet_name += 'Seg'
    elif net_args.dataset == 'prague':
        exp_worksheet_name = exp_worksheet_name.capitalize()
    else:
        exp_worksheet_name = exp_worksheet_name.upper()
    writer = LabScribeWriter(
        'Results',
        exp_name=exp_name,
        exp_worksheet_name=exp_worksheet_name,
        metrics_worksheet_name=f'{exp_worksheet_name}Metrics',
        nsplits=training_args.nsplits
    )
    writer.begin_experiment(vars(net_args))
    for split_no, split in zip(range(training_args.nsplits), splits):
        print('Beginning split #{}/{}'.format(split_no + 1, training_args.nsplits))
        m, stats = run_segmentation_split(
            net_args,
            training_args,
            writer=writer,
            device=device,
            split=split,
            split_no=split_no,
            args=args,
            test_idxs=test_idxs,
        )
        save_paths.append(m.pop('save_path'))
        metrics.append(m)
        writer.upload_split({k: m[k] for k in ('IoU', 'PSNR', 'Dice')})

    mean_m = {key: sum(mi[key] for mi in metrics) / training_args.nsplits for key in m.keys()}
    best_iou = max([mi['IoU'] for mi in metrics])
    best_split = [mi['IoU'] for mi in metrics].index(best_iou) + 1
    best_psnr = metrics[[mi['IoU'] for mi in metrics].index(best_iou)]['PSNR']
    mean_m['epoch'] = metrics[best_split-1]['epoch']
    eval_m = metrics[best_split-1]
    error_m = {'PSNR': 0}

    if training_args.nsplits > 1:
        error_m = {key: calculate_error([mi[key] for mi in metrics])
                   for key in m.keys()}
    writer.upload_best_split(
        {
            'test_iou': eval_m['IoU'],
            'mean_iou': mean_m['IoU'],
            'best_split': best_split,
        },
        best_split
    )

    write_results(
        **{
            'params': stats['total_params'],
            'standard': args.model_variant,
            'psnr': mean_m['PSNR'],
            'psnr_error': error_m['PSNR'],
            'best_psnr': best_psnr,
            'best_iou': best_iou,
            'best_split': best_split,
            'dataset': net_args.dataset,
            'denoise': args.denoise,
        },
        # **vars(training_args)
    )

    return best_iou, mean_m['IoU']


def main():
    parser = ExperimentParser(description='Runs a segmentation model')
    parser.add_argument('--dir',
                        default='../data/stars_bad_columns_ccd', type=str,
                        help='Path to data directory. (default: %(default)s)')
    parser.add_argument('--model_variant',
                        default="SFC", type=str,
                        choices=['SFC', 'Standard', 'DAF', 'DAFMS', 'DAFMSPlain', 'RCF', 'SFCReal', 'StandardReal'],
                        help='Path to data directory. (default: %(default)s)')
    parser.add_argument('--denoise',
                        default=False, action='store_true',
                        help='Attempts to denoise the image')
    parser.add_argument('--only_val',
                        default=False, action='store_true',
                        help='Prevents evaluation on test dataset.')
    parser.add_argument('--val_size',
                        default=1, type=int,
                        help='Number of images used for validation dataset (only ISBI).')
    parser.add_argument('--padding',
                        default=32, type=int,
                        help='Number of images used for validation dataset (only ISBI).')
    parser.add_argument('--model_path',
                        default='', type=str,
                        help='Path to model, enabling pretraining/evaluation. (default: %(default)s)')

    net_args, training_args = parser.parse_group_args()
    args = parser.parse_args()

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    run_seg_exp(net_args, training_args, device=device, args=args)


if __name__ == '__main__':
    main()
