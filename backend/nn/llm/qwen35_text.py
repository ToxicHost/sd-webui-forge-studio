# https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/text_encoders/qwen35.py
# The Qwen3.5 language model, used as MOSS-Image's text encoder. Only what
# encoding a prompt needs: no KV cache, no decoding kernels, no vision tower,
# no LM or MTP head. Studio's `qwen35.py` already holds the vision half that
# Qwen3-VL borrows; this is the text half, in a module of its own because
# `llama.py` imports `qwen35.py` at its end.

from dataclasses import dataclass, field

import torch
import torch.nn as nn
import torch.nn.functional as F

from backend.memory_management import pytorch_attention_enabled
from backend.nn.llm.llama import BaseLlama, MLP, apply_rope, precompute_freqs_cis

if pytorch_attention_enabled:
    from backend.attention import attention_pytorch as attention_function
else:
    from backend.attention import attention_basic as attention_function


def _layer_types(n: int) -> list[str]:
    return ["full_attention" if (i + 1) % 4 == 0 else "linear_attention" for i in range(n)]


@dataclass
class Qwen35_9BConfig:
    vocab_size: int = 248320
    hidden_size: int = 4096
    intermediate_size: int = 12288
    num_hidden_layers: int = 32
    num_attention_heads: int = 16
    num_key_value_heads: int = 4
    head_dim: int = 256
    partial_rotary_factor: float = 0.25
    linear_num_key_heads: int = 16
    linear_num_value_heads: int = 32
    linear_key_head_dim: int = 128
    linear_value_head_dim: int = 128
    conv_kernel_size: int = 4
    rms_norm_eps: float = 1e-6
    rope_theta: float = 10000000.0
    layer_types: list = field(default_factory=lambda: _layer_types(32))
    rms_norm_add = True
    mlp_activation = "silu"


class RMSNormGated(nn.Module):
    """Qwen3.5's gated norm: a plain-weight RMSNorm (not 1 + weight) times silu(gate).
    The weight is float32 in the checkpoint; it is cast to the input on use."""

    def __init__(self, dim: int, eps: float):
        super().__init__()
        self.weight = nn.Parameter(torch.ones(dim, dtype=torch.float32))
        self.eps = eps

    def forward(self, x, gate):
        # as transformers' Qwen3_5RMSNormGated: normalise in float32, weight in the
        # input dtype, gate in float32
        normed = F.rms_norm(x.float(), (x.shape[-1],), None, self.eps).to(x.dtype)
        out = self.weight.to(device=x.device, dtype=x.dtype) * normed
        return (out.float() * F.silu(gate.float())).to(x.dtype)


def l2norm(x, dim=-1, eps=1e-6):
    """transformers' (and the fla kernels') l2norm: eps inside the root. ComfyUI
    uses `F.normalize`, which clamps the norm instead; the two differ for small
    vectors, and OpenMOSS's pipeline runs transformers'."""
    return x * torch.rsqrt((x * x).sum(dim=dim, keepdim=True) + eps)


