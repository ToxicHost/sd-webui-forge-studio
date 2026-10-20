# https://huggingface.co/OpenMOSS-Team/MOSS-Image-Base/blob/main/pipeline_moss_image.py
# https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/ldm/moss_image/model.py
# MOSS-Image's 5B single-stream DiT. The arithmetic follows OpenMOSS's
# reference pipeline (MOSSImageTransformer); the key layout follows ComfyUI's,
# which is the one Comfy-Org's single-file release uses (q/k/v fused).

import torch
import torch.nn as nn
import torch.nn.functional as F

from backend.attention import attention_function

#: Zero-based block the reference skips in the negative branch of CFG
#: (`uncond_skip_layers=(22,)`, the model card's recommended default).
UNCOND_SKIP_LAYERS = frozenset({22})


def situ(hidden, gate):
    """Soft-capped gated activation."""
    return (4.0 * torch.tanh(gate / 4.0) * torch.sigmoid(gate)) * (25.0 * torch.tanh(hidden / 25.0))


def _rotate(x, rope):
    """Split-half RoPE; `rope` is [1, N, 2 * D/2] as sin then cos."""
    sin, cos = rope.chunk(2, -1)
    sin = torch.cat((sin, sin), -1).unsqueeze(-2)
    cos = torch.cat((cos, cos), -1).unsqueeze(-2)
    a, b = x.chunk(2, -1)
    return x * cos + torch.cat((-b, a), -1) * sin


