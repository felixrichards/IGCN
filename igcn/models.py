import torch
import torch.nn as nn
from quicktorch.models import Model
from igcn import IGConv
from igcn.cmplx_modules import IGConvCmplx, ReLUCmplx, BatchNormCmplx, MaxPoolCmplx, AvgPoolCmplx
from igcn.cmplx import new_cmplx


class DoubleIGConv(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size, pooling='max',
                 no_g=4, prev_gabor_pooling=None, gabor_pooling=None, first=False):
        super().__init__()
        padding = kernel_size // 2
        first_div = 2 if first else 1
        max_g_div = no_g if gabor_pooling is not None else 1
        prev_max_g_div = no_g if gabor_pooling is not None else 1
        if 'max' in pooling:
            Pool = MaxPoolCmplx
        elif pooling == 'avg':
            Pool = AvgPoolCmplx
        self.double_conv = nn.Sequential(
            IGConv(
                in_channels // prev_max_g_div,
                out_channels // first_div,
                kernel_size,
                pooling=Pool,
                padding=padding,
                no_g=no_g,
                gabor_pooling=None
            ),
            IGConv(
                out_channels // first_div,
                out_channels // max_g_div,
                kernel_size,
                pooling=Pool,
                padding=padding,
                no_g=no_g,
                gabor_pooling=gabor_pooling,
                pool_kernel=2,
                pool_stride=2
            ),
            ReLUCmplx(inplace=True),
        )

    def forward(self, x):
        return self.double_conv(x)


class DoubleIGConvCmplx(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size,
                 no_g=4, prev_gabor_pooling=None, gabor_pooling=None,
                 pooling='maxmag', weight_init=None, all_gp=False,
                 first=False, last=True):
        super().__init__()
        padding = kernel_size // 2 - 1
        all_gp_div = no_g if all_gp else 1
        max_g_div = no_g if gabor_pooling is not None else 1
        prev_max_g_div = no_g if prev_gabor_pooling is not None else 1
        first_div = 2 if first else 1
        all_gp = gabor_pooling if all_gp else None
        if 'max' in pooling:
            Pool = MaxPoolCmplx
        elif pooling == 'avg':
            Pool = AvgPoolCmplx
        self.double_conv = nn.Sequential(
            IGConvCmplx(
                in_channels // prev_max_g_div,
                out_channels // first_div // all_gp_div,
                kernel_size,
                padding=padding,
                no_g=no_g,
                gabor_pooling=all_gp,
                weight_init=weight_init
            ),
            IGConvCmplx(
                out_channels // first_div // all_gp_div,
                out_channels // max_g_div,
                kernel_size,
                padding=padding + int(last),
                no_g=no_g,
                gabor_pooling=gabor_pooling,
                weight_init=weight_init
            ),
            Pool(kernel_size=2, stride=2),
            BatchNormCmplx(),
            ReLUCmplx(inplace=True),
        )

    def forward(self, x):
        return self.double_conv(x)


class SingleIGConvCmplx(nn.Module):
    def __init__(self, in_channels, out_channels, kernel_size,
                 no_g=4, prev_gabor_pooling=None, gabor_pooling=None,
                 pooling='maxmag', weight_init=None,
                 last=True, **kwargs):
        super().__init__()
        print(f'out_channels={out_channels}')
        padding = kernel_size // 2 - 1
        max_g_div = no_g if gabor_pooling is not None else 1
        prev_max_g_div = no_g if prev_gabor_pooling is not None else 1
        if 'max' in pooling:
            Pool = MaxPoolCmplx
        elif pooling == 'avg':
            Pool = AvgPoolCmplx
        self.double_conv = nn.Sequential(
            IGConvCmplx(
                in_channels // prev_max_g_div,
                out_channels // max_g_div,
                kernel_size,
                padding=padding,
                no_g=no_g,
                gabor_pooling=gabor_pooling,
                weight_init=weight_init
            ),
            Pool(kernel_size=2, stride=2),
            BatchNormCmplx(),
            ReLUCmplx(inplace=True),
        )

    def forward(self, x):
        return self.double_conv(x)