def torch_chunk_gated_delta_rule(query, key, value, g, beta, chunk_size=64):
    """The gated delta rule over a whole prompt, chunked, in float32 (ComfyUI's
    torch fallback, without the recurrent-state output a decoder would need)."""
    initial_dtype = query.dtype
    query = l2norm(query, dim=-1)
    key = l2norm(key, dim=-1)
    query, key, value, beta, g = [x.transpose(1, 2).contiguous().to(torch.float32) for x in (query, key, value, beta, g)]

    batch_size, num_heads, sequence_length, k_head_dim = key.shape
    v_head_dim = value.shape[-1]
    pad_size = (chunk_size - sequence_length % chunk_size) % chunk_size
    query = F.pad(query, (0, 0, 0, pad_size))
    key = F.pad(key, (0, 0, 0, pad_size))
    value = F.pad(value, (0, 0, 0, pad_size))
    beta = F.pad(beta, (0, pad_size))
    g = F.pad(g, (0, pad_size))
    total_sequence_length = sequence_length + pad_size
    query = query * (1 / (query.shape[-1] ** 0.5))

    v_beta = value * beta.unsqueeze(-1)
    k_beta = key * beta.unsqueeze(-1)
    query, key, value, k_beta, v_beta = [x.reshape(x.shape[0], x.shape[1], -1, chunk_size, x.shape[-1]) for x in (query, key, value, k_beta, v_beta)]
    g = g.reshape(g.shape[0], g.shape[1], -1, chunk_size)
    mask = torch.triu(torch.ones(chunk_size, chunk_size, dtype=torch.bool, device=query.device), diagonal=0)

    g = g.cumsum(dim=-1)
    decay_mask = ((g.unsqueeze(-1) - g.unsqueeze(-2)).tril().exp().float()).tril()
    attn = -((k_beta @ key.transpose(-1, -2)) * decay_mask).masked_fill(mask, 0)
    for i in range(1, chunk_size):
        row = attn[..., i, :i].clone()
        sub = attn[..., :i, :i].clone()
        attn[..., i, :i] = row + (row.unsqueeze(-1) * sub).sum(-2)
    attn = attn + torch.eye(chunk_size, dtype=attn.dtype, device=attn.device)
    value = attn @ v_beta
    k_cumdecay = attn @ (k_beta * g.exp().unsqueeze(-1))
    state = torch.zeros(batch_size, num_heads, k_head_dim, v_head_dim).to(value)
    core_attn_out = torch.zeros_like(value)
    mask = torch.triu(torch.ones(chunk_size, chunk_size, dtype=torch.bool, device=query.device), diagonal=1)

    for i in range(0, total_sequence_length // chunk_size):
        q_i, k_i, v_i = query[:, :, i], key[:, :, i], value[:, :, i]
        attn = (q_i @ k_i.transpose(-1, -2) * decay_mask[:, :, i]).masked_fill_(mask, 0)
        v_new = v_i - k_cumdecay[:, :, i] @ state
        core_attn_out[:, :, i] = (q_i * g[:, :, i, :, None].exp()) @ state + attn @ v_new
        state = state * g[:, :, i, -1, None, None].exp() + (k_i * (g[:, :, i, -1, None] - g[:, :, i]).exp()[..., None]).transpose(-1, -2) @ v_new

    core_attn_out = core_attn_out.reshape(core_attn_out.shape[0], core_attn_out.shape[1], -1, core_attn_out.shape[-1])
    core_attn_out = core_attn_out[:, :, :sequence_length]
    return core_attn_out.transpose(1, 2).contiguous().to(initial_dtype)


class GatedDeltaNet(nn.Module):
    """Qwen3.5's linear-attention layer (three of every four layers)."""

    def __init__(self, config: Qwen35_9BConfig):
        super().__init__()

        hidden = config.hidden_size
        self.num_key_heads = config.linear_num_key_heads
        self.num_value_heads = config.linear_num_value_heads
        self.key_head_dim = config.linear_key_head_dim
        self.value_head_dim = config.linear_value_head_dim
        self.conv_kernel_size = config.conv_kernel_size

        self.key_dim = self.num_key_heads * self.key_head_dim
        self.value_dim = self.num_value_heads * self.value_head_dim
        conv_dim = self.key_dim * 2 + self.value_dim

        self.in_proj_qkv = nn.Linear(hidden, conv_dim, bias=False)
        self.in_proj_z = nn.Linear(hidden, self.value_dim, bias=False)
        self.in_proj_b = nn.Linear(hidden, self.num_value_heads, bias=False)
        self.in_proj_a = nn.Linear(hidden, self.num_value_heads, bias=False)
        self.out_proj = nn.Linear(self.value_dim, hidden, bias=False)

        # float32 in the checkpoint (A_log) or tiny (dt_bias): kept out of the
        # Forge-managed ops and cast to the input's device on use
        self.dt_bias = nn.Parameter(torch.zeros(self.num_value_heads, dtype=torch.float32))
        self.A_log = nn.Parameter(torch.zeros(self.num_value_heads, dtype=torch.float32))

        self.conv1d = nn.Conv1d(in_channels=conv_dim, out_channels=conv_dim, bias=False, kernel_size=self.conv_kernel_size, groups=conv_dim, padding=self.conv_kernel_size - 1)

        self.norm = RMSNormGated(self.value_head_dim, eps=config.rms_norm_eps)

    def forward(self, x):
        batch_size, seq_len, _ = x.shape

        mixed_qkv = self.in_proj_qkv(x).transpose(1, 2)
        z = self.in_proj_z(x)
        b = self.in_proj_b(x)
        a = self.in_proj_a(x)

        mixed_qkv = F.silu(self.conv1d(mixed_qkv)[:, :, :seq_len]).transpose(1, 2)
        query, key, value = mixed_qkv.split([self.key_dim, self.key_dim, self.value_dim], dim=-1)

        g_decay = -self.A_log.to(device=x.device, dtype=torch.float32).exp()
        dt_bias = self.dt_bias.to(device=x.device, dtype=torch.float32)
        beta = b.sigmoid()
        g = g_decay * F.softplus(a.float() + dt_bias)

        query = query.reshape(batch_size, seq_len, self.num_key_heads, self.key_head_dim)
        key = key.reshape(batch_size, seq_len, self.num_key_heads, self.key_head_dim)
        value = value.reshape(batch_size, seq_len, self.num_value_heads, self.value_head_dim)
        if self.num_value_heads != self.num_key_heads:
            rep = self.num_value_heads // self.num_key_heads
            query = query.repeat_interleave(rep, dim=2)
            key = key.repeat_interleave(rep, dim=2)

        core_attn_out = torch_chunk_gated_delta_rule(query, key, value, g=g, beta=beta)

        core_attn_out = self.norm(core_attn_out.reshape(-1, self.value_head_dim), z.reshape(-1, self.value_head_dim))
        return self.out_proj(core_attn_out.reshape(batch_size, seq_len, -1))


def apply_partial_rope(xq, xk, freqs_cis, rotary_dim):
    """RoPE on the first `rotary_dim` dims of each head; the rest pass through."""
    xq_rot, xk_rot = apply_rope(xq[..., :rotary_dim].contiguous(), xk[..., :rotary_dim].contiguous(), freqs_cis)
    return torch.cat([xq_rot, xq[..., rotary_dim:]], dim=-1), torch.cat([xk_rot, xk[..., rotary_dim:]], dim=-1)


class GatedAttention(nn.Module):
    """Qwen3.5's full-attention layer (every fourth): GQA with a sigmoid output gate."""

    def __init__(self, config: Qwen35_9BConfig):
        super().__init__()

        self.num_heads = config.num_attention_heads
        self.num_kv_heads = config.num_key_value_heads
        self.head_dim = config.head_dim
        self.inner_size = self.num_heads * self.head_dim
        self.rotary_dim = int(self.head_dim * config.partial_rotary_factor)

        # q_proj carries the query and its gate, interleaved per head
        self.q_proj = nn.Linear(config.hidden_size, self.inner_size * 2, bias=False)
        self.k_proj = nn.Linear(config.hidden_size, self.num_kv_heads * self.head_dim, bias=False)
        self.v_proj = nn.Linear(config.hidden_size, self.num_kv_heads * self.head_dim, bias=False)
        self.o_proj = nn.Linear(self.inner_size, config.hidden_size, bias=False)

        self.q_norm = nn.RMSNorm(self.head_dim, eps=config.rms_norm_eps, add=config.rms_norm_add)
        self.k_norm = nn.RMSNorm(self.head_dim, eps=config.rms_norm_eps, add=config.rms_norm_add)

    def forward(self, x, attention_mask=None, freqs_cis=None):
        batch_size, seq_length, _ = x.shape

        qg = self.q_proj(x).view(batch_size, seq_length, self.num_heads, self.head_dim * 2)
        xq, gate = qg[..., : self.head_dim], qg[..., self.head_dim :]
        gate = gate.reshape(batch_size, seq_length, -1)

        xk = self.k_proj(x)
        xv = self.v_proj(x)

        xq = self.q_norm(xq).transpose(1, 2)
        xk = self.k_norm(xk.view(batch_size, seq_length, self.num_kv_heads, self.head_dim)).transpose(1, 2)
        xv = xv.view(batch_size, seq_length, self.num_kv_heads, self.head_dim).transpose(1, 2)

        xq, xk = apply_partial_rope(xq, xk, freqs_cis, self.rotary_dim)

        xk = xk.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)
        xv = xv.repeat_interleave(self.num_heads // self.num_kv_heads, dim=1)

        output = attention_function(xq, xk, xv, self.num_heads, mask=attention_mask, skip_reshape=True)
        output = output * gate.sigmoid()
        return self.o_proj(output)


class Qwen35TransformerBlock(nn.Module):
    def __init__(self, config: Qwen35_9BConfig, index: int):
        super().__init__()
        self.layer_type = config.layer_types[index]
        if self.layer_type == "linear_attention":
            self.linear_attn = GatedDeltaNet(config)
        else:
            self.self_attn = GatedAttention(config)
        self.mlp = MLP(config)
        self.input_layernorm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps, add=config.rms_norm_add)
        self.post_attention_layernorm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps, add=config.rms_norm_add)

    def forward(self, x, attention_mask=None, freqs_cis=None):
        if self.layer_type == "linear_attention":
            h = self.linear_attn(self.input_layernorm(x))
        else:
            h = self.self_attn(self.input_layernorm(x), attention_mask=attention_mask, freqs_cis=freqs_cis)
        x = x + h
        return x + self.mlp(self.post_attention_layernorm(x))


