# https://huggingface.co/OpenMOSS-Team/MOSS-Image-Base/blob/main/pipeline_moss_image.py (encode_prompt)
# https://github.com/Comfy-Org/ComfyUI/blob/master/comfy/text_encoders/moss_image.py

import torch

from backend import memory_management

#: The chat template with enable_thinking=True: the user turn (trimmed), then an
#: open think block. The conditioning is EVERY token's last hidden state,
#: template included, as the reference passes it.
TEMPLATE = "<|im_start|>user\n{}<|im_end|>\n<|im_start|>assistant\n<think>\n"

#: The reference truncates at 4096 tokens.
MAX_TOKENS = 4096


class MOSSTextProcessingEngine:
    """Prompt -> Qwen3.5 last hidden states, one unpadded prompt at a time.

    No emphasis, no chunking: MOSS reads the prompt as plain text (its JSON
    layouts are full of brackets), and the reference encodes each prompt by
    itself because padding would leak into the linear-attention state."""

    def __init__(self, text_encoder, tokenizer):
        self.text_encoder = text_encoder
        self.tokenizer = tokenizer

    def tokenize(self, texts):
        return [ids[:MAX_TOKENS] for ids in self.tokenizer([TEMPLATE.format(text.strip()) for text in texts])["input_ids"]]

    def encode(self, ids):
        device = memory_management.text_encoder_device()
        tokens = torch.tensor([ids], device=device, dtype=torch.long)
        embeds = self.text_encoder.get_input_embeddings()(tokens)
        hidden, _ = self.text_encoder(None, embeds=embeds)
        return hidden[0]  # [tokens, 4096], as the other engines return one prompt

    def __call__(self, texts):
        zs = []
        cache = {}
        for text in texts:
            if text not in cache:
                cache[text] = self.encode(self.tokenize([text])[0])
            zs.append(cache[text])
        return zs