class LinearBlock(nn.Module):
    def __init__(self, fcn, dropout):
        super().__init__()
        self.block = nn.Sequential(
            nn.Linear(fcn, fcn),
            nn.ReLU(inplace=True),
            nn.Dropout(p=dropout)
        )

    def forward(self, x):
        return self.block(x)


class IGCN(Model):
    """Model factory for IGCN.

    Args:
        n_classes (int, optional): Number of classes to estimate.
        n_channels (int, optional): Number of input channels.
        base_channels (int, optional): Number of feature channels in first layer of network.
        no_g (int, optional): The number of desired Gabor filters.
        kernel_size (int or tuple, optional): Size of kernel.
        inter_gp (str, optional): Type of pooling to apply across Gabor
            axis for intermediate layers.
            Choices are [None, 'max', 'avg']. Defaults to None.
        final_gp (str, optional): Type of pooling to apply across Gabor
            axis for the final layer.
            Choices are [None, 'max', 'avg']. Defaults to None.
        cmplx (bool, optional): Whether to use a complex architecture.
        pooling (str, optional): Type of pooling.
        dropout (float, optional): Probability of dropout layer(s).
        dset (str, optional): Type of dataset.
        single (bool, optional): Whether to use a single gconv layer between each pooling layer.
        all_gp (bool, optional): Whether to apply Gabor pooling on all layers.
        nfc (int, optional): Number of fully connected layers before classification.
        weight_init (str, optional): Type of weight initialisation.
    """
    def __init__(self, n_classes=10, n_channels=1, base_channels=16, no_g=4,
                 kernel_size=3, inter_gp=None, final_gp=None, cmplx=False,
                 pooling='max', dropout=0.3, dset='mnist', single=False,
                 all_gp=False, nfc=2, weight_init=None, **kwargs):
        super().__init__(**kwargs)
        if cmplx:
            ConvBlock = DoubleIGConvCmplx
            if single:
                ConvBlock = SingleIGConvCmplx
        else:
            ConvBlock = DoubleIGConv
        self.conv1 = ConvBlock(
            n_channels,
            base_channels * 2,
            kernel_size,
            no_g=no_g,
            gabor_pooling=inter_gp,
            pooling=pooling,
            first=True,
            weight_init=weight_init,
            all_gp=all_gp
        )
        self.conv2 = ConvBlock(
            base_channels * 2,
            base_channels * 3,
            kernel_size,
            no_g=no_g,
            prev_gabor_pooling=inter_gp,
            gabor_pooling=inter_gp,
            pooling=pooling,
            weight_init=weight_init,
            all_gp=all_gp
        )
        self.conv3 = ConvBlock(
            base_channels * 3,
            base_channels * 4,
            kernel_size,
            no_g=no_g,
            prev_gabor_pooling=inter_gp,
            gabor_pooling=final_gp,
            pooling=pooling,
            last=True,
            weight_init=weight_init,
            all_gp=all_gp
        )
        self.fcn = (2 if cmplx else 1) * 4 * base_channels // (no_g if final_gp else 1) * (4 if n_channels == 3 else 1)
        linear_blocks = []
        for _ in range(nfc):
            linear_blocks.append(LinearBlock(self.fcn, dropout))
        self.classifier = nn.Sequential(
            *linear_blocks,
            nn.Linear(self.fcn, 10)
        )
        self.cmplx = cmplx

    def forward(self, x):
        if self.cmplx:
            x = new_cmplx(x)
        x = self.conv1(x)
        x = self.conv2(x)
        x = self.conv3(x)
        if self.cmplx:
            x = torch.cat([x[0], x[1]], dim=1)
        x = x.flatten(1)
        x = self.classifier(x)
        return x