class Qwen35Transformer(nn.Module):
    def __init__(self, config: Qwen35_9BConfig):
        super().__init__()
        self.config = config
        self.embed_tokens = nn.Embedding(config.vocab_size, config.hidden_size)
        self.layers = nn.ModuleList([Qwen35TransformerBlock(config, index=i) for i in range(config.num_hidden_layers)])
        self.norm = nn.RMSNorm(config.hidden_size, eps=config.rms_norm_eps, add=config.rms_norm_add)

    def forward(self, x, embeds=None, **kwargs):
        """Causal encoding of ONE unpadded prompt; returns (last hidden state after
        the final norm, None). Text-only, so the multimodal RoPE's three position
        axes are equal and it reduces to ordinary RoPE on the rotary dims."""
        if embeds is None:
            embeds = self.embed_tokens(x)
        x = embeds
        seq_len = x.shape[1]

        position_ids = torch.arange(seq_len, device=x.device).unsqueeze(0)
        rotary_dim = int(self.config.head_dim * self.config.partial_rotary_factor)
        freqs_cis = precompute_freqs_cis(rotary_dim, position_ids, self.config.rope_theta, device=x.device)

        mask = None
        if seq_len > 1:
            mask = torch.empty(seq_len, seq_len, dtype=x.dtype, device=x.device).fill_(torch.finfo(x.dtype).min / 4).triu_(1)

        for layer in self.layers:
            x = layer(x, attention_mask=mask, freqs_cis=freqs_cis)

        return self.norm(x), None


class Qwen35_9B(BaseLlama, nn.Module):
    """Text half of Qwen3.5-9B. The loader drops the vision tower, the LM head and
    the MTP head before loading, so their keys never reach this module."""

    def __init__(self, config_dict: dict):
        super().__init__()
        config = Qwen35_9BConfig()

        text = config_dict.get("text_config", config_dict)
        for key in ("hidden_size", "intermediate_size", "num_hidden_layers", "num_attention_heads", "num_key_value_heads", "head_dim", "vocab_size"):
            if key in text:
                assert getattr(config, key) == text[key], f"Qwen3.5-9B {key}: {text[key]} != {getattr(config, key)}"
        if "layer_types" in text:
            assert list(text["layer_types"]) == config.layer_types

        self.num_layers = config.num_hidden_layers
        self.model = Qwen35Transformer(config)