def _attention(q, k, v, rope, heads):
    shape = (*q.shape[:2], heads, q.shape[-1] // heads)
    q = _rotate(q.reshape(shape), rope).transpose(1, 2)
    k = _rotate(k.reshape(shape), rope).transpose(1, 2)
    v = v.reshape(shape).transpose(1, 2)
    return attention_function(q.to(v.dtype), k.to(v.dtype), v, heads, skip_reshape=True)


class TextBlock(nn.Module):
    def __init__(self, dim, ffn_dim, heads):
        super().__init__()
        self.heads = heads
        self.norm1 = nn.RMSNorm(dim, eps=1e-6)
        self.norm2 = nn.RMSNorm(dim, eps=1e-6)
        self.q_norm = nn.RMSNorm(dim // heads, eps=1e-6)
        self.k_norm = nn.RMSNorm(dim // heads, eps=1e-6)
        self.qkv = nn.Linear(dim, 3 * dim)
        self.proj = nn.Linear(dim, dim)
        self.ffn_gate = nn.Linear(dim, ffn_dim)
        self.ffn_up = nn.Linear(dim, ffn_dim)
        self.ffn_down = nn.Linear(ffn_dim, dim)

    def forward(self, x, rope):
        q, k, v = self.qkv(self.norm1(x)).chunk(3, -1)
        q = self.q_norm(q.unflatten(-1, (self.heads, -1))).flatten(2)
        k = self.k_norm(k.unflatten(-1, (self.heads, -1))).flatten(2)
        x = x + self.proj(_attention(q, k, v, rope, self.heads))
        y = self.norm2(x)
        return x + self.ffn_down(situ(self.ffn_up(y), self.ffn_gate(y)))


class TextEmbedder(nn.Module):
    def __init__(self, text_dim, dim, ffn_dim, heads):
        super().__init__()
        self.proj = nn.Linear(text_dim, dim)
        self.blocks = nn.ModuleList([TextBlock(dim, ffn_dim, heads) for _ in range(2)])

    def forward(self, x, rope):
        x = self.proj(x)
        for block in self.blocks:
            x = block(x, rope)
        return x


class Block(nn.Module):
    def __init__(self, dim, ffn_dim, heads):
        super().__init__()
        self.heads = heads
        self.norm1 = nn.RMSNorm(dim, eps=1e-6)
        self.norm2 = nn.RMSNorm(dim, eps=1e-6)
        self.q_norm = nn.RMSNorm(dim // heads, eps=1e-6)
        self.k_norm = nn.RMSNorm(dim // heads, eps=1e-6)
        self.proj = nn.ModuleDict(
            {
                "qkv": nn.Linear(dim, 3 * dim, bias=False),
                "o": nn.Linear(dim, dim, bias=False),
                "ffn_up_gate": nn.Linear(dim, 2 * ffn_dim, bias=False),
                "ffn_down": nn.Linear(ffn_dim, dim, bias=False),
            }
        )

    def _load_from_state_dict(self, state_dict, prefix, *args, **kwargs):
        # OpenMOSS's own (diffusers) files keep q, k and v apart; fuse them
        names = [f"{prefix}proj.{n}.weight" for n in ("q", "k", "v")]
        if all(n in state_dict for n in names):
            state_dict[f"{prefix}proj.qkv.weight"] = torch.cat([state_dict.pop(n) for n in names])
        super()._load_from_state_dict(state_dict, prefix, *args, **kwargs)

    def forward(self, x, rope, modulation):
        shift_a, scale_a, gate_a, shift_f, scale_f, gate_f = modulation.unbind(1)
        y = self.norm1(x) * (1 + scale_a[:, None]) + shift_a[:, None]
        q, k, v = self.proj["qkv"](y).chunk(3, -1)
        q = self.q_norm(q.unflatten(-1, (self.heads, -1))).flatten(2)
        k = self.k_norm(k.unflatten(-1, (self.heads, -1))).flatten(2)
        x = x + self.proj["o"](_attention(q, k, v, rope, self.heads)) * gate_a[:, None]
        y = self.norm2(x) * (1 + scale_f[:, None]) + shift_f[:, None]
        hidden, gate = self.proj["ffn_up_gate"](y).chunk(2, -1)
        return x + self.proj["ffn_down"](situ(hidden, gate)) * gate_f[:, None]


def rope_table(height, width, text_length, num_bands, base, device):
    """Positions (r, f, h, w), image tokens then text tokens: the image sits after
    the text on f, its rows and columns centred. Frequencies are built in float32
    on the CPU, as training did."""
    exponent = torch.arange(num_bands, device="cpu", dtype=torch.float32) / num_bands
    bands = (1 / (base**exponent)).to(device)
    h, w = torch.meshgrid(torch.arange(height, device=device), torch.arange(width, device=device), indexing="ij")
    image = torch.stack((torch.zeros_like(h), torch.full_like(h, text_length), h, w), -1).flatten(0, 1).float()
    image[:, 2] -= (height - 1) / 2
    image[:, 3] -= (width - 1) / 2
    text = torch.zeros(text_length, 4, device=device, dtype=torch.float32)
    text[:, 1] = torch.arange(text_length, device=device)
    phase = torch.cat((image, text), 0).unsqueeze(-1) * bands
    return torch.cat((phase.sin(), phase.cos()), -2).flatten(-2).unsqueeze(0)


def untile(context):
    """One row of a batched context, back at its true length.

    MOSS takes the text length into its RoPE and evaluates every sample at its
    true length (the reference never pads), so padding would make a different
    prompt. Neo pads in two ways:

    - a cond and an uncond of different lengths are concatenated by TILING the
      shorter up to their common multiple (`ConditionCrossAttn.concat`, at
      most 4 times);
    - prompts of different lengths in one batch are stacked by repeating the
      LAST vector (`prompt_parser.stack_conds`).

    An encoder's hidden states never repeat exactly -- each position sees a
    different causal prefix -- so an exact repeat can only be padding. The
    tiling is undone first, because it is applied last."""
    length = context.shape[1]
    for factor in (4, 3, 2):
        if length % factor == 0:
            period = length // factor
            head = context[:, :period]
            if torch.equal(head.repeat(1, factor, 1), context):
                context = head
                break
    end = context.shape[1]
    while end > 1 and torch.equal(context[:, end - 1], context[:, end - 2]):
        end -= 1
    return context[:, :end]


class MOSSImageTransformer(nn.Module):
    def __init__(self, dim=3072, ffn_dim=8192, num_heads=24, num_layers=42, text_dim=4096, freq_dim=256, latent_channels=128, rope_base=10000.0, **kwargs):
        super().__init__()
        if dim % num_heads or (dim // num_heads) % 8 or freq_dim % 2:
            raise ValueError("MOSS-Image needs dim divisible by heads, head_dim divisible by 8, and an even freq_dim")
        self.dim = dim
        self.num_heads = num_heads
        self.freq_dim = freq_dim
        self.latent_channels = latent_channels
        self.rope_base = rope_base
        self.num_bands = (dim // num_heads) // 8

        self.video_embedder = nn.Linear(latent_channels, dim)
        self.text_embedder = TextEmbedder(text_dim, dim, ffn_dim, num_heads)
        self.time_embedding = nn.Sequential(nn.Linear(freq_dim, dim), nn.SiLU(), nn.Linear(dim, dim))
        self.shared_block_modulation = nn.Linear(dim, 6 * dim)
        self.layers = nn.ModuleList([Block(dim, ffn_dim, num_heads) for _ in range(num_layers)])
        self.final_norm_video = nn.RMSNorm(dim, eps=1e-6)
        self.final_linear_video = nn.Linear(dim, latent_channels, bias=False)
        self.video_head_modulation = nn.Linear(dim, 2 * dim)

    def _load_from_state_dict(self, state_dict, prefix, *args, **kwargs):
        # the RoPE frequencies are derived constants (the reference rebuilds them
        # in float32 on every load); the checkpoint's copy is not used
        state_dict.pop(f"{prefix}rope.bands", None)
        super()._load_from_state_dict(state_dict, prefix, *args, **kwargs)

    def _time(self, timesteps, batch, device, dtype):
        # training builds the sinusoid in float64 (cos then sin) and feeds float32;
        # MPS has no float64, so it is built on the CPU there
        work = torch.device("cpu") if device.type == "mps" else device
        times = torch.as_tensor(timesteps, dtype=torch.float32).to(work).reshape(-1).expand(batch)
        half = self.freq_dim // 2
        freq = 10000 ** (-torch.arange(half, device=work, dtype=torch.float64) / half)
        phase = times.double()[:, None] * freq
        embedding = torch.cat((phase.cos(), phase.sin()), -1).float().to(device)
        return self.time_embedding(embedding.to(dtype))

    def forward(self, x, timesteps, context, transformer_options={}, **kwargs):
        batch, _, height, width = x.shape
        dtype = x.dtype
        if context.shape[0] != batch:
            raise ValueError(f"MOSS-Image: {context.shape[0]} prompts for {batch} latents")

        time = self._time(timesteps, batch, x.device, dtype)
        mod = self.shared_block_modulation(time).view(batch, 6, -1)
        head = self.video_head_modulation(time).view(batch, 2, -1)

        uncond = set(transformer_options.get("uncond_indices", ()))

        outputs = []
        for index in range(batch):
            text = untile(context[index : index + 1])
            video = self.video_embedder(x[index : index + 1].flatten(2).transpose(1, 2).contiguous())
            rope = rope_table(height, width, text.shape[1], self.num_bands, self.rope_base, x.device).to(video.dtype)
            text = self.text_embedder(text.to(dtype), rope[:, height * width :])
            h = torch.cat((video, text), 1)
            skip = UNCOND_SKIP_LAYERS if index in uncond else ()
            for layer_index, layer in enumerate(self.layers):
                if layer_index not in skip:
                    h = layer(h, rope, mod[index : index + 1])
            h = self.final_norm_video(h[:, : height * width])
            h = h * (1 + head[index : index + 1, 1, None]) + head[index : index + 1, 0, None]
            h = self.final_linear_video(h)
            outputs.append(h.transpose(1, 2).reshape(1, -1, height, width))
        return torch.cat(outputs)
