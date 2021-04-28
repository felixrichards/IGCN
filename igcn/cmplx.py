import numpy as np
import torch
import torch.nn.functional as F
import torch.nn.init as init
from igcn.utils import _compress_shape, _recover_shape


def cmplx(real, imag):
    """Stacks real and imaginary component into single tensor.

    This functions more as a readability aid than a helper method.
    """
    return torch.stack([real, imag], dim=0)


def new_cmplx(real):
    """Creates a trivial complex tensor.
    """
    return cmplx(real, torch.zeros_like(real))


def magnitude(x, eps=1e-8, sq=False, **kwargs):
    """Computes magnitude of given complex tensor.

    Must return nonzero as grad(0)=inf
    """
    x = x.pow(2).sum(dim=0)
    if sq:
        return x
    return torch.sqrt(torch.clamp(x, min=eps))


def phase(x, eps=1e-8, **kwargs):
    """Computes phase of given complex tensor.

    Must return nonzero as grad(0)=inf
    """
    return torch.atan(x[1] / torch.clamp(x[0], min=eps))


def concatenate(x, **kwargs):
    """Concatenates complex tensor into real tensor
    """
    return torch.cat([x[0], x[1]], dim=1)


def conv_cmplx(x, w, transpose=False, **kwargs):
    """Computes complex convolution.
    """
    conv = F.conv2d
    if transpose:
        conv = F.conv_transpose2d
        w = w.transpose(1, 2)

    x = cmplx(
        conv(x[0], w[0], **kwargs) - conv(x[1], w[1], **kwargs),
        conv(x[0], w[1], **kwargs) + conv(x[1], w[0], **kwargs)
    )

    return x


def linear_cmplx(x, w, b=None, transpose=False, **kwargs):
    """Computes complex linear transformation
    """
    linear = F.linear
    if transpose:
        pass

    x = cmplx(
        linear(x[0, ...], w[0, ...]) - linear(x[1, ...], w[1, ...]),
        linear(x[0, ...], w[1, ...]) + linear(x[1, ...], w[0, ...])
    )

    if b is not None:
        x = x - b

    return x


def cmplx_mult(*args):
    """Computes complex multiplication
    """
    def mult(x, y):
        return torch.stack([
            x[0] * y[0] - x[1] * y[1],
            x[0] * y[1] + x[1] * y[0]
        ])
    if len(args) == 2:
        return mult(args[0], args[1])
    prod = args[0]
    for item in args[1:]:
        prod = mult(prod, item)
    return prod


def relu_cmplx_z(x, inplace=False, eps=1e-12, **kwargs):
    """Computes complex relu.
    """
    x = torch.where(x[0] > 0, x, torch.zeros_like(x))
    x = torch.where(x[1] > 0, x, torch.zeros_like(x))
    return x


def relu_cmplx_mod(x, b=1e-8, inplace=False, **kwargs):
    """Computes complex relu.
    """
    r = magnitude(x, sq=False)
    if r.dim() < b.dim():
        b = b.flatten(0)
    elif r.dim() > b.dim():
        b = b.unsqueeze(-1)
    return F.relu(r + b) * x / r


def relu_cmplx(x, inplace=False, **kwargs):
    """Computes complex relu.
    """
    return cmplx(F.relu(x[0]), F.relu(x[1]))


def bnorm_cmplx_old(x, eps=1e-8):
    """Computes complex simple batch normalisation.
    """
    x, xs = _compress_shape(x)

    means = torch.mean(x, (1, 3, 4), keepdim=True)
    x = x - means

    stds = torch.std(magnitude(x, eps=eps, sq=False), (0, 2, 3), keepdim=True)
    x = x / torch.clamp(stds.unsqueeze(0), min=eps)

    x = _recover_shape(x, xs)

    return x


def pool_cmplx(x, kernel_size, operator='max', **kwargs):
    """Computes complex pooling.
    """
    x, xs = _compress_shape(x)

    pool = F.max_pool2d
    if operator == 'avg' or operator == 'average':
        pool = F.avg_pool2d

    if operator == 'mag':
        x = max_mag_pool(x, kernel_size, **kwargs)
    else:
        x = cmplx(
            pool(x[0], kernel_size, **kwargs),
            pool(x[1], kernel_size, **kwargs)
        )

    x = _recover_shape(x, xs)

    return x


def max_mag_pool(x, kernel_size, **kwargs):
    """Computes max magnitude pooling on complex tensors.
    """
    r = magnitude(x)
    _, idxs = F.max_pool2d(r, kernel_size, return_indices=True, **kwargs)
    cmplx_idxs = cmplx(idxs, idxs)
    max_by_mags = x.flatten(start_dim=3).gather(dim=3, index=cmplx_idxs.flatten(start_dim=3))
    return max_by_mags.view_as(cmplx_idxs)


def max_mag_gabor_pool(x, **kwargs):
    """Computes max magnitude pooling over gabor axis.
    """
    r = magnitude(x)
    _, idxs = torch.max(r, dim=2, keepdim=True)
    idxs = idxs.unsqueeze(0).repeat(2, 1, 1, 1, 1, 1)
    return x.gather(dim=3, index=idxs).squeeze(3), idxs


def max_summed_mag_gabor_pool(x, **kwargs):
    """Computes max summed magnitude pooling over gabor axis.
    """
    r_summed = magnitude(x).sum(dim=(-1, -2), keepdim=True)
    _, idxs = torch.max(r_summed, dim=2, keepdim=True)
    idxs = idxs.unsqueeze(0).repeat(2, 1, 1, 1, x.size(-2), x.size(-1))
    return x.gather(dim=3, index=idxs).squeeze(3), idxs


def init_weights(w, mode='he', polar=False):
    """Initialises conv. weights according to C. Trabelsi, Deep Complex Networks
    """
    fan_in, fan_out = init._calculate_fan_in_and_fan_out(w[0])
    if mode == 'he':
        sigma = 1 / fan_in
    if mode == 'glorot':
        sigma = 1 / (fan_in + fan_out)

    mag = w[0].new_tensor(np.random.rayleigh(scale=sigma, size=w[0].size()))
    phase = w[0].new_tensor(np.random.uniform(low=-np.pi, high=np.pi, size=w[0].size()))

    with torch.no_grad():
        if polar:
            w.data = cmplx(mag, phase)
        else:
            w.data = cmplx(mag * torch.cos(phase), mag * torch.sin(phase))


def exp_cmplx(x):
    """Complex exponential
    """
    return torch.exp(x[0]) * cmplx(torch.cos(x[1]), torch.sin(x[1]))


def softmax_cmplx(x, dim=None):
    """Implementation of softmax on complex space using complex exponential
    """
    return F.softmax(magnitude(x, sq=True), dim=dim)


def resample_cmplx(x, size, mode='bilinear'):
    """Upsamples complex tensor by upsampling real/imag parts separately
    """
    x, xs = _compress_shape(x)

    x = cmplx(
        F.interpolate(x[0], size=size, mode=mode, align_corners=True),
        F.interpolate(x[1], size=size, mode=mode, align_corners=True)
    )

    x = _recover_shape(x, xs)

    return x
